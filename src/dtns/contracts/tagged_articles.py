"""Pydantic models for normalized and tagged article contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION = "1.0"


class NormalizedArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    title: str = Field(min_length=1)
    canonical_url: str
    published_at: datetime | None
    collected_at: datetime
    source_type: str | None = None
    original_url: str | None = None
    summary: str | None = None
    author: str | None = None
    language: str | None = None


class NormalizedArticlesDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    generated_at: datetime
    articles: list[NormalizedArticle] = Field(default_factory=list)


class AIMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    confidence: float = Field(ge=0, le=1)
    rationale: str | None = None

    @field_validator("rationale")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = re.sub(r"\s+", " ", value).strip()
        return normalized or None


class TaggedArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    title: str
    canonical_url: str
    published_at: datetime | None
    tags: list[str]
    technologies: list[str]
    domains: list[str]
    ai_metadata: AIMetadata
    summary: str | None = None


class TaggedArticlesDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    generated_at: datetime
    articles: list[TaggedArticle]
