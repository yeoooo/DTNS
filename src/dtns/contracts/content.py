"""Shared content-selection contracts used across pipeline artifacts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class EditorialSourceType(StrEnum):
    OFFICIAL_RELEASE = "official_release"
    ENGINEERING_BLOG = "engineering_blog"
    DEVELOPER_BLOG = "developer_blog"
    ENGINE_CASE_STUDY = "engine_case_study"
    CONFERENCE = "conference"
    RESEARCH_LAB = "research_lab"
    STEAM_DEVLOG = "steam_devlog"
    TECHNICAL_MEDIA = "technical_media"
    TECHNICAL_SYNTHESIS = "technical_synthesis"
    DISCOVERY = "discovery"


class SourcePriority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceMetadata(BaseModel):
    """Editorial provenance, separate from RSS/API transport details."""

    model_config = ConfigDict(extra="forbid")

    source_type: EditorialSourceType
    source_priority: SourcePriority
    source_name: str = Field(min_length=1)
    source_url: HttpUrl
    is_discovery: bool = False


class ArticleType(StrEnum):
    PRODUCTION_CASE = "production_case"
    RELEASE = "release"
    ENGINEERING_DEEP_DIVE = "engineering_deep_dive"
    INCIDENT = "incident"
    MIGRATION = "migration"
    BENCHMARK = "benchmark"
    RESEARCH = "research"
    TECHNICAL_SYNTHESIS = "technical_synthesis"
    ARCHITECTURE_ESSAY = "architecture_essay"
    ANNOUNCEMENT = "announcement"
    GENERAL_NEWS = "general_news"


class ArticleEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    implementation_detail: bool = False
    has_metrics: bool = False
    has_tradeoff: bool = False
    has_production_problem: bool = False
    has_existing_limit: bool = False
    connects_multiple_sources: bool = False
    explains_why: bool = False
    provides_decision_criteria: bool = False


class ArticleEvaluation(BaseModel):
    """Bounded 0..3 signals; zero means the input did not provide evidence."""

    model_config = ConfigDict(extra="forbid")

    technical_depth: int = Field(default=0, ge=0, le=3)
    practical_relevance: int = Field(default=0, ge=0, le=3)
    source_quality: int = Field(default=0, ge=0, le=3)
    ecosystem_impact: int = Field(default=0, ge=0, le=3)
    implementation_detail: int = Field(default=0, ge=0, le=3)
    release_significance: int = Field(default=0, ge=0, le=3)
    cross_cutting_relevance: int = Field(default=0, ge=0, le=3)
    decision_making_value: int = Field(default=0, ge=0, le=3)
    trend_explanatory_power: int = Field(default=0, ge=0, le=3)


MEANINGFUL_RELEASE_CHANGES = frozenset(
    {
        "breaking_change",
        "major_feature",
        "performance_improvement",
        "architecture_change",
        "new_programming_model",
        "new_runtime_capability",
        "developer_experience",
        "critical_security",
    }
)

