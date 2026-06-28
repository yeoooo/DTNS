"""Bounded, resumable Map-Reduce Trend Agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dtns.agents.execution_state import execution_state_path
from dtns.agents.gemini import (
    DEFAULT_FALLBACK_MODEL,
    GenerationResult,
    generate_content_with_fallback,
    resolve_fallback_model,
)
from dtns.agents.trend.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    TrendCandidate,
    TrendCheckpoint,
)


TOPIC_ARTICLES_FILENAME = "topic_articles.json"
TOPIC_TRENDS_FILENAME = "topic_trends.json"
SCHEMA_VERSION = "1.0"
DEFAULT_MODEL = "gemini-3.5-flash"
MODEL_ENV_VAR = "DTNS_TREND_MODEL"
GEMINI_MODEL_ENV_VAR = "GEMINI_MODEL"
MAP_BATCH_SIZE = 12
MAP_CANDIDATE_LIMIT = 4
REDUCE_BATCH_SIZE = 16
REDUCE_CANDIDATE_LIMIT = 8
MAX_RESPONSE_ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 8192
GENERATION_TEMPERATURE = 0.2
STATE_DIRECTORY = Path(".state") / "trend"


class AIMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    confidence: float = Field(ge=0, le=1)
    rationale: str | None = None


class ClassificationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matched_rules: list[str] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0)


class TopicArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source: str
    title: str
    canonical_url: str
    published_at: datetime | None
    tags: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    ai_metadata: AIMetadata
    classification: ClassificationMetadata
    summary: str | None = None

    @field_validator("id", "source", "title", "canonical_url")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("tags", "technologies", "domains")
    @classmethod
    def require_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value


class TopicArticlesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    generated_at: datetime
    topic: Literal["technology", "backend", "qa"]
    articles: list[TopicArticle] = Field(default_factory=list)


class Trend(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=120)
    importance: Literal["high", "medium", "low"]
    summary: str = Field(min_length=1, max_length=500)
    why_it_matters: str = Field(min_length=1, max_length=500)
    article_ids: list[str] = Field(min_length=1, max_length=20)
    keywords: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("article_ids", "keywords")
    @classmethod
    def require_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value

    @field_validator("keywords")
    @classmethod
    def limit_keywords(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 80 for item in value):
            raise ValueError("keywords must contain 1 to 80 characters")
        return value


class TrendPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: date
    end: date


class TrendsFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    generated_at: datetime
    topic: Literal["technology", "backend", "qa"]
    period: TrendPeriod | None = None
    trends: list[Trend] = Field(default_factory=list, max_length=8)

    @field_validator("trends")
    @classmethod
    def unique_ids(cls, value: list[Trend]) -> list[Trend]:
        ids = [trend.id for trend in value]
        if len(ids) != len(set(ids)):
            raise ValueError("trend IDs must be unique")
        return value


@dataclass(frozen=True)
class TrendResponse:
    payload: Mapping[str, Any]
    model: str
    generation: GenerationResult | None = None


class LLMClient(Protocol):
    model: str

    def discover(
        self,
        *,
        topic: str,
        phase: Literal["map", "reduce"],
        sources: Sequence[Mapping[str, Any]],
        candidate_limit: int,
    ) -> Mapping[str, Any] | TrendResponse: ...


@dataclass
class GeminiTrendClient:
    model: str
    raw_client: Any | None = None
    fallback_model: str = field(default_factory=resolve_fallback_model)
    preferred_model: str | None = field(default=None, init=False)
    attempted_models: list[str] = field(default_factory=list, init=False)
    run_id: str | None = field(default=None, init=False)
    execution_state_path: Path | None = field(default=None, init=False)

    def discover(
        self,
        *,
        topic: str,
        phase: Literal["map", "reduce"],
        sources: Sequence[Mapping[str, Any]],
        candidate_limit: int,
    ) -> TrendResponse:
        requested_model = self.preferred_model or self.model
        _append_unique(self.attempted_models, requested_model)
        instruction = (
            f"Perform the {phase} phase. Return only a JSON object containing "
            f"a candidates array with at most {candidate_limit} items. "
            "Do not generate topic, timestamps, period, or schema_version."
        )
        try:
            generation = generate_content_with_fallback(
                primary_model=requested_model,
                fallback_model=self.fallback_model,
                contents=[
                    f"{_build_system_prompt(topic)}\n\n{instruction}",
                    json.dumps(
                        {"topic": topic, "phase": phase, "sources": sources},
                        ensure_ascii=False,
                    ),
                ],
                config={
                    "temperature": GENERATION_TEMPERATURE,
                    "response_mime_type": "application/json",
                    "response_json_schema": _candidate_response_schema(
                        candidate_limit
                    ),
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                },
                client=self.raw_client,
                run_id=self.run_id,
                execution_state_path=self.execution_state_path,
                policy_primary_model=self.model,
            )
        except Exception as error:
            if _is_transient(error) and self.fallback_model != requested_model:
                _append_unique(self.attempted_models, self.fallback_model)
            raise
        _append_unique(self.attempted_models, generation.model)
        _reject_truncated_response(generation.response)
        text = getattr(generation.response, "text", None)
        if not text:
            raise TrendResponseError("invalid_json")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            raise TrendResponseError("invalid_json") from None
        if not isinstance(payload, Mapping):
            raise TrendResponseError("invalid_json")
        return TrendResponse(
            payload=payload,
            model=generation.model,
            generation=generation,
        )


class TrendResponseError(ValueError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


class TrendRunError(RuntimeError):
    """Sanitized terminal failure for one map or reduce checkpoint."""

    def __init__(
        self,
        *,
        run_id: str,
        topic: str,
        phase: str,
        checkpoint_id: str,
        source_ids: Sequence[str],
        models: Sequence[str],
        attempt_count: int,
        category: str,
    ):
        self.run_id = run_id
        self.topic = topic
        self.phase = phase
        self.checkpoint_id = checkpoint_id
        self.source_ids = tuple(source_ids)
        self.models = tuple(dict.fromkeys(models))
        self.attempt_count = attempt_count
        self.category = category
        super().__init__(
            "Trend run failed: "
            f"run_id={run_id}, topic={topic}, phase={phase}, "
            f"checkpoint_id={checkpoint_id}, source_ids={','.join(source_ids)}, "
            f"models={','.join(self.models)}, attempt_count={attempt_count}, "
            f"category={category}"
        )


@dataclass
class _RunContext:
    run_id: str
    topic: Literal["technology", "backend", "qa"]
    input_fingerprint: str
    policy_fingerprint: str
    state_path: Path
    articles: Sequence[TopicArticle]
    client: LLMClient
    checkpoints: dict[str, TrendCheckpoint]
    attempted_models: list[str]


def discover_trends(
    topic: str,
    input_path: Path | str,
    output_path: Path | str,
    *,
    model: str | None = None,
    client: Any | None = None,
    llm_client: LLMClient | None = None,
    run_id: str | None = None,
    state_path: Path | str | None = None,
    ai_state_path: Path | str | None = None,
) -> TrendsFile:
    """Discover bounded topic trends and resume valid checkpoints."""

    input_path = Path(input_path)
    output_path = Path(output_path)
    input_bytes = input_path.read_bytes()
    try:
        input_payload = json.loads(input_bytes)
        document = TopicArticlesFile.model_validate(input_payload)
    except (json.JSONDecodeError, ValidationError) as error:
        raise ValueError("Invalid topic_articles contract") from error
    normalized_topic = _normalize_topic(topic)
    if document.topic != normalized_topic:
        raise ValueError(
            f"topic argument '{normalized_topic}' does not match input topic "
            f"'{document.topic}'"
        )
    article_ids = [article.id for article in document.articles]
    if len(article_ids) != len(set(article_ids)):
        raise ValueError("topic_articles contains duplicate article IDs")

    configured_model = _resolve_model(model)
    selected_client = llm_client or GeminiTrendClient(
        model=configured_model,
        raw_client=client,
    )
    input_fingerprint = hashlib.sha256(input_bytes).hexdigest()
    policy_fingerprint = _policy_fingerprint(
        topic=normalized_topic,
        model=getattr(selected_client, "model", configured_model),
        fallback_model=getattr(selected_client, "fallback_model", None),
    )
    selected_run_id = run_id or (
        f"{input_fingerprint[:16]}-{policy_fingerprint[:16]}"
    )
    _validate_run_id(selected_run_id)
    if isinstance(selected_client, GeminiTrendClient):
        selected_client.run_id = selected_run_id
        selected_client.execution_state_path = (
            Path(ai_state_path)
            if ai_state_path is not None
            else execution_state_path(output_path.parent, selected_run_id)
        )
    selected_state_path = Path(state_path) if state_path else (
        output_path.parent
        / STATE_DIRECTORY
        / normalized_topic
        / selected_run_id
    )
    context = _RunContext(
        run_id=selected_run_id,
        topic=document.topic,
        input_fingerprint=input_fingerprint,
        policy_fingerprint=policy_fingerprint,
        state_path=selected_state_path,
        articles=document.articles,
        client=selected_client,
        checkpoints={},
        attempted_models=[],
    )
    context.checkpoints = _load_checkpoints(context)
    _restore_model_preference(context)

    if not document.articles:
        final_candidates: list[TrendCandidate] = []
    else:
        map_candidates = _run_map(context)
        final_candidates = _run_reduce(map_candidates, context)
    output = _finalize(final_candidates, document)
    _atomic_write_json(
        output_path,
        output.model_dump(mode="json", exclude_none=True),
    )
    return output


def _run_map(context: _RunContext) -> list[TrendCandidate]:
    candidates: list[TrendCandidate] = []
    for start in range(0, len(context.articles), MAP_BATCH_SIZE):
        end = min(start + MAP_BATCH_SIZE, len(context.articles))
        candidates.extend(_map_range(start, end, context))
    return candidates


def _map_range(
    start: int,
    end: int,
    context: _RunContext,
) -> list[TrendCandidate]:
    checkpoint_id = f"map-{start:06d}-{end:06d}"
    articles = context.articles[start:end]
    source_ids = [article.id for article in articles]
    cached = _matching_checkpoint(checkpoint_id, "map", source_ids, context)
    if cached is not None:
        return cached.candidates
    completed_ranges = sorted(
        (
            (*_parse_map_checkpoint_id(item.checkpoint_id), item)
            for item in context.checkpoints.values()
            if item.phase == "map"
            and start <= _parse_map_checkpoint_id(item.checkpoint_id)[0]
            and _parse_map_checkpoint_id(item.checkpoint_id)[1] <= end
        ),
        key=lambda item: item[0],
    )
    if completed_ranges:
        resumed: list[TrendCandidate] = []
        cursor = start
        for completed_start, completed_end, completed in completed_ranges:
            if cursor < completed_start:
                resumed.extend(_map_range(cursor, completed_start, context))
            resumed.extend(completed.candidates)
            cursor = completed_end
        if cursor < end:
            resumed.extend(_map_range(cursor, end, context))
        return resumed
    sources = [_project_article(article) for article in articles]
    try:
        return _request_checkpoint(
            checkpoint_id=checkpoint_id,
            phase="map",
            source_ids=source_ids,
            sources=sources,
            allowed_article_ids=set(source_ids),
            candidate_limit=MAP_CANDIDATE_LIMIT,
            context=context,
        ).candidates
    except TrendRunError as error:
        if len(articles) == 1 or not _is_recoverable_content_failure(error):
            raise
        midpoint = start + (end - start) // 2
        return _map_range(start, midpoint, context) + _map_range(
            midpoint, end, context
        )


def _run_reduce(
    candidates: Sequence[TrendCandidate],
    context: _RunContext,
) -> list[TrendCandidate]:
    current = list(candidates)
    level = 0
    while len(current) > REDUCE_BATCH_SIZE:
        reduced: list[TrendCandidate] = []
        for start in range(0, len(current), REDUCE_BATCH_SIZE):
            group = current[start : start + REDUCE_BATCH_SIZE]
            reduced.extend(
                _reduce_group(
                    group,
                    checkpoint_id=(
                        f"reduce-{level:03d}-{start:06d}-"
                        f"{start + len(group):06d}"
                    ),
                    context=context,
                )
            )
        current = reduced
        level += 1
    return _reduce_group(current, checkpoint_id="reduce-final", context=context)


def _reduce_group(
    candidates: Sequence[TrendCandidate],
    *,
    checkpoint_id: str,
    context: _RunContext,
) -> list[TrendCandidate]:
    if not candidates:
        return []
    source_ids = [candidate.id for candidate in candidates]
    if len(source_ids) != len(set(source_ids)):
        raise _terminal_error(
            context,
            "reduce",
            checkpoint_id,
            source_ids,
            0,
            "id_mismatch",
        )
    allowed_article_ids = {
        article_id
        for candidate in candidates
        for article_id in candidate.article_ids
    }
    cached = _matching_checkpoint(checkpoint_id, "reduce", source_ids, context)
    if cached is not None:
        if any(
            not set(candidate.article_ids) <= allowed_article_ids
            for candidate in cached.candidates
        ):
            raise ValueError(
                f"Trend checkpoint article reference mismatch: {checkpoint_id}"
            )
        return cached.candidates
    digest = hashlib.sha256(
        "\0".join(source_ids).encode("utf-8")
    ).hexdigest()[:12]
    child_ids = {
        f"reduce-split-{digest}-left",
        f"reduce-split-{digest}-right",
        f"reduce-split-{digest}-merge",
    }
    if len(candidates) > 1 and child_ids & context.checkpoints.keys():
        return _reduce_split(candidates, digest=digest, context=context)
    sources = [candidate.model_dump(mode="json") for candidate in candidates]
    try:
        return _request_checkpoint(
            checkpoint_id=checkpoint_id,
            phase="reduce",
            source_ids=source_ids,
            sources=sources,
            allowed_article_ids=allowed_article_ids,
            candidate_limit=REDUCE_CANDIDATE_LIMIT,
            context=context,
        ).candidates
    except TrendRunError as error:
        if len(candidates) == 1 or not _is_recoverable_content_failure(error):
            raise
        return _reduce_split(candidates, digest=digest, context=context)


def _reduce_split(
    candidates: Sequence[TrendCandidate],
    *,
    digest: str,
    context: _RunContext,
) -> list[TrendCandidate]:
    midpoint = len(candidates) // 2
    left = _reduce_group(
        candidates[:midpoint],
        checkpoint_id=f"reduce-split-{digest}-left",
        context=context,
    )
    right = _reduce_group(
        candidates[midpoint:],
        checkpoint_id=f"reduce-split-{digest}-right",
        context=context,
    )
    return _reduce_group(
        left + right,
        checkpoint_id=f"reduce-split-{digest}-merge",
        context=context,
    )


def _request_checkpoint(
    *,
    checkpoint_id: str,
    phase: Literal["map", "reduce"],
    source_ids: Sequence[str],
    sources: Sequence[Mapping[str, Any]],
    allowed_article_ids: set[str],
    candidate_limit: int,
    context: _RunContext,
) -> TrendCheckpoint:
    last_category = "internal_error"
    for attempt in range(1, MAX_RESPONSE_ATTEMPTS + 1):
        requested_model = (
            getattr(context.client, "preferred_model", None)
            or context.client.model
        )
        _append_unique(context.attempted_models, requested_model)
        try:
            raw_response = context.client.discover(
                topic=context.topic,
                phase=phase,
                sources=sources,
                candidate_limit=candidate_limit,
            )
            payload, actual_model, generation = _unpack_response(
                raw_response, context.client
            )
            _append_unique(context.attempted_models, actual_model)
            candidates = _validate_candidates(
                payload,
                allowed_article_ids=allowed_article_ids,
                candidate_limit=candidate_limit,
            )
            if generation is not None:
                accept = getattr(generation, "accept", None)
                if callable(accept):
                    accept()
                if (
                    isinstance(context.client, GeminiTrendClient)
                    and actual_model == context.client.fallback_model
                ):
                    context.client.preferred_model = actual_model
            checkpoint = TrendCheckpoint(
                run_id=context.run_id,
                topic=context.topic,
                input_fingerprint=context.input_fingerprint,
                policy_fingerprint=context.policy_fingerprint,
                checkpoint_id=checkpoint_id,
                phase=phase,
                source_ids=list(source_ids),
                model=actual_model,
                generated_at=datetime.now(UTC),
                candidates=candidates,
            )
            _write_checkpoint(checkpoint, context.state_path)
            context.checkpoints[checkpoint_id] = checkpoint
            return checkpoint
        except TrendResponseError as error:
            last_category = error.category
        except ValidationError:
            last_category = "invalid_schema"
        except OSError:
            raise _terminal_error(
                context,
                phase,
                checkpoint_id,
                source_ids,
                attempt,
                "checkpoint_io",
            ) from None
        except Exception as error:
            category = "transient_api" if _is_transient(error) else "internal_error"
            raise _terminal_error(
                context,
                phase,
                checkpoint_id,
                source_ids,
                attempt,
                category,
            ) from None
    raise _terminal_error(
        context,
        phase,
        checkpoint_id,
        source_ids,
        MAX_RESPONSE_ATTEMPTS,
        last_category,
    ) from None


def _validate_candidates(
    payload: Mapping[str, Any],
    *,
    allowed_article_ids: set[str],
    candidate_limit: int,
) -> list[TrendCandidate]:
    if set(payload) != {"candidates"} or not isinstance(
        payload.get("candidates"), list
    ):
        raise TrendResponseError("invalid_schema")
    raw_candidates = payload["candidates"]
    if len(raw_candidates) > candidate_limit:
        raise TrendResponseError("invalid_schema")
    try:
        candidates = [TrendCandidate.model_validate(item) for item in raw_candidates]
    except ValidationError:
        raise TrendResponseError("invalid_schema") from None
    candidate_ids = [candidate.id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise TrendResponseError("id_mismatch")
    for candidate in candidates:
        if not set(candidate.article_ids) <= allowed_article_ids:
            raise TrendResponseError("id_mismatch")
    return candidates


def _load_checkpoints(context: _RunContext) -> dict[str, TrendCheckpoint]:
    if not context.state_path.exists():
        return {}
    checkpoints: dict[str, TrendCheckpoint] = {}
    map_ranges: list[tuple[int, int]] = []
    known_article_ids = {article.id for article in context.articles}
    for path in sorted(context.state_path.glob("*.json")):
        try:
            checkpoint = TrendCheckpoint.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            raise ValueError(f"Invalid Trend checkpoint: {path.name}") from None
        if path.name != f"{checkpoint.checkpoint_id}.json":
            raise ValueError(f"Trend checkpoint filename mismatch: {path.name}")
        if checkpoint.checkpoint_id in checkpoints:
            raise ValueError("Duplicate Trend checkpoint ID")
        if (
            checkpoint.run_id != context.run_id
            or checkpoint.topic != context.topic
            or checkpoint.input_fingerprint != context.input_fingerprint
            or checkpoint.policy_fingerprint != context.policy_fingerprint
        ):
            raise ValueError(f"Trend checkpoint identity mismatch: {path.name}")
        referenced = {
            article_id
            for candidate in checkpoint.candidates
            for article_id in candidate.article_ids
        }
        if not referenced <= known_article_ids:
            raise ValueError(f"Unknown article ID in Trend checkpoint: {path.name}")
        ids = [candidate.id for candidate in checkpoint.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate candidate ID in Trend checkpoint: {path.name}")
        if checkpoint.phase == "map":
            start, end = _parse_map_checkpoint_id(checkpoint.checkpoint_id)
            expected_ids = [article.id for article in context.articles[start:end]]
            if start >= end or checkpoint.source_ids != expected_ids:
                raise ValueError(f"Invalid map checkpoint range: {path.name}")
            if len(checkpoint.candidates) > MAP_CANDIDATE_LIMIT or any(
                not set(candidate.article_ids) <= set(expected_ids)
                for candidate in checkpoint.candidates
            ):
                raise ValueError(f"Invalid map checkpoint candidates: {path.name}")
            if any(a < end and start < b for a, b in map_ranges):
                raise ValueError(f"Overlapping map checkpoints: {path.name}")
            map_ranges.append((start, end))
        elif not checkpoint.checkpoint_id.startswith("reduce-"):
            raise ValueError(f"Invalid reduce checkpoint ID: {path.name}")
        checkpoints[checkpoint.checkpoint_id] = checkpoint
    for checkpoint in checkpoints.values():
        available_source_ids = {
            candidate.id
            for other in checkpoints.values()
            if other.checkpoint_id != checkpoint.checkpoint_id
            for candidate in other.candidates
        }
        if checkpoint.phase == "reduce" and not set(
            checkpoint.source_ids
        ) <= available_source_ids:
            raise ValueError(
                "Unknown source candidate in Trend checkpoint: "
                f"{checkpoint.checkpoint_id}.json"
            )
    return checkpoints


def _matching_checkpoint(
    checkpoint_id: str,
    phase: str,
    source_ids: Sequence[str],
    context: _RunContext,
) -> TrendCheckpoint | None:
    checkpoint = context.checkpoints.get(checkpoint_id)
    if checkpoint is None:
        return None
    if checkpoint.phase != phase or checkpoint.source_ids != list(source_ids):
        raise ValueError(f"Trend checkpoint source mismatch: {checkpoint_id}")
    return checkpoint


def _finalize(
    candidates: Sequence[TrendCandidate],
    document: TopicArticlesFile,
) -> TrendsFile:
    positions = {article.id: index for index, article in enumerate(document.articles)}
    importance_order = {"high": 0, "medium": 1, "low": 2}
    ordered = sorted(
        candidates,
        key=lambda item: (
            importance_order[item.importance],
            min(positions[article_id] for article_id in item.article_ids),
            item.id,
        ),
    )
    trends = [Trend.model_validate(item.model_dump(mode="json")) for item in ordered]
    output = TrendsFile(
        generated_at=datetime.now(UTC),
        topic=document.topic,
        period=_infer_period(document.articles),
        trends=trends,
    )
    _validate_article_references(output, document.articles)
    return output


def _project_article(article: TopicArticle) -> dict[str, Any]:
    return {
        "id": article.id,
        "title": article.title,
        "published_at": (
            article.published_at.isoformat() if article.published_at else None
        ),
        "summary": article.summary[:500] if article.summary else None,
        "tags": article.tags[:6],
        "technologies": article.technologies[:6],
        "domains": article.domains[:4],
    }


def _candidate_response_schema(limit: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidates"],
        "properties": {
            "candidates": {
                "type": "array",
                "maxItems": limit,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id", "title", "importance", "summary",
                        "why_it_matters", "article_ids", "keywords",
                    ],
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "title": {"type": "string", "minLength": 1, "maxLength": 120},
                        "importance": {"type": "string", "enum": ["high", "medium", "low"]},
                        "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                        "why_it_matters": {"type": "string", "minLength": 1, "maxLength": 500},
                        "article_ids": {
                            "type": "array", "minItems": 1, "maxItems": 20,
                            "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "keywords": {
                            "type": "array", "maxItems": 8, "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1, "maxLength": 80},
                        },
                    },
                },
            }
        },
    }


def _policy_fingerprint(
    *, topic: str, model: str, fallback_model: str | None
) -> str:
    policy = {
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "public_schema": TrendsFile.model_json_schema(),
        "prompt": _build_system_prompt(topic),
        "models": {"primary": model, "fallback": fallback_model or DEFAULT_FALLBACK_MODEL},
        "limits": {
            "map_batch": MAP_BATCH_SIZE, "map_candidates": MAP_CANDIDATE_LIMIT,
            "reduce_batch": REDUCE_BATCH_SIZE, "reduce_candidates": REDUCE_CANDIDATE_LIMIT,
            "attempts": MAX_RESPONSE_ATTEMPTS, "output_tokens": MAX_OUTPUT_TOKENS,
        },
        "generation": {"temperature": GENERATION_TEMPERATURE, "mime_type": "application/json"},
        "map_schema": _candidate_response_schema(MAP_CANDIDATE_LIMIT),
        "reduce_schema": _candidate_response_schema(REDUCE_CANDIDATE_LIMIT),
    }
    return hashlib.sha256(
        json.dumps(policy, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _build_system_prompt(topic: str) -> str:
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / f"trend_{topic}.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return (
        "You are the DTNS Trend Agent. Group related articles into concise "
        "weekly trends. Return JSON only and use only supplied article IDs."
    )


def _infer_period(articles: Sequence[TopicArticle]) -> TrendPeriod | None:
    dates = [article.published_at.date() for article in articles if article.published_at]
    return TrendPeriod(start=min(dates), end=max(dates)) if dates else None


def _validate_article_references(
    output: TrendsFile, articles: Sequence[TopicArticle]
) -> None:
    known_ids = {article.id for article in articles}
    for trend in output.trends:
        if not set(trend.article_ids) <= known_ids:
            raise ValueError(f"trend '{trend.id}' references unknown article IDs")


def _unpack_response(
    response: Mapping[str, Any] | TrendResponse, client: LLMClient
) -> tuple[Mapping[str, Any], str, GenerationResult | None]:
    if isinstance(response, TrendResponse):
        return response.payload, response.model, response.generation
    if not isinstance(response, Mapping):
        raise TrendResponseError("invalid_schema")
    return response, client.model, None


def _restore_model_preference(context: _RunContext) -> None:
    client = context.client
    fallback = getattr(client, "fallback_model", None)
    if fallback and any(cp.model == fallback for cp in context.checkpoints.values()):
        try:
            client.preferred_model = fallback  # type: ignore[attr-defined]
        except AttributeError:
            pass


def _terminal_error(
    context: _RunContext,
    phase: str,
    checkpoint_id: str,
    source_ids: Sequence[str],
    attempt_count: int,
    category: str,
) -> TrendRunError:
    models = list(context.attempted_models)
    for attempted in getattr(context.client, "attempted_models", ()):
        _append_unique(models, attempted)
    return TrendRunError(
        run_id=context.run_id, topic=context.topic, phase=phase,
        checkpoint_id=checkpoint_id, source_ids=source_ids, models=models,
        attempt_count=attempt_count, category=category,
    )


def _is_recoverable_content_failure(error: TrendRunError) -> bool:
    return error.category in {
        "max_tokens",
        "invalid_json",
        "invalid_schema",
        "id_mismatch",
    }


def _reject_truncated_response(response: Any) -> None:
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        reason = getattr(candidates[0], "finish_reason", None)
        name = getattr(reason, "name", str(reason)).upper()
        if "MAX_TOKENS" in name or "LENGTH" in name:
            raise TrendResponseError("max_tokens")


def _is_transient(error: BaseException) -> bool:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for value in (
            getattr(current, "code", None),
            getattr(current, "status_code", None),
            getattr(getattr(current, "response", None), "status_code", None),
        ):
            try:
                if int(value) in {429, 500, 502, 503, 504}:
                    return True
            except (TypeError, ValueError):
                pass
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def _write_checkpoint(checkpoint: TrendCheckpoint, state_path: Path) -> None:
    _atomic_write_json(
        state_path / f"{checkpoint.checkpoint_id}.json",
        checkpoint.model_dump(mode="json"),
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _resolve_model(model: str | None) -> str:
    _load_dotenv()
    value = (
        model or os.getenv(MODEL_ENV_VAR) or os.getenv(GEMINI_MODEL_ENV_VAR)
        or DEFAULT_MODEL
    ).strip()
    if not value:
        raise ValueError("Trend model must not be empty")
    return value


def _normalize_topic(topic: str) -> Literal["technology", "backend", "qa"]:
    normalized = topic.strip()
    if normalized not in {"technology", "backend", "qa"}:
        raise ValueError("topic must be technology, backend, or qa")
    return normalized  # type: ignore[return-value]


def _validate_run_id(run_id: str) -> None:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be a non-empty path segment")


def _parse_map_checkpoint_id(checkpoint_id: str) -> tuple[int, int]:
    match = re.fullmatch(r"map-(\d{6})-(\d{6})", checkpoint_id)
    if match is None:
        raise ValueError(f"Invalid map checkpoint ID: {checkpoint_id}")
    return int(match.group(1)), int(match.group(2))


def _append_unique(values: list[str], value: str | None) -> None:
    if value and value not in values:
        values.append(value)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dtns.agents.trend")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--input", default=TOPIC_ARTICLES_FILENAME, type=Path)
    parser.add_argument("--output", default=TOPIC_TRENDS_FILENAME, type=Path)
    parser.add_argument("--model", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--state-path", default=None, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    discover_trends(
        args.topic, args.input, args.output, model=args.model,
        run_id=args.run_id, state_path=args.state_path,
    )
    return 0
