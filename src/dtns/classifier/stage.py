"""Deterministic multi-label article classifier."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dtns.contracts.tagged_articles import (
    AIMetadata,
    SCHEMA_VERSION,
    TaggedArticle,
    TaggedArticlesDocument,
)
from dtns.contracts.content import (
    ArticleEvaluation,
    ArticleEvidence,
    ArticleType,
    MEANINGFUL_RELEASE_CHANGES,
    SourceMetadata,
)


TAGGED_ARTICLES_FILENAME = "tagged_articles.json"
TOPIC_ARTICLES_FILENAME_TEMPLATE = "{topic}_articles.json"
TOPICS = ("technology", "backend", "game_client")
CLASSIFIER_POLICY_VERSION = "2"
logger = logging.getLogger(__name__)

Topic = Literal["technology", "backend", "game_client"]

TECHNOLOGY_TERMS = {
    "agent",
    "agent evaluation",
    "agent-evaluation",
    "ai",
    "ai engineering",
    "architecture",
    "cloud",
    "database",
    "databases",
    "framework",
    "infrastructure",
    "llm",
    "ai infrastructure",
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
    "distributed-system",
    "caching",
    "concurrency",
    "consistency",
    "go",
    "java",
    "jvm",
    "kafka",
    "kotlin",
    "observability",
    "backpressure",
    "load shedding",
    "rate limiting",
    "reliability",
    "scalability",
    "opentelemetry",
    "postgresql",
    "python backend",
    "redis",
    "spring",
    "spring boot",
    "testcontainers",
}

GAME_CLIENT_TERMS = {
    "animation",
    "c#",
    "c++",
    "game client",
    "game development",
    "game engine",
    "gameplay",
    "godot",
    "graphics",
    "audio",
    "asset pipeline",
    "engine architecture",
    "gpu",
    "input",
    "memory",
    "networking",
    "physics",
    "platform optimization",
    "mobile game",
    "rendering",
    "shader",
    "tooling",
    "ui",
    "unity",
    "unreal engine",
    "ue5",
    "uefn",
}

TERM_RULES: dict[Topic, set[str]] = {
    "technology": TECHNOLOGY_TERMS,
    "backend": BACKEND_TERMS,
    "game_client": GAME_CLIENT_TERMS,
}


class ArtifactValidationError(ValueError):
    """A sanitized persisted-artifact contract validation failure."""


class ClassificationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched_rules: list[str] = Field(default_factory=list)
    score: float = Field(default=0, ge=0)
    selection_score: float = Field(default=0, ge=0)
    selected: bool = True
    rejection_reasons: list[str] = Field(default_factory=list)


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
    technical_topics: list[str] = Field(default_factory=list)
    article_type: ArticleType = ArticleType.GENERAL_NEWS
    evidence: ArticleEvidence = Field(default_factory=ArticleEvidence)
    evaluation: ArticleEvaluation = Field(default_factory=ArticleEvaluation)
    release_change_types: list[str] = Field(default_factory=list)
    source_metadata: SourceMetadata | None = None
    ai_metadata: AIMetadata
    classification: ClassificationMetadata
    summary: str | None = None

    @field_validator(
        "tags", "technologies", "domains", "technical_topics", "release_change_types"
    )
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
    try:
        tagged_document = TaggedArticlesDocument.model_validate_json(
            input_path.read_bytes()
        )
    except FileNotFoundError:
        raise FileNotFoundError(f"Input file not found: {input_path}") from None
    except ValidationError as error:
        raise _artifact_validation_error(
            input_path,
            contract_name=TaggedArticlesDocument.__name__,
            error=error,
        ) from None
    classified = classify_tagged_articles(tagged_document.articles)

    output_dir.mkdir(parents=True, exist_ok=True)
    for topic, document in classified.items():
        output_path = output_dir / TOPIC_ARTICLES_FILENAME_TEMPLATE.format(topic=topic)
        output_path.write_text(
            json.dumps(
                _topic_output_payload(document),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    logger.info(
        "classifier_metric input_count=%d technology_count=%d "
        "backend_count=%d game_client_count=%d total_assignment_count=%d",
        len(tagged_document.articles),
        len(classified["technology"].articles),
        len(classified["backend"].articles),
        len(classified["game_client"].articles),
        sum(len(document.articles) for document in classified.values()),
    )

    return classified


def _topic_output_payload(document: TopicArticlesDocument) -> dict[str, object]:
    payload = document.model_dump(mode="json", exclude_none=True)
    for article in payload["articles"]:
        article.setdefault("published_at", None)
    return payload


def classify_tagged_articles(
    articles: Iterable[TaggedArticle],
) -> dict[Topic, TopicArticlesDocument]:
    """Classify tagged articles into all configured newsletter topics."""

    now = datetime.now(UTC)
    topic_articles: dict[Topic, list[TopicArticle]] = {
        "technology": [],
        "backend": [],
        "game_client": [],
    }

    for article in articles:
        for topic in TOPICS:
            classification = classify_article_for_topic(article, topic)
            if classification.score <= 0 or not classification.selected:
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
                    technical_topics=article.technical_topics,
                    article_type=article.article_type,
                    evidence=article.evidence,
                    evaluation=article.evaluation,
                    release_change_types=article.release_change_types,
                    source_metadata=article.source_metadata,
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
    selection_score, rejection_reasons = _selection_decision(article, topic)
    return ClassificationMetadata(
        matched_rules=matched_rules,
        score=float(len(matched_rules)),
        selection_score=selection_score,
        selected=bool(matched_rules) and not rejection_reasons,
        rejection_reasons=rejection_reasons,
    )


def _article_terms(article: TaggedArticle) -> set[str]:
    values: list[str] = []
    values.extend(article.tags)
    values.extend(article.technologies)
    values.extend(article.domains)
    values.extend(article.technical_topics)
    values.append(article.source)
    values.append(article.title)
    if article.summary:
        values.append(article.summary)

    terms: set[str] = set()
    for value in values:
        lowered = value.casefold()
        terms.add(lowered)
    return terms


def _selection_decision(
    article: TaggedArticle,
    topic: Topic,
) -> tuple[float, list[str]]:
    """Apply deterministic signal-to-noise policy after topic matching."""

    if _is_legacy_enrichment(article):
        return 1.0, []

    evaluation = article.evaluation
    selection_score = float(
        evaluation.technical_depth
        + evaluation.practical_relevance
        + evaluation.source_quality
        + evaluation.ecosystem_impact
        + evaluation.implementation_detail
        + evaluation.release_significance
        + evaluation.cross_cutting_relevance
        + evaluation.decision_making_value
        + evaluation.trend_explanatory_power
    )
    reasons: list[str] = []

    if article.article_type == ArticleType.RELEASE:
        meaningful = set(article.release_change_types) & MEANINGFUL_RELEASE_CHANGES
        if not meaningful:
            reasons.append("release:not-meaningful")

    if article.article_type in {ArticleType.ANNOUNCEMENT, ArticleType.GENERAL_NEWS}:
        if (
            evaluation.technical_depth < 2
            and evaluation.practical_relevance < 2
            and evaluation.ecosystem_impact < 2
        ):
            reasons.append("content:low-technical-signal")

    if topic == "game_client" and _is_game_content_update(article):
        reasons.append("game_client:content-update-without-implementation")

    threshold = 8.0
    if article.article_type in {
        ArticleType.PRODUCTION_CASE,
        ArticleType.INCIDENT,
        ArticleType.MIGRATION,
    } and (
        article.evidence.implementation_detail
        and (
            article.evidence.has_production_problem
            or article.evidence.has_tradeoff
            or article.evidence.has_metrics
        )
    ):
        threshold = 6.0
    elif article.article_type in {
        ArticleType.TECHNICAL_SYNTHESIS,
        ArticleType.ARCHITECTURE_ESSAY,
    } and (
        article.evidence.explains_why
        and (
            article.evidence.connects_multiple_sources
            or article.evidence.provides_decision_criteria
        )
    ):
        threshold = 6.0
    elif article.article_type == ArticleType.RELEASE:
        threshold = 6.0

    if selection_score < threshold:
        reasons.append(f"selection:score-below-{int(threshold)}")
    return selection_score, reasons


def _is_game_content_update(article: TaggedArticle) -> bool:
    terms = " ".join(
        [article.title, *(article.tags or []), *(article.technical_topics or [])]
    ).casefold()
    content_terms = {
        "balance patch", "balancing", "new map", "new character", "new item",
        "skin", "event", "battle pass", "content update",
    }
    return (
        any(term in terms for term in content_terms)
        and not article.evidence.implementation_detail
    )


def _is_legacy_enrichment(article: TaggedArticle) -> bool:
    return (
        article.source_metadata is None
        and article.article_type == ArticleType.GENERAL_NEWS
        and not article.technical_topics
        and not article.release_change_types
        and article.evaluation == ArticleEvaluation()
        and article.evidence == ArticleEvidence()
    )


def classifier_policy_fingerprint() -> str:
    """Return the deterministic classification rule and contract identity."""

    policy = {
        "policy_version": CLASSIFIER_POLICY_VERSION,
        "schema_version": SCHEMA_VERSION,
        "topics": list(TOPICS),
        "term_rules": {
            topic: sorted(TERM_RULES[topic]) for topic in TOPICS
        },
        "matching": {
            "normalization": "casefold-exact-term",
            "multi_label": True,
            "score": "matched-rule-count",
        },
        "input_schema": TaggedArticlesDocument.model_json_schema(),
        "output_schema": TopicArticlesDocument.model_json_schema(),
    }
    encoded = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_validation_error(
    path: Path,
    *,
    contract_name: str,
    error: ValidationError,
) -> ArtifactValidationError:
    failures = []
    for detail in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = _format_error_location(detail["loc"])
        failures.append(f"{location} ({detail['type']})")

    joined_failures = ", ".join(failures) or "<document> (validation_error)"
    return ArtifactValidationError(
        f"Artifact validation failed: path={path}; contract={contract_name}; "
        f"fields={joined_failures}"
    )


def _format_error_location(location: tuple[int | str, ...]) -> str:
    formatted = ""
    for part in location:
        if isinstance(part, int):
            formatted += f"[{part}]"
        elif formatted:
            formatted += f".{part}"
        else:
            formatted = part
    return formatted or "<document>"


__all__ = [
    "TAGGED_ARTICLES_FILENAME",
    "TOPIC_ARTICLES_FILENAME_TEMPLATE",
    "TOPICS",
    "ArtifactValidationError",
    "ClassificationMetadata",
    "Topic",
    "TopicArticle",
    "TopicArticlesDocument",
    "classify_article_for_topic",
    "classify_articles",
    "classify_tagged_articles",
    "classifier_policy_fingerprint",
]
