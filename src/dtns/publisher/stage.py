"""Discord Webhook publisher stage.

The publisher reads a Markdown newsletter file and sends it to a configured
Discord Webhook. It is deterministic and does not use AI.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx


Topic = Literal["technology", "backend", "qa"]

DISCORD_CONTENT_LIMIT = 2000
DEFAULT_TIMEOUT_SECONDS = 20.0
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


def publish_newsletter(
    input_path: Path | str,
    *,
    topic: Topic | None = None,
    webhook_url: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> PublishResult:
    """Read Markdown from ``input_path`` and publish it to Discord."""

    _load_dotenv()
    input_path = Path(input_path)
    content = input_path.read_text(encoding="utf-8")
    messages = split_discord_messages(content)
    resolved_webhook_url = resolve_webhook_url(topic=topic, webhook_url=webhook_url)

    owns_client = client is None
    http_client = client or httpx.Client(timeout=timeout_seconds)
    try:
        for message in messages:
            _send_discord_message(http_client, resolved_webhook_url, message)
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
) -> PublishResult:
    """Publish ``<topic>_newsletter.md`` from ``data_dir``."""

    data_dir = Path(data_dir)
    input_path = data_dir / NEWSLETTER_FILENAME_TEMPLATE.format(topic=topic)
    return publish_newsletter(
        input_path,
        topic=topic,
        webhook_url=webhook_url,
        timeout_seconds=timeout_seconds,
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
) -> None:
    response = client.post(
        webhook_url,
        json={
            "content": content,
            "allowed_mentions": {"parse": []},
        },
    )
    if response.status_code >= 400:
        raise DiscordPublishError(
            "Discord Webhook publish failed "
            f"with HTTP {response.status_code}: {response.text}"
        )


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is project-level.
        return
    load_dotenv()
