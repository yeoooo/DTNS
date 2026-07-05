from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from dtns.classifier import classify_articles
from dtns.preprocessors import preprocess
from dtns.preprocessors.stage import ArtifactValidationError
from dtns.publisher import stage as publisher_stage
from dtns.publisher import (
    AmbiguousDiscordDeliveryError,
    DiscordPublishError,
    publish_newsletter,
    split_discord_messages,
)
from dtns.publisher.receipt import read_publish_receipt, write_publish_receipt


def test_preprocess_deduplicates_and_removes_tracking_query(tmp_path):
    input_path = tmp_path / "articles.json"
    output_path = tmp_path / "normalized_articles.json"
    now = datetime(2026, 6, 25, 0, 0, tzinfo=UTC).isoformat()
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": now,
                "articles": [
                    {
                        "source": "Example",
                        "source_type": "rss",
                        "title": " Spring Boot 3.5 Released ",
                        "url": "https://example.com/post?utm_source=x&b=2&a=1",
                        "published_at": now,
                        "collected_at": now,
                    },
                    {
                        "source": "Example",
                        "source_type": "rss",
                        "title": "Duplicate",
                        "url": "https://example.com/post?a=1&b=2",
                        "published_at": now,
                        "collected_at": now,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    output = preprocess(input_path, output_path)

    assert len(output.articles) == 1
    assert output.articles[0].canonical_url == "https://example.com/post?a=1&b=2"
    assert json.loads(output_path.read_text(encoding="utf-8"))["articles"][0]["id"]


def test_preprocess_sanitizes_artifact_validation_error(tmp_path):
    input_path = tmp_path / "articles.json"
    output_path = tmp_path / "normalized_articles.json"
    sensitive_value = "must-not-appear-in-validation-error"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-06-25T00:00:00Z",
                "articles": sensitive_value,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactValidationError) as error_info:
        preprocess(input_path, output_path)

    message = str(error_info.value)
    assert f"path={input_path}" in message
    assert "contract=RawArticlesFile" in message
    assert "fields=articles (list_type)" in message
    assert sensitive_value not in message


def test_classifier_supports_multi_label_outputs(tmp_path):
    input_path = tmp_path / "tagged_articles.json"
    now = datetime(2026, 6, 25, 0, 0, tzinfo=UTC).isoformat()
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": now,
                "articles": [
                    {
                        "id": "article_1",
                        "source": "GitHub Releases",
                        "title": "Testcontainers update",
                        "canonical_url": "https://example.com/testcontainers",
                        "published_at": now,
                        "tags": ["Testcontainers"],
                        "technologies": ["Testcontainers"],
                        "domains": ["Backend", "Quality Engineering"],
                        "ai_metadata": {
                            "model": "fake",
                            "confidence": 0.9,
                            "rationale": "fixture",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    outputs = classify_articles(input_path, tmp_path)

    assert [article.id for article in outputs["backend"].articles] == ["article_1"]
    assert [article.id for article in outputs["qa"].articles] == ["article_1"]
    assert outputs["technology"].articles == []
    assert (tmp_path / "backend_articles.json").exists()
    assert (tmp_path / "qa_articles.json").exists()


def test_split_discord_messages_preserves_content():
    content = "first paragraph\n\n" + "x" * 20 + "\n\nlast paragraph"

    chunks = split_discord_messages(content, limit=25)

    assert all(len(chunk) <= 25 for chunk in chunks)
    assert "".join(chunks) == content


def test_split_discord_messages_rejects_empty_content():
    with pytest.raises(ValueError, match="must not be empty"):
        split_discord_messages("")


def test_manual_publish_label_is_sent_only_as_delivery_prefix(monkeypatch, tmp_path):
    input_path = tmp_path / "newsletter.md"
    original = "# Newsletter\n\n" + "x" * 2100
    input_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("DTNS_PUBLISH_LABEL", "🧪 테스트 발행")
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = publish_newsletter(
            input_path,
            topic="technology",
            webhook_url="https://discord.example/webhook-test",
            client=client,
            publication_date=datetime(2026, 7, 5, tzinfo=UTC).date(),
        )

    payloads = [json.loads(request.content) for request in requests]
    prefix = "> 7월 1주차\n> 🧪 테스트 발행\n\n"
    assert payloads[0]["content"].startswith(prefix + "# Newsletter")
    assert all("🧪 테스트 발행" not in item["content"] for item in payloads[1:])
    assert all("7월 1주차" not in item["content"] for item in payloads[1:])
    assert all(len(item["content"]) <= 2000 for item in payloads)
    assert input_path.read_text(encoding="utf-8") == original
    assert result.character_count == len(prefix + original)


@pytest.mark.parametrize(
    ("day", "expected"),
    [(1, "7월 1주차"), (8, "7월 2주차"), (15, "7월 3주차"),
     (22, "7월 4주차"), (29, "7월 5주차")],
)
def test_publication_marker_uses_korean_week_of_month(day, expected):
    assert publisher_stage._publication_marker(
        datetime(2026, 7, day, tzinfo=UTC).date()
    ) == expected


def test_publish_label_changes_receipt_identity(monkeypatch, tmp_path):
    input_path = tmp_path / "newsletter.md"
    input_path.write_text("# Newsletter", encoding="utf-8")

    def handler(request):
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publish_newsletter(
            input_path,
            topic="technology",
            webhook_url="https://discord.example/webhook-test",
            client=client,
        )
        monkeypatch.setenv("DTNS_PUBLISH_LABEL", "🧪 테스트 발행")
        publish_newsletter(
            input_path,
            topic="technology",
            webhook_url="https://discord.example/webhook-test",
            client=client,
        )

    receipts = list(
        (tmp_path / ".state" / "publisher" / "technology").glob("*.json")
    )
    assert len(receipts) == 2


def test_publisher_retries_discord_rate_limit(monkeypatch, tmp_path):
    input_path = tmp_path / "newsletter.md"
    input_path.write_text("# Newsletter", encoding="utf-8")
    responses = iter(
        [
            httpx.Response(429, json={"retry_after": 0.3, "global": False}),
            httpx.Response(204),
        ]
    )
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return next(responses)

    delays: list[float] = []
    monkeypatch.setattr(publisher_stage.time, "sleep", delays.append)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = publish_newsletter(
            input_path,
            webhook_url="https://discord.example/webhook",
            client=client,
        )

    assert result.message_count == 1
    assert len(requests) == 2
    assert delays == [pytest.approx(0.35)]


def test_publisher_does_not_retry_terminal_client_error(monkeypatch, tmp_path):
    input_path = tmp_path / "newsletter.md"
    input_path.write_text("# Newsletter", encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={"message": "invalid webhook"})

    delays: list[float] = []
    monkeypatch.setattr(publisher_stage.time, "sleep", delays.append)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(publisher_stage.DiscordPublishError, match="HTTP 401"):
            publish_newsletter(
                input_path,
                webhook_url="https://discord.example/webhook",
                client=client,
            )

    assert len(requests) == 1
    assert delays == []


def test_publisher_retries_transient_server_error(monkeypatch, tmp_path):
    input_path = tmp_path / "newsletter.md"
    input_path.write_text("# Newsletter", encoding="utf-8")
    responses = iter([httpx.Response(503), httpx.Response(204)])

    def handler(request):
        return next(responses)

    delays: list[float] = []
    monkeypatch.setattr(publisher_stage.time, "sleep", delays.append)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publish_newsletter(
            input_path,
            webhook_url="https://discord.example/webhook",
            client=client,
        )

    assert delays == [1.0]


def test_publisher_skips_chunks_already_delivered(tmp_path):
    input_path = tmp_path / "newsletter.md"
    input_path.write_text("# Newsletter", encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for _ in range(2):
            publish_newsletter(
                input_path,
                topic="technology",
                webhook_url="https://discord.example/webhook-a",
                client=client,
            )

    assert len(requests) == 1
    receipt_path = next(
        (tmp_path / ".state" / "publisher" / "technology").glob("*.json")
    )
    receipt = read_publish_receipt(receipt_path)
    assert receipt is not None
    assert receipt.status == "completed"
    assert receipt.chunks[0].status == "delivered"


def test_publisher_does_not_retry_unknown_chunk(tmp_path):
    input_path = tmp_path / "newsletter.md"
    input_path.write_text("# Newsletter", encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("ambiguous delivery", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DiscordPublishError):
            publish_newsletter(
                input_path,
                topic="backend",
                webhook_url="https://discord.example/webhook",
                max_attempts=1,
                client=client,
            )

        with pytest.raises(AmbiguousDiscordDeliveryError):
            publish_newsletter(
                input_path,
                topic="backend",
                webhook_url="https://discord.example/webhook",
                max_attempts=1,
                client=client,
            )

    assert len(requests) == 1


def test_publisher_keeps_receipts_for_each_webhook(tmp_path):
    input_path = tmp_path / "newsletter.md"
    input_path.write_text("# Newsletter", encoding="utf-8")
    requested_urls: list[str] = []

    def handler(request):
        requested_urls.append(str(request.url))
        return httpx.Response(204)

    webhook_a = "https://discord.example/webhook-a"
    webhook_b = "https://discord.example/webhook-b"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for webhook_url in (webhook_a, webhook_b, webhook_a):
            publish_newsletter(
                input_path,
                topic="qa",
                webhook_url=webhook_url,
                client=client,
            )

    assert requested_urls == [webhook_a, webhook_b]
    receipt_paths = list(
        (tmp_path / ".state" / "publisher" / "qa").glob("*.json")
    )
    assert len(receipt_paths) == 2


def test_write_publish_receipt_revalidates_and_replaces_atomically(
    monkeypatch,
    tmp_path,
):
    input_path = tmp_path / "newsletter.md"
    input_path.write_text("# Newsletter", encoding="utf-8")

    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(204))
    ) as client:
        publish_newsletter(
            input_path,
            topic="technology",
            webhook_url="https://discord.example/webhook",
            client=client,
        )

    receipt_path = next(
        (tmp_path / ".state" / "publisher" / "technology").glob("*.json")
    )
    receipt = read_publish_receipt(receipt_path)
    assert receipt is not None
    receipt.chunks[0].attempts = -1
    with pytest.raises(ValidationError):
        write_publish_receipt(receipt_path, receipt)

    receipt.chunks[0].attempts = 1
    replacements: list[tuple[Path, Path]] = []
    original_replace = Path.replace

    def tracking_replace(source, target):
        replacements.append((source, Path(target)))
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", tracking_replace)
    write_publish_receipt(receipt_path, receipt)

    assert len(replacements) == 1
    temporary_path, target_path = replacements[0]
    assert temporary_path.parent == target_path.parent == receipt_path.parent
    assert temporary_path.suffix == ".tmp"
    assert target_path == receipt_path
    assert list(receipt_path.parent.glob("*.tmp")) == []


def test_publisher_rejects_corrupted_receipt(tmp_path):
    input_path = tmp_path / "newsletter.md"
    input_path.write_text("# Newsletter", encoding="utf-8")
    request_count = 0

    def handler(request):
        nonlocal request_count
        request_count += 1
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publish_newsletter(
            input_path,
            topic="qa",
            webhook_url="https://discord.example/webhook",
            client=client,
        )
        receipt_path = next(
            (tmp_path / ".state" / "publisher" / "qa").glob("*.json")
        )
        receipt_path.write_text('{"schema_version": "invalid"}', encoding="utf-8")

        with pytest.raises(ValidationError):
            publish_newsletter(
                input_path,
                topic="qa",
                webhook_url="https://discord.example/webhook",
                client=client,
            )

    assert request_count == 1
