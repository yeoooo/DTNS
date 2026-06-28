from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from dtns.agents.editor import runner
from dtns.agents.editor.runner import (
    normalize_markdown,
    validate_markdown,
    write_newsletter,
)


VALID_MARKDOWN = """# 🗞️ 이번 주 Technology 뉴스레터

## 🔎 핵심 요약

이번 주의 핵심 기술 변화를 간결하게 정리합니다.

## 📌 주요 트렌드

이 변화가 개발자에게 중요한 이유와 영향을 설명합니다.

## 💡 이번 주 인사이트

다음 주에도 관련 기술의 변화를 계속 확인해야 합니다.
"""


class FakeModels:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


def _response(text, finish_reason="STOP"):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=finish_reason)],
    )


def _write_trends(path, *, count=1):
    now = datetime(2026, 6, 25, 0, 0, tzinfo=UTC).isoformat()
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": now,
                "topic": "technology",
                "trends": [
                    {
                        "id": f"trend-{index}",
                        "title": "변화",
                        "importance": "high",
                        "summary": "요약",
                        "why_it_matters": "중요성",
                        "article_ids": [f"article-{index}"],
                        "keywords": [],
                    }
                    for index in range(count)
                ],
            }
        ),
        encoding="utf-8",
    )


def test_editor_writes_deterministic_empty_newsletter_without_client(tmp_path):
    input_path = tmp_path / "technology_trends.json"
    output_path = tmp_path / "technology_newsletter.md"
    now = datetime(2026, 6, 25, 0, 0, tzinfo=UTC).isoformat()
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": now,
                "topic": "technology",
                "trends": [],
            }
        ),
        encoding="utf-8",
    )

    markdown = write_newsletter(input_path, output_path)

    assert markdown.startswith("# 🗞️ 이번 주 Technology Trends 뉴스레터")
    assert "분류된 주요 기사가 없습니다" in markdown
    assert output_path.read_text(encoding="utf-8").strip() == markdown


def test_normalize_markdown_uses_discord_safe_dividers_and_bold_labels():
    markdown = normalize_markdown(
        "# 뉴스레터\n\n---\n\n#### 왜 중요한가\n\n본문\n\n#### **관련 글**"
    )

    assert "\n━━━━━━━━━━━━━━━━━━━━\n" in markdown
    assert "**왜 중요한가**" in markdown
    assert "**관련 글**" in markdown
    assert "---" not in markdown
    assert "####" not in markdown


def test_validate_markdown_parses_link_destination_and_optional_title():
    allowed = "https://example.com/releases/v1_(stable)"

    assert validate_markdown(
        VALID_MARKDOWN + f'\n[Release]({allowed} "title")',
        known_urls={allowed},
    )
    with pytest.raises(ValueError, match="unknown article URLs"):
        validate_markdown(
            VALID_MARKDOWN + '\n[Release](https://evil.example "title")',
            known_urls={allowed},
        )


@pytest.mark.parametrize(
    "link",
    [
        "[링크](HTTPS://evil.example)",
        "HTTPS://evil.example",
    ],
)
def test_validate_markdown_rejects_unknown_uppercase_scheme_url(link):
    with pytest.raises(ValueError, match="unknown article URLs"):
        validate_markdown(
            VALID_MARKDOWN + f"\n{link}",
            known_urls={"https://known.example"},
        )


def test_validate_markdown_matches_scheme_and_host_case_insensitively():
    assert validate_markdown(
        VALID_MARKDOWN + "\n[링크](HTTPS://KNOWN.EXAMPLE/CaseSensitivePath)",
        known_urls={"https://known.example/CaseSensitivePath"},
    )


@pytest.mark.parametrize("suffix", ["?", "!", ";"])
def test_validate_markdown_preserves_valid_bare_url_terminal_punctuation(suffix):
    allowed = f"https://known.example/article{suffix}"

    assert validate_markdown(
        VALID_MARKDOWN + f"\n{allowed}",
        known_urls={allowed},
    )


def test_validate_markdown_rejects_english_body_with_korean_headings():
    markdown = """# 🗞️ 이번 주 Technology 뉴스레터

## 🔎 핵심 요약

The platform changed its deployment model.

## 📌 주요 트렌드

This change affects every backend service and deployment workflow.

## 💡 이번 주 인사이트

Teams should update their operational guidance this week.
"""

    with pytest.raises(ValueError, match="written in Korean"):
        validate_markdown(markdown, known_urls=set())


def test_editor_rejects_more_than_eight_trends(tmp_path):
    input_path = tmp_path / "technology_trends.json"
    _write_trends(input_path, count=9)

    with pytest.raises(ValueError, match="Invalid TrendsFile artifact"):
        write_newsletter(input_path, tmp_path / "technology_newsletter.md")


def test_editor_validates_trends_from_original_json_bytes(tmp_path):
    input_path = tmp_path / "technology_trends.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-06-25T00:00:00Z",
                "topic": "technology",
                "period": {"start": "2026-06-18", "end": "2026-06-25"},
                "trends": [],
            }
        ),
        encoding="utf-8",
    )

    markdown = write_newsletter(
        input_path,
        tmp_path / "technology_newsletter.md",
    )

    assert "Technology Trends 뉴스레터" in markdown


def test_editor_validates_topic_articles_from_original_json_bytes(tmp_path):
    trends_path = tmp_path / "technology_trends.json"
    articles_path = tmp_path / "technology_articles.json"
    _write_trends(trends_path, count=0)
    articles_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-06-25T00:00:00Z",
                "topic": "technology",
                "articles": [
                    {
                        "id": "article-1",
                        "source": "example",
                        "title": "Example",
                        "canonical_url": "https://example.com/article",
                        "published_at": "2026-06-24T12:00:00Z",
                        "tags": [],
                        "technologies": [],
                        "domains": [],
                        "ai_metadata": {"model": "test-model", "confidence": 1.0},
                        "classification": {"matched_rules": []},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    markdown = write_newsletter(
        trends_path,
        tmp_path / "technology_newsletter.md",
        articles_path=articles_path,
    )

    assert "Technology Trends 뉴스레터" in markdown


@pytest.mark.parametrize(
    ("invalid_fields", "failed_field"),
    [
        ({"generated_at": "not-a-datetime"}, "generated_at"),
        (
            {"period": {"start": "2026-99-99", "end": "2026-06-25"}},
            "period.start",
        ),
    ],
)
def test_editor_rejects_malformed_trend_temporal_fields(
    tmp_path,
    invalid_fields,
    failed_field,
):
    input_path = tmp_path / "technology_trends.json"
    payload = {
        "schema_version": "1.0",
        "generated_at": "2026-06-25T00:00:00Z",
        "topic": "technology",
        "trends": [],
        **invalid_fields,
    }
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=failed_field):
        write_newsletter(input_path, tmp_path / "technology_newsletter.md")


def test_editor_rejects_unexpected_trends_field(tmp_path):
    input_path = tmp_path / "technology_trends.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-06-25T00:00:00Z",
                "topic": "technology",
                "trends": [],
                "unexpected": "must not be accepted",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unexpected"):
        write_newsletter(input_path, tmp_path / "technology_newsletter.md")


def test_editor_rejects_invalid_topic_literal(tmp_path):
    input_path = tmp_path / "technology_trends.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-06-25T00:00:00Z",
                "topic": "security",
                "trends": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="topic"):
        write_newsletter(input_path, tmp_path / "technology_newsletter.md")


def test_editor_checks_topic_mismatch_after_topic_articles_validation(tmp_path):
    trends_path = tmp_path / "technology_trends.json"
    articles_path = tmp_path / "backend_articles.json"
    _write_trends(trends_path, count=0)
    articles_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-06-25T00:00:00Z",
                "topic": "backend",
                "articles": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match trends topic"):
        write_newsletter(
            trends_path,
            tmp_path / "technology_newsletter.md",
            articles_path=articles_path,
        )


def test_editor_retries_truncation_then_uses_fallback(tmp_path, monkeypatch):
    input_path = tmp_path / "technology_trends.json"
    output_path = tmp_path / "technology_newsletter.md"
    _write_trends(input_path)
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "fallback-model")
    client = FakeClient(
        [
            _response(VALID_MARKDOWN, "MAX_TOKENS"),
            _response("# invalid"),
            _response(VALID_MARKDOWN),
        ]
    )

    write_newsletter(
        input_path,
        output_path,
        model="primary-model",
        client=client,
        run_id="retry-run",
    )

    assert [call["model"] for call in client.models.calls] == [
        "primary-model",
        "primary-model",
        "fallback-model",
    ]


def test_editor_resumes_matching_checkpoint_without_model_call(tmp_path):
    input_path = tmp_path / "technology_trends.json"
    output_path = tmp_path / "technology_newsletter.md"
    state_path = tmp_path / "editor-state"
    _write_trends(input_path)
    first_client = FakeClient([_response(VALID_MARKDOWN)])
    write_newsletter(
        input_path,
        output_path,
        client=first_client,
        run_id="resume-run",
        state_path=state_path,
    )
    output_path.write_text("corrupt", encoding="utf-8")
    resume_client = FakeClient([])

    resumed = write_newsletter(
        input_path,
        output_path,
        client=resume_client,
        run_id="resume-run",
        state_path=state_path,
    )

    assert resumed == VALID_MARKDOWN.strip()
    assert resume_client.models.calls == []
    assert output_path.read_text(encoding="utf-8").strip() == resumed


def test_failed_generation_preserves_existing_newsletter(tmp_path):
    input_path = tmp_path / "technology_trends.json"
    output_path = tmp_path / "technology_newsletter.md"
    _write_trends(input_path)
    output_path.write_text("existing newsletter", encoding="utf-8")
    client = FakeClient([_response("# invalid") for _ in range(3)])

    with pytest.raises(ValueError, match="failed content validation"):
        write_newsletter(
            input_path,
            output_path,
            client=client,
            run_id="failed-run",
        )

    assert output_path.read_text(encoding="utf-8") == "existing newsletter"


def test_atomic_write_fsyncs_file_and_parent_directory(tmp_path, monkeypatch):
    fsync_targets = []

    def record_fsync(descriptor):
        fsync_targets.append(stat.S_ISDIR(runner.os.fstat(descriptor).st_mode))

    monkeypatch.setattr(runner.os, "fsync", record_fsync)

    runner._atomic_write_text(tmp_path / "state" / "candidate.md", "candidate")

    assert fsync_targets == [True, False, True]
