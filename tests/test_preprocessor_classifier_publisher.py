from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from dtns.classifier import classify_articles
from dtns.preprocessors import preprocess
from dtns.publisher import split_discord_messages


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
