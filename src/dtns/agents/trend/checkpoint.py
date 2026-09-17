"""Strict internal checkpoint models for the Trend Agent."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)


CHECKPOINT_SCHEMA_VERSION = "1.0"
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
CheckpointId = Annotated[
    str,
    StringConstraints(pattern=r"^(map|reduce)-[a-z0-9-]+$"),
]
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]


def _require_unique(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("items must be unique")
    return values


class TrendArticleRole(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    article_id: NonEmptyString
    role: Literal[
        "production_case", "meaningful_release", "technical_perspective", "supporting"
    ]


class TrendCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: NonEmptyString
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    importance: Literal["high", "medium", "low"]
    summary: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    why_it_matters: Annotated[
        str,
        StringConstraints(min_length=1, max_length=500),
    ]
    article_ids: list[NonEmptyString] = Field(min_length=1, max_length=20)
    keywords: list[
        Annotated[str, StringConstraints(min_length=1, max_length=80)]
    ] = Field(max_length=8)
    article_roles: list[TrendArticleRole] = Field(default_factory=list, max_length=20)

    @field_validator("article_ids", "keywords")
    @classmethod
    def require_unique_items(cls, values: list[str]) -> list[str]:
        return _require_unique(values)

    @field_validator("article_roles")
    @classmethod
    def require_unique_role_articles(
        cls, values: list[TrendArticleRole]
    ) -> list[TrendArticleRole]:
        if len({value.article_id for value in values}) != len(values):
            raise ValueError("article roles must reference unique articles")
        return values


class TrendCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = CHECKPOINT_SCHEMA_VERSION
    run_id: NonEmptyString
    topic: Literal["technology", "backend", "game_client"]
    input_fingerprint: Fingerprint
    policy_fingerprint: Fingerprint
    checkpoint_id: CheckpointId
    phase: Literal["map", "reduce"]
    source_ids: list[NonEmptyString] = Field(min_length=1)
    model: NonEmptyString
    generated_at: datetime
    candidates: list[TrendCandidate] = Field(max_length=8)

    @field_validator("source_ids")
    @classmethod
    def require_unique_source_ids(cls, values: list[str]) -> list[str]:
        return _require_unique(values)

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone offset")
        return value
