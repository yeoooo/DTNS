"""Models for the raw article collector contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SourceType(StrEnum):
    RSS = "rss"
    ATOM = "atom"
    GITHUB_RELEASE = "github_release"
    API = "api"
    HTML = "html"


class RawArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_type: SourceType | None = None
    title: str = Field(min_length=1)
    url: HttpUrl
    summary: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    collected_at: datetime
    raw: dict[str, Any] | None = None


class RawArticlesDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    source_run_id: str | None = None
    articles: list[RawArticle]
