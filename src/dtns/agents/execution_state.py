"""Persistent circuit state shared by Gemini-backed pipeline stages."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)


SCHEMA_VERSION = "1.0"
EXECUTION_STATE_FILENAME = "execution_state.json"
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]


class AIExecutionState(BaseModel):
    """Strict representation of ``ai_execution_state.schema.json``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: NonEmptyString
    policy_fingerprint: Fingerprint
    primary_model: NonEmptyString
    fallback_model: NonEmptyString
    preferred_model: NonEmptyString
    circuit_state: Literal["closed", "open"]
    opened_at: datetime | None = None
    open_reason: Literal["transient_api_exhausted"] | None = None
    primary_failures: int = Field(default=0, ge=0)
    fallback_successes: int = Field(default=0, ge=0)
    updated_at: datetime

    @model_validator(mode="after")
    def validate_circuit_invariants(self) -> AIExecutionState:
        expected = (
            self.primary_model
            if self.circuit_state == "closed"
            else self.fallback_model
        )
        if self.preferred_model != expected:
            raise ValueError("preferred_model does not match circuit_state")
        if self.circuit_state == "closed":
            if self.opened_at is not None or self.open_reason is not None:
                raise ValueError("closed circuit must not have open metadata")
        elif self.opened_at is None or self.open_reason is None:
            raise ValueError("open circuit requires open metadata")
        for field_name in ("opened_at", "updated_at"):
            value = getattr(self, field_name)
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{field_name} must include a timezone offset")
        return self


class AIExecutionStateStore:
    """Load and atomically update execution state for one pipeline run."""

    def __init__(
        self,
        path: Path | str,
        *,
        run_id: str,
        policy_fingerprint: str,
        primary_model: str,
        fallback_model: str,
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.policy_fingerprint = policy_fingerprint
        self.primary_model = primary_model
        self.fallback_model = fallback_model

    def load(self) -> AIExecutionState:
        """Return persisted state or a fresh, not-yet-persisted closed state."""

        if not self.path.exists():
            return self._closed_state()
        try:
            state = AIExecutionState.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
            raise ValueError("Invalid AI execution state") from error
        if state.run_id != self.run_id:
            raise ValueError("AI execution state run_id mismatch")
        if (
            state.policy_fingerprint != self.policy_fingerprint
            or state.primary_model != self.primary_model
            or state.fallback_model != self.fallback_model
        ):
            return self._closed_state()
        return state

    def save(self, state: AIExecutionState) -> None:
        validated = AIExecutionState.model_validate_json(state.model_dump_json())
        if validated.run_id != self.run_id:
            raise ValueError("AI execution state run_id mismatch")
        if (
            validated.policy_fingerprint != self.policy_fingerprint
            or validated.primary_model != self.primary_model
            or validated.fallback_model != self.fallback_model
        ):
            raise ValueError("AI execution state policy mismatch")
        payload = validated.model_dump(mode="json", exclude_none=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    def _closed_state(self) -> AIExecutionState:
        return AIExecutionState(
            run_id=self.run_id,
            policy_fingerprint=self.policy_fingerprint,
            primary_model=self.primary_model,
            fallback_model=self.fallback_model,
            preferred_model=self.primary_model,
            circuit_state="closed",
            updated_at=datetime.now(UTC),
        )


def execution_state_path(data_dir: Path | str, run_id: str) -> Path:
    """Return the contract-defined state path below a data directory."""

    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be a non-empty path segment")
    return Path(data_dir) / ".state" / "ai" / run_id / EXECUTION_STATE_FILENAME
