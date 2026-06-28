"""Internal models for resumable Tagger batch checkpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


CHECKPOINT_SCHEMA_VERSION = "1.0"
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
BatchId = Annotated[
    str,
    StringConstraints(pattern=r"^articles-[0-9]{6}-[0-9]{6}$"),
]
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]


def _require_unique(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("items must be unique")
    return values


class CheckpointAIMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model: NonEmptyString
    confidence: float = Field(ge=0, le=1)
    rationale: str | None = Field(default=None, max_length=160)


class CheckpointArticle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: NonEmptyString
    tags: list[NonEmptyString] = Field(max_length=6)
    technologies: list[NonEmptyString] = Field(max_length=6)
    domains: list[NonEmptyString] = Field(max_length=4)
    ai_metadata: CheckpointAIMetadata

    @field_validator("tags", "technologies", "domains")
    @classmethod
    def require_unique_values(cls, values: list[str]) -> list[str]:
        return _require_unique(values)


class TaggerBatchCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = CHECKPOINT_SCHEMA_VERSION
    run_id: NonEmptyString
    input_fingerprint: Fingerprint
    policy_fingerprint: Fingerprint
    batch_id: BatchId
    parent_batch_id: BatchId | None = None
    article_ids: list[NonEmptyString] = Field(min_length=1)
    model: NonEmptyString
    generated_at: datetime
    articles: list[CheckpointArticle] = Field(min_length=1)

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone offset")
        return value

    @field_validator("article_ids")
    @classmethod
    def require_unique_article_ids(cls, values: list[str]) -> list[str]:
        return _require_unique(values)
