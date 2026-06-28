"""Validated, durable receipts for resumable Discord publishing."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


RECEIPT_SCHEMA_VERSION = "1.0"
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]


class PublishChunkReceipt(BaseModel):
    """Delivery state for one deterministic Discord message chunk."""

    model_config = ConfigDict(extra="forbid", strict=True)

    index: int = Field(ge=0)
    fingerprint: Fingerprint
    character_count: int = Field(ge=1, le=2000)
    status: Literal["pending", "delivered", "failed", "unknown"] = "pending"
    attempts: int = Field(default=0, ge=0)
    delivered_at: datetime | None = None

    @field_validator("delivered_at")
    @classmethod
    def require_delivery_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("delivered_at must include a timezone offset")
        return value


class PublishReceipt(BaseModel):
    """Validated state for one newsletter and webhook identity."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = RECEIPT_SCHEMA_VERSION
    run_id: NonEmptyString
    topic: Literal["technology", "backend", "qa"]
    newsletter_fingerprint: Fingerprint
    webhook_fingerprint: Fingerprint
    status: Literal["pending", "partial", "completed", "failed"] = "pending"
    chunks: list[PublishChunkReceipt]
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must include a timezone offset")
        return value


def read_publish_receipt(path: Path) -> PublishReceipt | None:
    """Read and validate a receipt, returning ``None`` when it does not exist."""

    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        return None
    return PublishReceipt.model_validate_json(payload)


def write_publish_receipt(path: Path, receipt: PublishReceipt) -> None:
    """Validate and atomically replace a receipt using a sibling temp file."""

    validated = PublishReceipt.model_validate(receipt.model_dump())
    path.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                validated.model_dump_json(indent=2, exclude_none=False) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
