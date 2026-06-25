"""AI-backed Editor Agent.

The Editor Agent reads topic trend JSON and writes a Korean Markdown
newsletter. It does not publish and must not invent facts beyond the supplied
trend and article metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


TOPIC_TRENDS_FILENAME = "topic_trends.json"
TOPIC_ARTICLES_FILENAME = "topic_articles.json"
NEWSLETTER_FILENAME = "newsletter.md"
SCHEMA_VERSION = "1.0"
DEFAULT_MODEL = "gemini-2.0-flash"
MODEL_ENV_VAR = "DTNS_EDITOR_MODEL"
GEMINI_MODEL_ENV_VAR = "GEMINI_MODEL"
FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*|\s*```\s*$", re.IGNORECASE)


class TrendPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date


class Trend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    importance: Literal["high", "medium", "low"]
    summary: str
    why_it_matters: str
    article_ids: list[str] = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)

    @field_validator("id", "title", "summary", "why_it_matters")
    @classmethod
    def require_non_empty_string(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value


class TrendsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    generated_at: datetime
    topic: str
    period: TrendPeriod | None = None
    trends: list[Trend] = Field(default_factory=list)

    @field_validator("topic")
    @classmethod
    def require_topic(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value


class AIMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    confidence: float = Field(ge=0, le=1)
    rationale: str | None = None


class ClassificationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched_rules: list[str] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0)


class TopicArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    title: str
    canonical_url: str
    published_at: datetime | None
    tags: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    ai_metadata: AIMetadata
    classification: ClassificationMetadata
    summary: str | None = None

    @field_validator("id", "source", "title", "canonical_url")
    @classmethod
    def require_non_empty_string(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value


class TopicArticlesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    generated_at: datetime
    topic: str
    articles: list[TopicArticle] = Field(default_factory=list)

    @field_validator("topic")
    @classmethod
    def require_topic(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value


def write_newsletter(
    input_path: Path | str,
    output_path: Path | str,
    *,
    articles_path: Path | str | None = None,
    model: str | None = None,
    client: Any | None = None,
) -> str:
    """Read topic trends, generate Korean Markdown, and write newsletter.md."""

    _load_dotenv()
    input_path = Path(input_path)
    output_path = Path(output_path)
    model = resolve_model(model)

    trends_file = TrendsFile.model_validate(_read_json(input_path))
    topic_articles = _load_topic_articles(articles_path, trends_file.topic)
    _validate_article_references(trends_file, topic_articles)

    if not trends_file.trends:
        markdown = _empty_newsletter(trends_file.topic)
    else:
        markdown = _request_newsletter(
            trends_file,
            topic_articles=topic_articles,
            model=model,
            client=client,
        )
    markdown = normalize_markdown(markdown)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown + "\n", encoding="utf-8")
    return markdown


def _empty_newsletter(topic: str) -> str:
    topic_names = {
        "technology": "Technology Trends",
        "backend": "Backend",
        "qa": "QA / Quality Engineering",
    }
    topic_name = topic_names.get(topic, topic)
    return (
        f"# 🗞️ 이번 주 {topic_name} 뉴스레터\n\n"
        "## 🔎 핵심 요약\n\n"
        "- 이번 실행에서 해당 토픽으로 분류된 주요 기사가 없습니다.\n\n"
        "## 💡 이번 주 인사이트\n\n"
        "수집 소스, 태그 규칙, 분류 규칙을 점검한 뒤 다음 실행에서 다시 확인합니다."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dtns.agents.editor")
    parser.add_argument(
        "--input",
        default=TOPIC_TRENDS_FILENAME,
        type=Path,
        help="Path to topic_trends.json.",
    )
    parser.add_argument(
        "--output",
        default=NEWSLETTER_FILENAME,
        type=Path,
        help="Path to write newsletter.md.",
    )
    parser.add_argument(
        "--articles",
        default=None,
        type=Path,
        help="Optional topic_articles.json path for article titles and URLs.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            f"Gemini model name. Defaults to ${MODEL_ENV_VAR}, "
            f"$GEMINI_MODEL, or {DEFAULT_MODEL}."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    write_newsletter(
        input_path=args.input,
        output_path=args.output,
        articles_path=args.articles,
        model=args.model,
    )
    return 0


def resolve_model(model: str | None = None) -> str:
    configured_model = (
        model
        or os.getenv(MODEL_ENV_VAR)
        or os.getenv(GEMINI_MODEL_ENV_VAR)
        or DEFAULT_MODEL
    )
    configured_model = configured_model.strip()
    if not configured_model:
        raise ValueError(f"{MODEL_ENV_VAR} must not be empty when set.")
    return configured_model


def normalize_markdown(markdown: str) -> str:
    markdown = markdown.strip()
    if not markdown:
        raise ValueError("Editor model returned empty Markdown.")

    markdown = FENCE_RE.sub("", markdown).strip()
    if markdown.startswith("{") or markdown.startswith("["):
        raise ValueError("Editor model returned JSON, but Markdown is required.")
    if not markdown.startswith("#"):
        markdown = f"# DTNS Newsletter\n\n{markdown}"
    return markdown


def _request_newsletter(
    trends_file: TrendsFile,
    *,
    topic_articles: list[TopicArticle],
    model: str,
    client: Any | None,
) -> str:
    if client is None:
        try:
            from google import genai
        except ImportError as error:  # pragma: no cover - dependency is project-level.
            raise RuntimeError(
                "The 'google-genai' package is required for editor LLM calls."
            ) from error
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    response = client.models.generate_content(
        model=model,
        contents=[
            _build_system_prompt(trends_file.topic),
            json.dumps(
                _build_input_payload(trends_file, topic_articles),
                ensure_ascii=False,
                indent=2,
            ),
        ],
        config={"temperature": 0.4},
    )

    content = getattr(response, "text", None)
    if not content:
        raise ValueError("Editor model returned empty Markdown.")
    return str(content)


def _build_system_prompt(topic: str) -> str:
    topic_prompt = _load_topic_prompt(topic)
    if topic_prompt:
        return f"{topic_prompt.rstrip()}\n\n{_universal_editor_rules()}"

    return _universal_editor_rules(
        "You are the DTNS Editor Agent. Write a weekly software engineering "
        "newsletter in natural Korean Markdown for any topic."
    )


def _universal_editor_rules(prefix: str | None = None) -> str:
    rules = (
        "Keep technical names in English. Summarize trends, summarize supplied "
        "articles, generate weekly insights, and explain why each trend matters. "
        "Do not fully translate articles. Do not fabricate information. If "
        "article metadata is missing, do not invent titles, URLs, dates, or "
        "claims. Return Markdown only with no JSON, no front matter, and no "
        "code fence."
    )
    if prefix is None:
        return rules
    return f"{prefix} {rules}"


def _build_input_payload(
    trends_file: TrendsFile,
    topic_articles: list[TopicArticle],
) -> dict[str, Any]:
    articles_by_id = {article.id: article for article in topic_articles}
    payload: dict[str, Any] = {
        "task": "write Korean newsletter Markdown",
        "schema_version": SCHEMA_VERSION,
        "topic": trends_file.topic,
        "period": (
            trends_file.period.model_dump(mode="json")
            if trends_file.period is not None
            else None
        ),
        "trends": [
            {
                "id": trend.id,
                "title": trend.title,
                "importance": trend.importance,
                "summary": trend.summary,
                "why_it_matters": trend.why_it_matters,
                "keywords": trend.keywords,
                "articles": [
                    _article_payload(articles_by_id[article_id])
                    for article_id in trend.article_ids
                    if article_id in articles_by_id
                ],
                "article_ids_without_metadata": [
                    article_id
                    for article_id in trend.article_ids
                    if article_id not in articles_by_id
                ],
            }
            for trend in trends_file.trends
        ],
        "requirements": [
            "Write natural Korean.",
            "Keep technical names in English.",
            "Output Markdown only.",
            "Summarize trends and explain why they matter.",
            "Summarize supplied article metadata without fully translating articles.",
            "Generate weekly insights based only on supplied trend and article data.",
            "Do not fabricate missing article titles, URLs, dates, or source details.",
            "Cite original article URLs whenever URL metadata is supplied.",
        ],
    }
    return payload


def _article_payload(article: TopicArticle) -> dict[str, Any]:
    return {
        "id": article.id,
        "source": article.source,
        "title": article.title,
        "canonical_url": article.canonical_url,
        "published_at": (
            article.published_at.isoformat()
            if article.published_at is not None
            else None
        ),
        "summary": article.summary,
        "tags": article.tags,
        "technologies": article.technologies,
        "domains": article.domains,
    }


def _load_topic_prompt(topic: str) -> str | None:
    prompt_path = (
        Path(__file__).resolve().parents[2] / "prompts" / f"editor_{topic}.md"
    )
    if not prompt_path.exists():
        return None
    return prompt_path.read_text(encoding="utf-8")


def _load_topic_articles(
    articles_path: Path | str | None,
    expected_topic: str,
) -> list[TopicArticle]:
    if articles_path is None:
        return []

    document = TopicArticlesFile.model_validate(_read_json(Path(articles_path)))
    if document.topic != expected_topic:
        raise ValueError(
            f"articles topic '{document.topic}' does not match trends topic "
            f"'{expected_topic}'"
        )
    return document.articles


def _validate_article_references(
    trends_file: TrendsFile,
    articles: list[TopicArticle],
) -> None:
    if not articles:
        return

    article_ids = {article.id for article in articles}
    for trend in trends_file.trends:
        missing_ids = sorted(set(trend.article_ids) - article_ids)
        if missing_ids:
            raise ValueError(
                f"trend '{trend.id}' references unknown article IDs: "
                f"{', '.join(missing_ids)}"
            )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}") from error
    except ValidationError:
        raise


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is project-level.
        return
    load_dotenv()


if __name__ == "__main__":
    raise SystemExit(main())
