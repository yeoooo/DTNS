"""Strict internal checkpoint model for the Editor Agent."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


CHECKPOINT_SCHEMA_VERSION = "1.0"
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]


class EditorGenerationCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = CHECKPOINT_SCHEMA_VERSION
    run_id: NonEmptyString
    topic: Literal["technology", "backend", "qa"]
    input_fingerprint: Fingerprint
    policy_fingerprint: Fingerprint
    model: NonEmptyString
    candidate_filename: Literal["candidate.md"] = "candidate.md"
    candidate_fingerprint: Fingerprint
    character_count: int = Field(ge=1, le=12000)
    validated_sections: list[
        Literal["title", "summary", "trends", "insights"]
    ] = Field(min_length=4, max_length=4)
    generated_at: datetime

    @field_validator("validated_sections")
    @classmethod
    def require_all_sections(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or set(value) != {
            "title", "summary", "trends", "insights"
        }:
            raise ValueError("all validated sections must be present")
        return value

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone offset")
        return value
