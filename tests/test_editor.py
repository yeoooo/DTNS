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


def _draft_response(*, article_id="article-0", trend_id="trend-0"):
    return json.dumps(
        {
            "title": "이번 주 Technology 뉴스레터",
            "summary_items": ["이번 주의 핵심 기술 변화를 정리합니다."],
            "trend_sections": [
                {
                    "trend_id": trend_id,
                    "heading": "핵심 변화",
                    "overview": "개발 생태계의 주요 변화를 설명합니다.",
                    "why_it_matters": "개발자와 운영 팀의 대응이 필요합니다.",
                    "article_ids": [article_id],
                }
            ],
            "insight_items": ["관련 기술의 변화를 계속 확인해야 합니다."],
        },
        ensure_ascii=False,
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


def _write_articles(path, *, count=1):
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-06-25T00:00:00Z",
                "topic": "technology",
                "articles": [
                    {
                        "id": f"article-{index}",
                        "source": "example",
                        "title": f"Example [Article] {index}",
                        "canonical_url": f"https://example.com/article-{index}",
                        "published_at": "2026-06-24T12:00:00Z",
                        "tags": [],
                        "technologies": [],
                        "domains": [],
                        "ai_metadata": {"model": "test-model", "confidence": 1.0},
                        "classification": {"matched_rules": []},
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
    articles_path = tmp_path / "technology_articles.json"
    output_path = tmp_path / "technology_newsletter.md"
    _write_trends(input_path)
    _write_articles(articles_path)
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "fallback-model")
    client = FakeClient(
        [
            _response(_draft_response(), "MAX_TOKENS"),
            _response("not json"),
            _response(_draft_response()),
        ]
    )

    write_newsletter(
        input_path,
        output_path,
        articles_path=articles_path,
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
    articles_path = tmp_path / "technology_articles.json"
    output_path = tmp_path / "technology_newsletter.md"
    state_path = tmp_path / "editor-state"
    _write_trends(input_path)
    _write_articles(articles_path)
    first_client = FakeClient([_response(_draft_response())])
    write_newsletter(
        input_path,
        output_path,
        articles_path=articles_path,
        client=first_client,
        run_id="resume-run",
        state_path=state_path,
    )
    output_path.write_text("corrupt", encoding="utf-8")
    resume_client = FakeClient([])

    resumed = write_newsletter(
        input_path,
        output_path,
        articles_path=articles_path,
        client=resume_client,
        run_id="resume-run",
        state_path=state_path,
    )

    assert "https://example.com/article-0" in resumed
    assert resume_client.models.calls == []
    assert output_path.read_text(encoding="utf-8").strip() == resumed


def test_failed_generation_preserves_existing_newsletter(tmp_path):
    input_path = tmp_path / "technology_trends.json"
    articles_path = tmp_path / "technology_articles.json"
    output_path = tmp_path / "technology_newsletter.md"
    _write_trends(input_path)
    _write_articles(articles_path)
    output_path.write_text("existing newsletter", encoding="utf-8")
    client = FakeClient([_response("not json") for _ in range(3)])

    with pytest.raises(ValueError, match="failed content validation"):
        write_newsletter(
            input_path,
            output_path,
            articles_path=articles_path,
            client=client,
            run_id="failed-run",
        )

    assert output_path.read_text(encoding="utf-8") == "existing newsletter"


def test_editor_does_not_send_urls_and_renders_links_from_article_contract(tmp_path):
    trends_path = tmp_path / "technology_trends.json"
    articles_path = tmp_path / "technology_articles.json"
    output_path = tmp_path / "technology_newsletter.md"
    _write_trends(trends_path)
    _write_articles(articles_path)
    client = FakeClient([_response(_draft_response())])

    markdown = write_newsletter(
        trends_path,
        output_path,
        articles_path=articles_path,
        client=client,
        run_id="structured-draft-run",
    )

    request = client.models.calls[0]
    assert "https://example.com" not in json.dumps(request, default=str)
    assert "[Example \\[Article\\] 0](https://example.com/article-0)" in markdown
    assert request["config"]["response_mime_type"] == "application/json"


def test_editor_retries_misplaced_article_id_with_id_only_feedback(tmp_path):
    trends_path = tmp_path / "technology_trends.json"
    articles_path = tmp_path / "technology_articles.json"
    _write_trends(trends_path, count=2)
    _write_articles(articles_path, count=2)
    misplaced = _draft_response(article_id="article-1", trend_id="trend-0")
    client = FakeClient([_response(misplaced), _response(_draft_response())])

    write_newsletter(
        trends_path,
        tmp_path / "technology_newsletter.md",
        articles_path=articles_path,
        client=client,
        run_id="reference-retry-run",
    )

    corrective_payload = json.loads(client.models.calls[1]["contents"][1])
    assert corrective_payload["validation_feedback"] == (
        "misplaced_article_id:trend-0:article-1"
    )
    assert "https://" not in client.models.calls[1]["contents"][1]


def test_editor_does_not_store_draft_before_markdown_validation(
    tmp_path,
    monkeypatch,
):
    trends_path = tmp_path / "technology_trends.json"
    articles_path = tmp_path / "technology_articles.json"
    state_path = tmp_path / "editor-state"
    _write_trends(trends_path)
    _write_articles(articles_path)
    monkeypatch.setattr(runner, "render_newsletter", lambda *_: "invalid")

    with pytest.raises(ValueError, match="valid title heading"):
        write_newsletter(
            trends_path,
            tmp_path / "technology_newsletter.md",
            articles_path=articles_path,
            client=FakeClient([_response(_draft_response())]),
            run_id="invalid-render-run",
            state_path=state_path,
        )

    assert not (state_path / "editor_draft.json").exists()
    assert not (state_path / "candidate.md").exists()
    assert not (state_path / "checkpoint.json").exists()


def test_renderer_rejects_unknown_and_misplaced_ids():
    now = datetime(2026, 6, 25, tzinfo=UTC)
    trends = runner.TrendsFile(
        schema_version="1.0",
        generated_at=now,
        topic="technology",
        trends=[
            runner.Trend(
                id="trend-0",
                title="변화",
                importance="high",
                summary="요약",
                why_it_matters="중요성",
                article_ids=["article-0"],
            )
        ],
    )
    draft = runner.EditorDraft(
        topic="technology",
        generated_at=now,
        title="이번 주 기술",
        summary_items=["핵심 요약입니다."],
        trend_sections=[
            runner.DraftTrendSection(
                trend_id="trend-0",
                heading="변화",
                overview="변화를 설명합니다.",
                why_it_matters="중요한 변화입니다.",
                article_ids=["article-1"],
            )
        ],
        insight_items=["계속 관찰합니다."],
    )

    with pytest.raises(ValueError, match="unknown_article_id:article-1"):
        runner.render_newsletter(draft, trends, [])


def test_renderer_exact_url_check_is_separate_from_public_validation():
    markdown = VALID_MARKDOWN + "\n[링크](HTTPS://KNOWN.EXAMPLE/path)"

    assert validate_markdown(
        markdown,
        known_urls={"https://known.example/path"},
    )
    with pytest.raises(ValueError, match="non-canonical article URLs"):
        runner._validate_rendered_urls_exact(
            markdown,
            known_urls={"https://known.example/path"},
        )


def test_renderer_exact_check_rejects_unselected_input_article_url(
    tmp_path,
    monkeypatch,
):
    trends_path = tmp_path / "technology_trends.json"
    articles_path = tmp_path / "technology_articles.json"
    state_path = tmp_path / "editor-state"
    _write_trends(trends_path)
    _write_articles(articles_path, count=2)
    original_renderer = runner.render_newsletter

    def render_with_unselected_url(draft, trends, articles):
        return (
            original_renderer(draft, trends, articles)
            + "\n[선택되지 않은 글](https://example.com/article-1)"
        )

    monkeypatch.setattr(runner, "render_newsletter", render_with_unselected_url)

    with pytest.raises(ValueError, match="non-canonical article URLs"):
        write_newsletter(
            trends_path,
            tmp_path / "technology_newsletter.md",
            articles_path=articles_path,
            client=FakeClient([_response(_draft_response())]),
            run_id="unselected-url-run",
            state_path=state_path,
        )

    assert not (state_path / "editor_draft.json").exists()
    assert not (state_path / "candidate.md").exists()


def test_policy_fingerprint_changes_with_contract_documents(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_editor_contract_documents",
        lambda: {"editor_draft.md": "version-one"},
    )
    first = runner.editor_policy_fingerprint("technology", model="model")
    monkeypatch.setattr(
        runner,
        "_editor_contract_documents",
        lambda: {"editor_draft.md": "version-two"},
    )

    assert runner.editor_policy_fingerprint("technology", model="model") != first


@pytest.mark.parametrize(
    "title",
    [
        "# 🗞️ 제목",
        "🗞️ 제목",
        "# 제목",
        "제목\n부제",
        "[제목](https://example.com)",
        "<b>제목</b>",
    ],
)
def test_editor_draft_rejects_presentation_syntax_in_title(title):
    with pytest.raises(ValueError):
        runner.EditorDraft(
            topic="technology",
            generated_at=datetime(2026, 6, 25, tzinfo=UTC),
            title=title,
            summary_items=["핵심 요약입니다."],
            trend_sections=[
                runner.DraftTrendSection(
                    trend_id="trend-0",
                    heading="핵심 변화",
                    overview="변화를 설명합니다.",
                    why_it_matters="중요한 변화입니다.",
                    article_ids=["article-0"],
                )
            ],
            insight_items=["계속 관찰합니다."],
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("heading", "🧭 핵심 변화"),
        ("overview", "- 목록으로 설명합니다."),
        ("why_it_matters", "<strong>중요합니다.</strong>"),
        ("overview", "첫 문장\n## 새 구역"),
        ("overview", "---"),
        ("overview", "관련 [문서][reference]를 확인합니다."),
        ("overview", "관련 ![이미지][diagram]을 확인합니다."),
    ],
)
def test_editor_draft_rejects_presentation_syntax_in_all_prose(field, value):
    section = {
        "trend_id": "trend-0",
        "heading": "핵심 변화",
        "overview": "변화를 설명합니다.",
        "why_it_matters": "중요한 변화입니다.",
        "article_ids": ["article-0"],
        field: value,
    }

    with pytest.raises(ValueError):
        runner.DraftTrendSection(**section)


@pytest.mark.parametrize("field", ["heading", "overview"])
@pytest.mark.parametrize(
    "value",
    [
        "sched_ext",
        "snake_case",
        "한글_sched_ext 식별자",
    ],
)
def test_editor_draft_allows_underscores_inside_technical_identifiers(
    field,
    value,
):
    section = {
        "trend_id": "trend-0",
        "heading": "핵심 변화",
        "overview": "변화를 설명합니다.",
        "why_it_matters": "중요한 변화입니다.",
        "article_ids": ["article-0"],
        field: value,
    }

    assert runner.DraftTrendSection(**section)


@pytest.mark.parametrize("field", ["heading", "overview"])
@pytest.mark.parametrize(
    "value",
    [
        "_emphasis_",
        "__strong__",
        r"sched\_ext",
        r"\_emphasis\_",
        "___",
        "_ _ _",
        "“_강조_”",
    ],
)
def test_editor_draft_rejects_underscore_markdown_syntax(field, value):
    section = {
        "trend_id": "trend-0",
        "heading": "핵심 변화",
        "overview": "변화를 설명합니다.",
        "why_it_matters": "중요한 변화입니다.",
        "article_ids": ["article-0"],
        field: value,
    }

    with pytest.raises(ValueError):
        runner.DraftTrendSection(**section)


def test_renderer_owns_title_and_importance_presentation():
    now = datetime(2026, 6, 25, tzinfo=UTC)
    trends = runner.TrendsFile(
        schema_version="1.0",
        generated_at=now,
        topic="technology",
        trends=[
            runner.Trend(
                id="trend-0",
                title="입력 제목",
                importance="medium",
                summary="요약",
                why_it_matters="중요성",
                article_ids=["article-0"],
            )
        ],
    )
    article = runner.TopicArticle(
        id="article-0",
        source="example",
        title="Article",
        canonical_url="https://example.com/article-0",
        published_at=None,
        tags=[],
        technologies=[],
        domains=[],
        ai_metadata=runner.AIMetadata(model="model", confidence=1.0),
        classification=runner.ClassificationMetadata(matched_rules=[]),
    )
    draft = runner.EditorDraft(
        topic="technology",
        generated_at=now,
        title="정상적인 제목",
        summary_items=["핵심 요약입니다."],
        trend_sections=[
            runner.DraftTrendSection(
                trend_id="trend-0",
                heading="핵심 변화",
                overview="변화를 설명합니다.",
                why_it_matters="중요한 변화입니다.",
                article_ids=["article-0"],
            )
        ],
        insight_items=["계속 관찰합니다."],
    )

    markdown = runner.render_newsletter(draft, trends, [article])

    assert markdown.startswith("# 🗞️ 정상적인 제목\n")
    assert "### 1. 🧭 핵심 변화" in markdown


def test_atomic_write_fsyncs_file_and_parent_directory(tmp_path, monkeypatch):
    fsync_targets = []

    def record_fsync(descriptor):
        fsync_targets.append(stat.S_ISDIR(runner.os.fstat(descriptor).st_mode))

    monkeypatch.setattr(runner.os, "fsync", record_fsync)

    runner._atomic_write_text(tmp_path / "state" / "candidate.md", "candidate")

    assert fsync_targets == [True, False, True]
