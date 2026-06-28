"""Implementation-facing models for the collection report contract."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


Fingerprint = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
SourceType = Literal["rss", "atom", "github_release", "api", "html"]
ErrorCategory = Literal[
    "timeout",
    "dns",
    "http_client",
    "http_server",
    "invalid_feed",
    "validation",
    "unknown",
]


class CollectionSourceReport(BaseModel):
    """Outcome of attempting one configured collector source."""

    model_config = ConfigDict(extra="forbid")

    name: NonEmptyString
    source_type: SourceType
    status: Literal["success", "empty", "failed"]
    fetched_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    http_status: int | None = Field(default=None, ge=100, le=599)
    error_category: ErrorCategory | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> CollectionSourceReport:
        if self.accepted_count > self.fetched_count:
            raise ValueError("accepted_count cannot exceed fetched_count")
        if self.status == "failed":
            if self.error_category is None:
                raise ValueError("failed source requires error_category")
            if self.fetched_count or self.accepted_count:
                raise ValueError("failed source counts must be zero")
        elif self.error_category is not None:
            raise ValueError("successful source cannot have error_category")
        if self.status == "empty" and self.accepted_count:
            raise ValueError("empty source cannot have accepted articles")
        if self.status == "success" and not self.accepted_count:
            raise ValueError("successful source requires accepted articles")
        return self


class CollectionReport(BaseModel):
    """Validated source-level health report for one collector run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_id: NonEmptyString
    source_config_fingerprint: Fingerprint
    started_at: datetime
    finished_at: datetime
    status: Literal["completed", "partial", "failed"]
    sources: list[CollectionSourceReport] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_run_status(self) -> CollectionReport:
        success_count = sum(source.status != "failed" for source in self.sources)
        if success_count == len(self.sources):
            expected_status = "completed"
        elif success_count:
            expected_status = "partial"
        else:
            expected_status = "failed"
        if self.status != expected_status:
            raise ValueError("status does not match source outcomes")
        for field_name in ("started_at", "finished_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone offset")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        return self
