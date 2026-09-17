from __future__ import annotations

from datetime import UTC, datetime

from dtns.classifier.stage import classify_article_for_topic
from dtns.collectors.sources import FeedSource, _source_metadata
from dtns.agents.tagger.stage import _validate_batch_output
from dtns.agents.trend.checkpoint import TrendCandidate
from dtns.contracts.content import (
    ArticleEvaluation,
    ArticleEvidence,
    ArticleType,
    SourceMetadata,
)
from dtns.contracts.tagged_articles import AIMetadata, NormalizedArticle, TaggedArticle


def _article(**overrides) -> TaggedArticle:
    payload = {
        "id": "article-1",
        "source": "Primary Engineering Blog",
        "title": "Production architecture deep dive",
        "canonical_url": "https://example.com/article",
        "published_at": datetime(2026, 9, 1, tzinfo=UTC),
        "tags": [],
        "technologies": [],
        "domains": [],
        "technical_topics": [],
        "article_type": ArticleType.ENGINEERING_DEEP_DIVE,
        "evidence": ArticleEvidence(),
        "evaluation": ArticleEvaluation(),
        "release_change_types": [],
        "ai_metadata": AIMetadata(model="fixture", confidence=1.0),
    }
    payload.update(overrides)
    return TaggedArticle(**payload)


def test_bodycam_style_implementation_devlog_is_selected_for_game_client():
    article = _article(
        title="Stress-based procedural reload animation and spatial audio rebuild",
        technologies=["Unreal Engine"],
        technical_topics=["animation", "audio", "engine architecture"],
        article_type=ArticleType.PRODUCTION_CASE,
        evidence=ArticleEvidence(
            implementation_detail=True,
            has_metrics=True,
            has_tradeoff=True,
            has_production_problem=True,
            has_existing_limit=True,
        ),
        evaluation=ArticleEvaluation(
            technical_depth=3,
            practical_relevance=3,
            source_quality=3,
            implementation_detail=3,
            decision_making_value=2,
        ),
    )

    result = classify_article_for_topic(article, "game_client")

    assert result.selected is True
    assert result.selection_score >= 6


def test_game_content_update_without_implementation_is_rejected():
    article = _article(
        title="New map, skins, and balance patch",
        tags=["gameplay", "content update"],
        article_type=ArticleType.GENERAL_NEWS,
        evaluation=ArticleEvaluation(source_quality=3),
    )

    result = classify_article_for_topic(article, "game_client")

    assert result.selected is False
    assert "game_client:content-update-without-implementation" in (
        result.rejection_reasons
    )


def test_backend_production_case_with_architecture_tradeoff_is_selected():
    article = _article(
        title="Preventing database overload by redesigning admission control",
        technologies=["PostgreSQL"],
        technical_topics=["distributed-system", "load shedding"],
        article_type=ArticleType.PRODUCTION_CASE,
        evidence=ArticleEvidence(
            implementation_detail=True,
            has_metrics=True,
            has_tradeoff=True,
            has_production_problem=True,
            has_existing_limit=True,
        ),
        evaluation=ArticleEvaluation(
            technical_depth=3,
            practical_relevance=3,
            source_quality=3,
            implementation_detail=3,
            decision_making_value=3,
        ),
    )

    assert classify_article_for_topic(article, "backend").selected is True


def test_technical_synthesis_is_high_value_technology_context():
    article = _article(
        title="Why agent evaluation is becoming a system architecture concern",
        technologies=["LLM"],
        technical_topics=["agent-evaluation", "ai infrastructure"],
        article_type=ArticleType.TECHNICAL_SYNTHESIS,
        evidence=ArticleEvidence(
            connects_multiple_sources=True,
            explains_why=True,
            provides_decision_criteria=True,
            has_tradeoff=True,
        ),
        evaluation=ArticleEvaluation(
            technical_depth=3,
            source_quality=3,
            ecosystem_impact=3,
            cross_cutting_relevance=3,
            decision_making_value=3,
            trend_explanatory_power=3,
        ),
    )

    result = classify_article_for_topic(article, "technology")

    assert result.selected is True
    assert any("agent-evaluation" in rule for rule in result.matched_rules)


def test_meaningful_release_is_selected_but_patch_only_release_is_rejected():
    base = {
        "title": "Spring Boot release",
        "technologies": ["Spring Boot"],
        "article_type": ArticleType.RELEASE,
        "evaluation": ArticleEvaluation(
            technical_depth=2,
            practical_relevance=2,
            source_quality=3,
            release_significance=3,
        ),
    }
    meaningful = _article(
        **base,
        release_change_types=["new_runtime_capability"],
    )
    patch_only = _article(
        **base,
        release_change_types=["patch_only", "bug_fixes_only"],
    )

    assert classify_article_for_topic(meaningful, "backend").selected is True
    rejected = classify_article_for_topic(patch_only, "backend")
    assert rejected.selected is False
    assert "release:not-meaningful" in rejected.rejection_reasons


def test_source_catalog_assigns_editorial_metadata_independent_of_transport():
    source = FeedSource(
        "Cloudflare Blog",
        "https://blog.cloudflare.com/rss/",
    )

    metadata = _source_metadata(source)

    assert metadata.source_type == "engineering_blog"
    assert metadata.source_priority == "high"
    assert str(metadata.source_url) == "https://blog.cloudflare.com/rss/"


def test_tagger_runtime_derives_source_quality_from_source_priority():
    now = datetime(2026, 9, 1, tzinfo=UTC)
    article = NormalizedArticle(
        id="source-quality",
        source="Cloudflare Blog",
        title="Tracing architecture",
        canonical_url="https://example.com/tracing",
        published_at=now,
        collected_at=now,
        source_metadata=SourceMetadata(
            source_type="engineering_blog",
            source_priority="high",
            source_name="Cloudflare Blog",
            source_url="https://blog.cloudflare.com/rss/",
        ),
    )
    response = {
        "articles": [
            {
                "id": article.id,
                "tags": ["Observability"],
                "technologies": ["OpenTelemetry"],
                "domains": ["Backend"],
                "technical_topics": ["observability"],
                "article_type": "production_case",
                "evidence": {"implementation_detail": True},
                "evaluation": {"source_quality": 0},
                "release_change_types": [],
                "ai_metadata": {"confidence": 0.9},
            }
        ]
    }

    tagged = _validate_batch_output([article], response, model="fixture")[0]

    assert tagged.evaluation.source_quality == 3
    assert tagged.article_type == "production_case"


def test_trend_contract_marks_synthesis_as_interpretive_context():
    candidate = TrendCandidate(
        id="observability-as-architecture",
        title="Observability becomes an architecture concern",
        importance="high",
        summary="A shared architectural movement",
        why_it_matters="It changes application design decisions",
        article_ids=["case", "release", "perspective"],
        keywords=["OpenTelemetry"],
        article_roles=[
            {"article_id": "case", "role": "production_case"},
            {"article_id": "release", "role": "meaningful_release"},
            {"article_id": "perspective", "role": "technical_perspective"},
        ],
    )

    assert candidate.article_roles[-1].role == "technical_perspective"
