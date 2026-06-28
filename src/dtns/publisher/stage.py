"""Discord Webhook publisher stage.

The publisher reads a Markdown newsletter file and sends it to a configured
Discord Webhook. It is deterministic and does not use AI.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from dtns.publisher.receipt import (
    PublishChunkReceipt,
    PublishReceipt,
    read_publish_receipt,
    write_publish_receipt,
)


Topic = Literal["technology", "backend", "qa"]

DISCORD_CONTENT_LIMIT = 2000
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_ATTEMPTS = 5
RATE_LIMIT_BUFFER_SECONDS = 0.05
NEWSLETTER_FILENAME_TEMPLATE = "{topic}_newsletter.md"
WEBHOOK_ENV_VARS: Mapping[Topic, str] = {
    "technology": "DISCORD_WEBHOOK_TECHNOLOGY",
    "backend": "DISCORD_WEBHOOK_BACKEND",
    "qa": "DISCORD_WEBHOOK_QA",
}


@dataclass(frozen=True)
class PublishResult:
    """Summary of a Discord publish run."""

    webhook_url: str
    input_path: Path
    message_count: int
    character_count: int


class PublisherError(RuntimeError):
    """Base error raised by the publisher stage."""


class MissingWebhookURLError(PublisherError):
    """Raised when no Discord Webhook URL is configured."""


class DiscordPublishError(PublisherError):
    """Raised when Discord rejects a webhook request."""

    def __init__(
        self,
        message: str,
        *,
        attempt_count: int,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.attempt_count = attempt_count
        self.status_code = status_code
        self.response_body = response_body


class AmbiguousDiscordDeliveryError(PublisherError):
    """Raised when a prior network failure may have delivered a chunk."""


def publish_newsletter(
    input_path: Path | str,
    *,
    topic: Topic | None = None,
    webhook_url: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    client: httpx.Client | None = None,
    receipt_root: Path | str | None = None,
    run_id: str | None = None,
) -> PublishResult:
    """Read Markdown from ``input_path`` and publish it to Discord."""

    _load_dotenv()
    if not 1 <= max_attempts <= DEFAULT_MAX_ATTEMPTS:
        raise ValueError("Discord publish max_attempts must be between 1 and 5.")
    input_path = Path(input_path)
    markdown_bytes = input_path.read_bytes()
    content = markdown_bytes.decode("utf-8")
    messages = split_discord_messages(content)
    resolved_webhook_url = resolve_webhook_url(topic=topic, webhook_url=webhook_url)

    receipt_path: Path | None = None
    receipt: PublishReceipt | None = None
    if topic is not None:
        root = Path(receipt_root) if receipt_root is not None else input_path.parent
        receipt_path, receipt = _prepare_publish_receipt(
            root,
            topic=topic,
            run_id=run_id,
            markdown_bytes=markdown_bytes,
            webhook_url=resolved_webhook_url,
            messages=messages,
        )

    owns_client = client is None
    http_client = client or httpx.Client(timeout=timeout_seconds)
    try:
        for index, message in enumerate(messages):
            chunk_receipt = receipt.chunks[index] if receipt is not None else None
            if chunk_receipt is not None and chunk_receipt.status == "delivered":
                continue
            if chunk_receipt is not None and chunk_receipt.status == "unknown":
                raise AmbiguousDiscordDeliveryError(
                    f"Discord chunk {index} has unknown delivery state; "
                    "manual reconciliation is required before retrying."
                )

            try:
                attempts = _send_discord_message(
                    http_client,
                    resolved_webhook_url,
                    message,
                    max_attempts=max_attempts,
                )
            except DiscordPublishError as error:
                if receipt is not None and receipt_path is not None:
                    chunk_receipt = receipt.chunks[index]
                    chunk_receipt.attempts += error.attempt_count
                    chunk_receipt.status = (
                        "unknown" if error.status_code is None else "failed"
                    )
                    receipt.status = "failed"
                    receipt.updated_at = datetime.now(UTC)
                    write_publish_receipt(receipt_path, receipt)
                raise

            if receipt is not None and receipt_path is not None:
                chunk_receipt = receipt.chunks[index]
                chunk_receipt.attempts += attempts
                chunk_receipt.status = "delivered"
                chunk_receipt.delivered_at = datetime.now(UTC)
                receipt.status = (
                    "completed"
                    if all(chunk.status == "delivered" for chunk in receipt.chunks)
                    else "partial"
                )
                receipt.updated_at = datetime.now(UTC)
                write_publish_receipt(receipt_path, receipt)
    finally:
        if owns_client:
            http_client.close()

    return PublishResult(
        webhook_url=resolved_webhook_url,
        input_path=input_path,
        message_count=len(messages),
        character_count=len(content),
    )


def publish_topic_newsletter(
    data_dir: Path | str,
    topic: Topic,
    *,
    webhook_url: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    run_id: str | None = None,
) -> PublishResult:
    """Publish ``<topic>_newsletter.md`` from ``data_dir``."""

    data_dir = Path(data_dir)
    input_path = data_dir / NEWSLETTER_FILENAME_TEMPLATE.format(topic=topic)
    return publish_newsletter(
        input_path,
        topic=topic,
        webhook_url=webhook_url,
        timeout_seconds=timeout_seconds,
        receipt_root=data_dir,
        run_id=run_id,
    )


def resolve_webhook_url(
    *,
    topic: Topic | None = None,
    webhook_url: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the webhook URL from an explicit value or a topic env var."""

    _load_dotenv()
    if webhook_url and webhook_url.strip():
        return webhook_url.strip()

    if topic is None:
        raise MissingWebhookURLError(
            "A webhook URL is required when no publisher topic is provided."
        )

    if env is None:
        env = os.environ
    env_var = WEBHOOK_ENV_VARS[topic]
    resolved = env.get(env_var, "").strip()
    if not resolved:
        raise MissingWebhookURLError(f"Missing Discord Webhook URL in {env_var}.")
    return resolved


def split_discord_messages(
    content: str,
    *,
    limit: int = DISCORD_CONTENT_LIMIT,
) -> list[str]:
    """Split Markdown into Discord-safe message bodies.

    The returned chunks are each at most ``limit`` characters. Joining the
    chunks recreates the original non-empty content exactly.
    """

    if limit <= 0:
        raise ValueError("Discord message limit must be positive.")

    if not content:
        raise ValueError("Newsletter content must not be empty.")

    chunks: list[str] = []
    remaining = content
    while len(remaining) > limit:
        split_at = _find_split_index(remaining, limit)
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]

    if remaining:
        chunks.append(remaining)

    return chunks


def _find_split_index(content: str, limit: int) -> int:
    window = content[:limit]
    for separator in ("\n\n", "\n", " "):
        index = window.rfind(separator)
        if index > 0:
            return index + len(separator)
    return limit


def _send_discord_message(
    client: httpx.Client,
    webhook_url: str,
    content: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    payload = {
        "content": content,
        "allowed_mentions": {"parse": []},
    }
    last_error: httpx.RequestError | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.post(webhook_url, json=payload)
        except httpx.RequestError as error:
            last_error = error
            if attempt == max_attempts:
                break
            time.sleep(_exponential_retry_delay(attempt))
            continue

        if response.status_code < 400:
            return attempt

        if response.status_code == 429:
            if attempt == max_attempts:
                raise _publish_error(response, attempt)
            time.sleep(_discord_retry_after(response, attempt))
            continue

        if 500 <= response.status_code < 600:
            if attempt == max_attempts:
                raise _publish_error(response, attempt)
            time.sleep(_exponential_retry_delay(attempt))
            continue

        raise _publish_error(response, attempt)

    raise DiscordPublishError(
        "Discord Webhook publish failed after "
        f"{max_attempts} attempts due to a network error: {last_error}",
        attempt_count=max_attempts,
    ) from last_error


def _discord_retry_after(response: httpx.Response, attempt: int) -> float:
    retry_after_candidates: list[object] = []
    try:
        payload = response.json()
        if isinstance(payload, dict):
            retry_after_candidates.append(payload.get("retry_after"))
    except ValueError:
        pass

    retry_after_candidates.append(response.headers.get("Retry-After"))
    for retry_after in retry_after_candidates:
        delay = _parse_retry_delay(retry_after)
        if delay is not None:
            return delay + RATE_LIMIT_BUFFER_SECONDS

    return _exponential_retry_delay(attempt)


def _parse_retry_delay(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(delay) or delay < 0:
        return None
    return delay


def _exponential_retry_delay(attempt: int) -> float:
    return float(2 ** (attempt - 1))


def _publish_error(response: httpx.Response, attempts: int) -> DiscordPublishError:
    return DiscordPublishError(
        "Discord Webhook publish failed "
        f"after {attempts} attempt(s) with HTTP {response.status_code}: "
        f"{response.text}",
        attempt_count=attempts,
        status_code=response.status_code,
        response_body=response.text,
    )


def _prepare_publish_receipt(
    data_dir: Path,
    *,
    topic: Topic,
    run_id: str | None,
    markdown_bytes: bytes,
    webhook_url: str,
    messages: list[str],
) -> tuple[Path, PublishReceipt]:
    newsletter_fingerprint = sha256(markdown_bytes).hexdigest()
    webhook_fingerprint = sha256(
        _normalize_webhook_url(webhook_url).encode("utf-8")
    ).hexdigest()
    path = (
        data_dir
        / ".state"
        / "publisher"
        / topic
        / f"{newsletter_fingerprint}.{webhook_fingerprint}.json"
    )
    expected_chunks = [
        PublishChunkReceipt(
            index=index,
            fingerprint=sha256(message.encode("utf-8")).hexdigest(),
            character_count=len(message),
        )
        for index, message in enumerate(messages)
    ]

    existing = read_publish_receipt(path)
    if existing is not None and _receipt_matches(
        existing,
        topic=topic,
        newsletter_fingerprint=newsletter_fingerprint,
        webhook_fingerprint=webhook_fingerprint,
        expected_chunks=expected_chunks,
    ):
        return path, existing

    now = datetime.now(UTC)
    receipt = PublishReceipt(
        run_id=run_id or uuid4().hex,
        topic=topic,
        newsletter_fingerprint=newsletter_fingerprint,
        webhook_fingerprint=webhook_fingerprint,
        status="pending",
        chunks=expected_chunks,
        updated_at=now,
    )
    write_publish_receipt(path, receipt)
    return path, receipt


def _receipt_matches(
    receipt: PublishReceipt,
    *,
    topic: Topic,
    newsletter_fingerprint: str,
    webhook_fingerprint: str,
    expected_chunks: list[PublishChunkReceipt],
) -> bool:
    if (
        receipt.topic != topic
        or receipt.newsletter_fingerprint != newsletter_fingerprint
        or receipt.webhook_fingerprint != webhook_fingerprint
        or len(receipt.chunks) != len(expected_chunks)
    ):
        return False

    return all(
        actual.index == expected.index
        and actual.fingerprint == expected.fingerprint
        and actual.character_count == expected.character_count
        for actual, expected in zip(receipt.chunks, expected_chunks, strict=True)
    )


def _normalize_webhook_url(webhook_url: str) -> str:
    parsed = urlsplit(webhook_url.strip())
    normalized_path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            parsed.query,
            "",
        )
    )


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is project-level.
        return
    load_dotenv()
