"""Atomic pipeline-run manifest and deterministic stage resume orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)


SCHEMA_VERSION = "1.0"
MANIFEST_FILENAME = "pipeline_run.json"
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
StageStatus = Literal["pending", "running", "completed", "failed", "skipped"]


class PipelineStageState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    stage_id: Annotated[
        str,
        StringConstraints(
            pattern=(
                r"^(collect|preprocess|tag|classify|"
                r"trend:(technology|backend|qa)|"
                r"edit:(technology|backend|qa)|"
                r"publish:(technology|backend|qa))$"
            )
        ),
    ]
    status: StageStatus = "pending"
    attempts: int = Field(default=0, ge=0)
    input_fingerprints: list[Fingerprint] = Field(default_factory=list)
    output_fingerprints: list[Fingerprint] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_category: Annotated[str, StringConstraints(max_length=80)] | None = None

    @field_validator("input_fingerprints", "output_fingerprints")
    @classmethod
    def require_unique_fingerprints(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("fingerprints must be unique")
        return values

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("stage timestamps must include a timezone offset")
        return value


class PipelineRunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: NonEmptyString
    status: Literal["running", "completed", "failed"] = "running"
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    stages: list[PipelineStageState] = Field(min_length=1)

    @field_validator("started_at", "updated_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("manifest timestamps must include a timezone offset")
        return value

    @field_validator("stages")
    @classmethod
    def require_unique_stage_ids(
        cls, values: list[PipelineStageState]
    ) -> list[PipelineStageState]:
        ids = [stage.stage_id for stage in values]
        if len(ids) != len(set(ids)):
            raise ValueError("stage IDs must be unique")
        return values


@dataclass(frozen=True)
class PipelineStage:
    stage_id: str
    action: Callable[[], Any]
    inputs: Sequence[Path]
    outputs: Callable[[], Sequence[Path]]
    configuration: Any = None
    validate_outputs: Callable[[Sequence[Path]], None] | None = None


def run_pipeline(
    data_dir: Path | str,
    run_id: str,
    stages: Sequence[PipelineStage],
) -> PipelineRunManifest:
    """Run or resume ordered stages while updating the manifest atomically."""

    path = pipeline_manifest_path(data_dir, run_id)
    manifest = _load_or_create(path, run_id, stages)
    expected_ids = [stage.stage_id for stage in stages]
    if [stage.stage_id for stage in manifest.stages] != expected_ids:
        raise ValueError("Pipeline manifest stage order mismatch")

    manifest.status = "running"
    manifest.finished_at = None
    _touch_and_write(path, manifest)
    force_rerun = False
    for index, definition in enumerate(stages):
        state = manifest.stages[index]
        try:
            inputs: list[str] | None = _input_fingerprints(definition)
        except (OSError, ValueError):
            inputs = None
        outputs = _resolve_outputs(definition)
        if (
            not force_rerun
            and state.status == "completed"
            and inputs is not None
            and state.input_fingerprints == inputs
            and _artifacts_match(definition, outputs, state.output_fingerprints)
        ):
            continue

        if not force_rerun:
            force_rerun = True
            _invalidate_from(manifest, index)
            _touch_and_write(path, manifest)

        now = datetime.now(UTC)
        state.status = "running"
        state.attempts += 1
        state.input_fingerprints = inputs or []
        state.output_fingerprints = []
        state.started_at = now
        state.finished_at = None
        state.error_category = None
        _touch_and_write(path, manifest)
        try:
            if inputs is None:
                inputs = _input_fingerprints(definition)
                state.input_fingerprints = inputs
                _touch_and_write(path, manifest)
            definition.action()
            outputs = _resolve_outputs(definition)
            if not outputs or any(not output.is_file() for output in outputs):
                raise RuntimeError("stage did not produce its required artifacts")
            if definition.validate_outputs is not None:
                definition.validate_outputs(outputs)
            state.output_fingerprints = _fingerprints(outputs)
        except BaseException as error:
            state.status = "failed"
            state.finished_at = datetime.now(UTC)
            state.error_category = type(error).__name__[:80]
            manifest.status = "failed"
            manifest.finished_at = state.finished_at
            _touch_and_write(path, manifest)
            raise
        state.status = "completed"
        state.finished_at = datetime.now(UTC)
        _touch_and_write(path, manifest)

    manifest.status = "completed"
    manifest.finished_at = datetime.now(UTC)
    _touch_and_write(path, manifest)
    return manifest


def pipeline_manifest_path(data_dir: Path | str, run_id: str) -> Path:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be a non-empty path segment")
    return (
        Path(data_dir)
        / ".state"
        / "pipeline"
        / run_id
        / MANIFEST_FILENAME
    )


def _load_or_create(
    path: Path,
    run_id: str,
    stages: Sequence[PipelineStage],
) -> PipelineRunManifest:
    if path.exists():
        manifest = PipelineRunManifest.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        if manifest.run_id != run_id:
            raise ValueError("Pipeline manifest run_id mismatch")
        return manifest
    now = datetime.now(UTC)
    return PipelineRunManifest(
        run_id=run_id,
        started_at=now,
        updated_at=now,
        stages=[PipelineStageState(stage_id=stage.stage_id) for stage in stages],
    )


def _input_fingerprints(stage: PipelineStage) -> list[str]:
    missing = [str(path) for path in stage.inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing stage input artifacts: " + ", ".join(missing))
    values = _fingerprints(stage.inputs)
    if stage.configuration is not None:
        encoded = json.dumps(
            stage.configuration,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        values.append(hashlib.sha256(encoded).hexdigest())
    return list(dict.fromkeys(values))


def _fingerprints(paths: Sequence[Path]) -> list[str]:
    values = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    return list(dict.fromkeys(values))


def _resolve_outputs(stage: PipelineStage) -> list[Path]:
    try:
        return list(stage.outputs())
    except (OSError, ValueError):
        return []


def _artifacts_match(
    stage: PipelineStage,
    paths: Sequence[Path],
    fingerprints: Sequence[str],
) -> bool:
    if (
        not paths
        or not all(path.is_file() for path in paths)
        or _fingerprints(paths) != list(fingerprints)
    ):
        return False
    try:
        if stage.validate_outputs is not None:
            stage.validate_outputs(paths)
    except (OSError, ValueError):
        return False
    return True


def _invalidate_from(manifest: PipelineRunManifest, index: int) -> None:
    for stage in manifest.stages[index:]:
        stage.status = "pending"
        stage.input_fingerprints = []
        stage.output_fingerprints = []
        stage.started_at = None
        stage.finished_at = None
        stage.error_category = None


def _touch_and_write(path: Path, manifest: PipelineRunManifest) -> None:
    manifest.updated_at = datetime.now(UTC)
    validated = PipelineRunManifest.model_validate_json(manifest.model_dump_json())
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(validated.model_dump_json(indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
