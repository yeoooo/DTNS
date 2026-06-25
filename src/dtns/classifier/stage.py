"""Deterministic multi-label article classifier."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dtns.contracts.tagged_articles import (
    AIMetadata,
    SCHEMA_VERSION,
    TaggedArticle,
    TaggedArticlesDocument,
)


TAGGED_ARTICLES_FILENAME = "tagged_articles.json"
TOPIC_ARTICLES_FILENAME_TEMPLATE = "{topic}_articles.json"
TOPICS = ("technology", "backend", "qa")

Topic = Literal["technology", "backend", "qa"]

TECHNOLOGY_TERMS = {
    "ai",
    "ai engineering",
    "architecture",
    "cloud",
    "database",
    "databases",
    "framework",
    "infrastructure",
    "language",
    "open source",
    "opentelemetry",
    "programming languages",
    "security",
}

BACKEND_TERMS = {
    "api",
    "apis",
    "backend",
    "distributed systems",
    "go",
    "java",
    "jvm",
    "kafka",
    "kotlin",
    "observability",
    "opentelemetry",
    "postgresql",
    "python backend",
    "redis",
    "spring",
    "spring boot",
    "testcontainers",
}

QA_TERMS = {
    "api testing",
    "chaos engineering",
    "ci/cd quality gates",
    "contract testing",
    "cypress",
    "junit",
    "load testing",
    "mutation testing",
    "performance testing",
    "playwright",
    "quality engineering",
    "selenium",
    "sonarqube",
    "static analysis",
    "test automation",
    "testcontainers",
}

TERM_RULES: dict[Topic, set[str]] = {
    "technology": TECHNOLOGY_TERMS,
    "backend": BACKEND_TERMS,
    "qa": QA_TERMS,
}


class ClassificationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched_rules: list[str] = Field(default_factory=list)
    score: float = Field(default=0, ge=0)


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

    @field_validator("tags", "technologies", "domains")
    @classmethod
    def require_unique_strings(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value


class TopicArticlesDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    generated_at: datetime
    topic: Topic
    articles: list[TopicArticle] = Field(default_factory=list)


def classify_articles(
    input_path: Path | str,
    output_dir: Path | str,
) -> dict[Topic, TopicArticlesDocument]:
    """Read tagged articles and write one multi-label topic file per topic."""

    input_path = Path(input_path)
    output_dir = Path(output_dir)
    tagged_document = TaggedArticlesDocument.model_validate(_read_json(input_path))
    classified = classify_tagged_articles(tagged_document.articles)

    output_dir.mkdir(parents=True, exist_ok=True)
    for topic, document in classified.items():
        output_path = output_dir / TOPIC_ARTICLES_FILENAME_TEMPLATE.format(topic=topic)
        output_path.write_text(
            json.dumps(
                document.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return classified


def classify_tagged_articles(
    articles: Iterable[TaggedArticle],
) -> dict[Topic, TopicArticlesDocument]:
    """Classify tagged articles into all configured newsletter topics."""

    now = datetime.now(UTC)
    topic_articles: dict[Topic, list[TopicArticle]] = {
        "technology": [],
        "backend": [],
        "qa": [],
    }

    for article in articles:
        for topic in TOPICS:
            classification = classify_article_for_topic(article, topic)
            if classification.score <= 0:
                continue
            topic_articles[topic].append(
                TopicArticle(
                    id=article.id,
                    source=article.source,
                    title=article.title,
                    canonical_url=article.canonical_url,
                    published_at=article.published_at,
                    summary=article.summary,
                    tags=article.tags,
                    technologies=article.technologies,
                    domains=article.domains,
                    ai_metadata=article.ai_metadata,
                    classification=classification,
                )
            )

    return {
        topic: TopicArticlesDocument(
            generated_at=now,
            topic=topic,
            articles=topic_articles[topic],
        )
        for topic in TOPICS
    }


def classify_article_for_topic(
    article: TaggedArticle,
    topic: Topic,
) -> ClassificationMetadata:
    """Return deterministic classification metadata for one article/topic pair."""

    rules = TERM_RULES[topic]
    terms = _article_terms(article)
    matched_rules = sorted(
        f"{topic}:term:{term}" for term in rules if term in terms
    )
    return ClassificationMetadata(
        matched_rules=matched_rules,
        score=float(len(matched_rules)),
    )


def _article_terms(article: TaggedArticle) -> set[str]:
    values: list[str] = []
    values.extend(article.tags)
    values.extend(article.technologies)
    values.extend(article.domains)
    values.append(article.source)
    values.append(article.title)
    if article.summary:
        values.append(article.summary)

    terms: set[str] = set()
    for value in values:
        lowered = value.casefold()
        terms.add(lowered)
    return terms


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}") from error
    except FileNotFoundError:
        raise FileNotFoundError(f"Input file not found: {path}") from None


__all__ = [
    "TAGGED_ARTICLES_FILENAME",
    "TOPIC_ARTICLES_FILENAME_TEMPLATE",
    "TOPICS",
    "ClassificationMetadata",
    "Topic",
    "TopicArticle",
    "TopicArticlesDocument",
    "classify_article_for_topic",
    "classify_articles",
    "classify_tagged_articles",
]
