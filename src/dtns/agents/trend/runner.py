"""AI-backed Trend Agent.

The Trend Agent reads one topic article file, asks an AI model to cluster related
articles into weekly trends, and writes JSON only. It does not generate
Markdown, publish, or call other pipeline stages.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


TOPIC_ARTICLES_FILENAME = "topic_articles.json"
TOPIC_TRENDS_FILENAME = "topic_trends.json"
SCHEMA_VERSION = "1.0"
DEFAULT_MODEL = "gemini-3.5-flash"
MODEL_ENV_VAR = "DTNS_TREND_MODEL"
GEMINI_MODEL_ENV_VAR = "GEMINI_MODEL"
IMPORTANCE_VALUES = ("high", "medium", "low")


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

    @field_validator("tags", "technologies", "domains")
    @classmethod
    def require_unique_strings(cls, value: list[str]) -> list[str]:
        return _require_unique_strings(value)


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

    @field_validator("article_ids", "keywords")
    @classmethod
    def require_unique_strings(cls, value: list[str]) -> list[str]:
        return _require_unique_strings(value)


class TrendPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date


class TrendsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
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

    @model_validator(mode="after")
    def require_unique_trend_ids(self) -> TrendsFile:
        trend_ids = [trend.id for trend in self.trends]
        if len(trend_ids) != len(set(trend_ids)):
            raise ValueError("trend IDs must be unique")
        return self


def discover_trends(
    topic: str,
    input_path: Path | str,
    output_path: Path | str,
    *,
    model: str | None = None,
    client: Any | None = None,
) -> TrendsFile:
    """Read topic articles, discover trends with AI, and write trend JSON."""

    _load_dotenv()
    topic = _normalize_topic(topic)
    input_path = Path(input_path)
    output_path = Path(output_path)
    model = (
        model
        or os.environ.get(MODEL_ENV_VAR)
        or os.environ.get(GEMINI_MODEL_ENV_VAR)
        or DEFAULT_MODEL
    )

    topic_articles = TopicArticlesFile.model_validate(_read_json(input_path))
    if topic_articles.topic != topic:
        raise ValueError(
            f"topic argument '{topic}' does not match input topic "
            f"'{topic_articles.topic}'"
        )

    if not topic_articles.articles:
        output = TrendsFile(
            generated_at=datetime.now(UTC),
            topic=topic,
            trends=[],
        )
    else:
        output = _request_trends(topic_articles, model=model, client=client)

    _validate_article_references(output, topic_articles.articles)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            output.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dtns.agents.trend")
    parser.add_argument("--topic", required=True, help="Topic identifier.")
    parser.add_argument(
        "--input",
        default=TOPIC_ARTICLES_FILENAME,
        type=Path,
        help="Path to the topic article JSON file.",
    )
    parser.add_argument(
        "--output",
        default=TOPIC_TRENDS_FILENAME,
        type=Path,
        help="Path to write the topic trend JSON file.",
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
    discover_trends(
        topic=args.topic,
        input_path=args.input,
        output_path=args.output,
        model=args.model,
    )
    return 0


def _request_trends(
    topic_articles: TopicArticlesFile,
    *,
    model: str,
    client: Any | None,
) -> TrendsFile:
    if client is None:
        from google import genai

        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    response = client.models.generate_content(
        model=model,
        contents=[
            _build_system_prompt(topic_articles.topic),
            json.dumps(
                _build_input_payload(topic_articles),
                ensure_ascii=False,
                indent=2,
            ),
        ],
        config={
            "temperature": 0.2,
            "response_mime_type": "application/json",
        },
    )

    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise ValueError("Trend model returned invalid JSON") from exc

    payload = _coerce_trends_payload(payload, topic_articles)
    try:
        return TrendsFile.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("Trend model returned invalid trend contract") from exc


def _coerce_trends_payload(
    payload: Any,
    topic_articles: TopicArticlesFile,
) -> dict[str, Any]:
    if isinstance(payload, dict) and {"generated_at", "topic", "trends"} <= set(
        payload
    ):
        return payload

    if isinstance(payload, dict):
        trend_items = (
            payload.get("trends")
            or payload.get("topic_trends")
            or payload.get("weekly_trends")
            or payload.get("items")
            or []
        )
        period = payload.get("period")
    elif isinstance(payload, list):
        trend_items = payload
        period = None
    else:
        trend_items = []
        period = None

    if not isinstance(trend_items, list):
        trend_items = []

    trends: list[dict[str, Any]] = []
    for index, item in enumerate(trend_items, start=1):
        if not isinstance(item, dict):
            continue
        article_ids = _coerce_article_ids(item.get("article_ids") or item.get("articles"))
        if not article_ids:
            continue

        title = str(
            item.get("title")
            or item.get("trend_title")
            or item.get("name")
            or f"Trend {index}"
        ).strip()
        summary = str(
            item.get("summary")
            or item.get("trend_summary")
            or item.get("description")
            or title
        ).strip()
        why_it_matters = str(
            item.get("why_it_matters")
            or item.get("why")
            or item.get("importance_reason")
            or item.get("rationale")
            or summary
        ).strip()
        importance = str(item.get("importance") or "medium").strip().lower()
        if importance not in IMPORTANCE_VALUES:
            importance = "medium"

        trends.append(
            {
                "id": str(item.get("id") or item.get("trend_id") or f"trend_{index}"),
                "title": title,
                "importance": importance,
                "summary": summary,
                "why_it_matters": why_it_matters,
                "article_ids": article_ids,
                "keywords": _coerce_string_list(item.get("keywords")),
            }
        )

    inferred_period = _infer_period(topic_articles.articles)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "topic": topic_articles.topic,
        "period": period
        or (inferred_period.model_dump(mode="json") if inferred_period else None),
        "trends": trends,
    }


def _coerce_article_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    article_ids: list[str] = []
    for item in value:
        if isinstance(item, dict):
            article_id = item.get("id") or item.get("article_id")
        else:
            article_id = item
        if article_id is None:
            continue
        text = str(article_id).strip()
        if text and text not in article_ids:
            article_ids.append(text)
    return article_ids


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    strings: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in strings:
            strings.append(text)
    return strings


def _build_system_prompt(topic: str) -> str:
    topic_prompt = _load_topic_prompt(topic)
    if topic_prompt:
        return topic_prompt

    return (
        "You are the DTNS Trend Agent. Discover weekly trends for the given "
        "newsletter topic by grouping similar articles. Generate concise trend "
        "titles, assign importance as high, medium, or low, and return JSON "
        "only. Do not write Markdown, publish, or draft newsletter copy. Use "
        "only article IDs from the input."
    )


def _load_topic_prompt(topic: str) -> str | None:
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / f"trend_{topic}.md"
    if not prompt_path.exists():
        return None
    return prompt_path.read_text(encoding="utf-8")


def _build_input_payload(topic_articles: TopicArticlesFile) -> dict[str, Any]:
    period = _infer_period(topic_articles.articles)
    payload: dict[str, Any] = {
        "task": "cluster articles and emit topic_trends JSON",
        "schema_version": SCHEMA_VERSION,
        "topic": topic_articles.topic,
        "articles": [
            {
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
                "classification_rules": article.classification.matched_rules,
                "classification_score": article.classification.score,
            }
            for article in topic_articles.articles
        ],
        "requirements": [
            "Group near-duplicate and related articles into shared trends.",
            "Every trend must reference one or more input article IDs.",
            "Use high, medium, or low for importance.",
            "Return structured JSON only.",
            "Do not generate Markdown.",
            "Do not publish.",
            "Do not write newsletters.",
        ],
    }
    if period is not None:
        payload["period"] = period.model_dump(mode="json")
    return payload


def _trends_response_schema(topic: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "generated_at", "topic", "period", "trends"],
        "properties": {
            "schema_version": {"type": "string", "const": SCHEMA_VERSION},
            "generated_at": {"type": "string", "format": "date-time"},
            "topic": {"type": "string", "const": topic},
            "period": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["start", "end"],
                        "properties": {
                            "start": {"type": "string", "format": "date"},
                            "end": {"type": "string", "format": "date"},
                        },
                    },
                    {"type": "null"},
                ],
            },
            "trends": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "title",
                        "importance",
                        "summary",
                        "why_it_matters",
                        "article_ids",
                        "keywords",
                    ],
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "title": {"type": "string", "minLength": 1},
                        "importance": {
                            "type": "string",
                            "enum": list(IMPORTANCE_VALUES),
                        },
                        "summary": {"type": "string", "minLength": 1},
                        "why_it_matters": {"type": "string", "minLength": 1},
                        "article_ids": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
        },
    }


def _infer_period(articles: list[TopicArticle]) -> TrendPeriod | None:
    published_dates = [
        article.published_at.date()
        for article in articles
        if article.published_at is not None
    ]
    if not published_dates:
        return None
    return TrendPeriod(start=min(published_dates), end=max(published_dates))


def _validate_article_references(
    trends_file: TrendsFile,
    articles: list[TopicArticle],
) -> None:
    article_ids = {article.id for article in articles}
    for trend in trends_file.trends:
        missing_ids = sorted(set(trend.article_ids) - article_ids)
        if missing_ids:
            raise ValueError(
                f"trend '{trend.id}' references unknown article IDs: "
                f"{', '.join(missing_ids)}"
            )


def _normalize_topic(topic: str) -> str:
    normalized = topic.strip()
    if not normalized:
        raise ValueError("topic must not be empty")
    return normalized


def _require_unique_strings(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("values must be unique")
    return values


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is project-level.
        return
    load_dotenv()
