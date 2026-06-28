"""AI-assisted article tagging with resumable adaptive batches."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from dtns.agents.execution_state import execution_state_path
from dtns.agents.gemini import (
    DEFAULT_FALLBACK_MODEL,
    GenerationResult,
    generate_content_with_fallback,
    generation_policy_fingerprint,
    resolve_fallback_model,
)
from dtns.agents.tagger.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointAIMetadata,
    CheckpointArticle,
    TaggerBatchCheckpoint,
)
from dtns.contracts.tagged_articles import (
    AIMetadata,
    NormalizedArticle,
    NormalizedArticlesDocument,
    SCHEMA_VERSION as TAGGED_SCHEMA_VERSION,
    TaggedArticle,
    TaggedArticlesDocument,
)


NORMALIZED_ARTICLES_FILENAME = "normalized_articles.json"
TAGGED_ARTICLES_FILENAME = "tagged_articles.json"
DEFAULT_MODEL = "gemini-3.5-flash"
MODEL_ENV_VAR = "DTNS_TAGGER_MODEL"
GEMINI_MODEL_ENV_VAR = "GEMINI_MODEL"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "tagger.md"
TAGGER_BATCH_SIZE = 8
MAX_BATCH_ATTEMPTS = 2
MAX_TAGS = 6
MAX_TECHNOLOGIES = 6
MAX_DOMAINS = 4
MAX_RATIONALE_LENGTH = 160
MAX_OUTPUT_TOKENS = 8192
GENERATION_TEMPERATURE = 0.1
STATE_DIRECTORY = Path(".state") / "tagger"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaggerResponse:
    payload: Mapping[str, Any]
    model: str
    generation: GenerationResult | None = None


class LLMClient(Protocol):
    model: str

    def tag(
        self, articles: Sequence[NormalizedArticle]
    ) -> Mapping[str, Any] | TaggerResponse:
        """Return JSON-compatible tag data for the supplied articles."""


@dataclass
class GeminiTaggerClient:
    """Gemini-backed client that stays on fallback after fallback succeeds."""

    model: str
    fallback_model: str = field(default_factory=resolve_fallback_model)
    prompt_path: Path = PROMPT_PATH
    preferred_model: str | None = field(default=None, init=False)
    attempted_models: list[str] = field(default_factory=list, init=False)
    run_id: str | None = field(default=None, init=False)
    execution_state_path: Path | None = field(default=None, init=False)

    def tag(self, articles: Sequence[NormalizedArticle]) -> TaggerResponse:
        _load_dotenv()
        requested_model = self.preferred_model or self.model
        _append_unique(self.attempted_models, requested_model)
        try:
            generation = generate_content_with_fallback(
                primary_model=requested_model,
                fallback_model=self.fallback_model,
                contents=[
                    self.prompt_path.read_text(encoding="utf-8"),
                    json.dumps(
                        {
                            "schema_version": TAGGED_SCHEMA_VERSION,
                            "articles": [
                                article.model_dump(mode="json", exclude_none=True)
                                for article in articles
                            ],
                        },
                        ensure_ascii=False,
                    ),
                ],
                config={
                    "temperature": GENERATION_TEMPERATURE,
                    "response_mime_type": "application/json",
                    "response_json_schema": _tagger_response_schema(),
                    "max_output_tokens": MAX_OUTPUT_TOKENS,
                },
                run_id=self.run_id,
                execution_state_path=self.execution_state_path,
                policy_primary_model=self.model,
            )
        except Exception as error:
            if (
                _client_error_category(error) == "transient_api"
                and self.fallback_model != requested_model
            ):
                _append_unique(self.attempted_models, self.fallback_model)
            raise
        _append_unique(self.attempted_models, generation.model)
        response = generation.response
        _reject_truncated_response(response)
        content = getattr(response, "text", None)
        if not content:
            raise BatchResponseError("invalid_json", "LLM returned an empty response")

        return TaggerResponse(
            payload=_parse_json_object(content),
            model=generation.model,
            generation=generation,
        )


class BatchResponseError(ValueError):
    """A model response failure eligible for retry and adaptive splitting."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class TaggerRunError(RuntimeError):
    """Sanitized terminal error for one Tagger batch."""

    def __init__(
        self,
        *,
        run_id: str,
        batch_id: str,
        article_ids: Sequence[str],
        models: Sequence[str],
        attempt_count: int,
        category: str,
    ):
        self.run_id = run_id
        self.batch_id = batch_id
        self.article_ids = tuple(article_ids)
        self.models = tuple(dict.fromkeys(models))
        self.attempt_count = attempt_count
        self.category = category
        super().__init__(
            f"Tagger run failed: run_id={run_id}, batch_id={batch_id}, "
            f"article_ids={','.join(article_ids)}, models={','.join(self.models)}, "
            f"attempt_count={attempt_count}, category={category}"
        )


@dataclass(frozen=True)
class _RunContext:
    run_id: str
    input_fingerprint: str
    policy_fingerprint: str
    state_path: Path
    input_articles: Sequence[NormalizedArticle]
    llm_client: LLMClient
    attempted_models: list[str]


def tag_articles(
    input_path: Path | str,
    output_path: Path | str,
    *,
    llm_client: LLMClient | None = None,
    model: str | None = None,
    run_id: str | None = None,
    state_path: Path | str | None = None,
    ai_state_path: Path | str | None = None,
) -> TaggedArticlesDocument:
    """Tag normalized articles, resuming valid completed batch checkpoints."""

    input_path = Path(input_path)
    output_path = Path(output_path)
    input_bytes = input_path.read_bytes()
    input_payload = json.loads(input_bytes)
    _validate_normalized_articles_schema(input_payload)
    document = NormalizedArticlesDocument.model_validate_json(input_bytes)
    configured_model = resolve_model(model)
    client = llm_client or GeminiTaggerClient(model=configured_model)
    input_fingerprint = hashlib.sha256(input_bytes).hexdigest()
    policy_fingerprint = _policy_fingerprint(
        model=getattr(client, "model", configured_model),
        fallback_model=getattr(client, "fallback_model", None),
    )
    selected_run_id = run_id or _default_run_id(
        input_fingerprint,
        policy_fingerprint,
    )
    _validate_run_id(selected_run_id)
    if isinstance(client, GeminiTaggerClient):
        client.run_id = selected_run_id
        client.execution_state_path = (
            Path(ai_state_path)
            if ai_state_path is not None
            else execution_state_path(output_path.parent, selected_run_id)
        )
    selected_state_path = Path(state_path) if state_path else (
        output_path.parent / STATE_DIRECTORY / selected_run_id
    )
    context = _RunContext(
        run_id=selected_run_id,
        input_fingerprint=input_fingerprint,
        policy_fingerprint=policy_fingerprint,
        state_path=selected_state_path,
        input_articles=document.articles,
        llm_client=client,
        attempted_models=[],
    )

    checkpoints = _load_checkpoints(context)
    completed = _index_completed_articles(checkpoints, context)
    _restore_model_preference(client, checkpoints)
    _process_uncovered_ranges(completed, context)
    output = _finalize_document(document.articles, completed)
    output_payload = output.model_dump(mode="json", exclude_none=True)
    _validate_tagged_articles_schema(output_payload)
    _atomic_write_json(
        output_path,
        output_payload,
    )
    return output


def _process_uncovered_ranges(
    completed: dict[int, CheckpointArticle],
    context: _RunContext,
) -> None:
    article_count = len(context.input_articles)
    for initial_start in range(0, article_count, TAGGER_BATCH_SIZE):
        initial_end = min(initial_start + TAGGER_BATCH_SIZE, article_count)
        index = initial_start
        while index < initial_end:
            if index in completed:
                index += 1
                continue
            end = index + 1
            while end < initial_end and end not in completed:
                end += 1
            checkpoint = _process_range(
                index,
                end,
                parent_batch_id=(
                    _batch_id(initial_start, initial_end)
                    if (index, end) != (initial_start, initial_end)
                    else None
                ),
                context=context,
            )
            for offset, article in enumerate(checkpoint.articles, start=index):
                completed[offset] = article
            index = end


def _process_range(
    start: int,
    end: int,
    *,
    parent_batch_id: str | None,
    context: _RunContext,
) -> TaggerBatchCheckpoint:
    articles = context.input_articles[start:end]
    batch_id = _batch_id(start, end)
    errors: list[BatchResponseError] = []

    for _attempt in range(1, MAX_BATCH_ATTEMPTS + 1):
        client_failure: TaggerRunError | None = None
        _append_unique(
            context.attempted_models,
            getattr(context.llm_client, "preferred_model", None)
            or context.llm_client.model,
        )
        try:
            response = context.llm_client.tag(articles)
        except BatchResponseError as error:
            errors.append(error)
            continue
        except Exception as error:
            client_failure = _terminal_error(
                context,
                batch_id,
                articles,
                attempt_count=_attempt,
                category=_client_error_category(error),
            )
        if client_failure is not None:
            raise client_failure

        try:
            payload, response_model, generation = _unpack_response(
                response, context.llm_client
            )
            _append_unique(context.attempted_models, response_model)
            checkpoint_articles = _validate_batch_output(
                articles,
                payload,
                model=response_model,
            )
            if generation is not None:
                accept = getattr(generation, "accept", None)
                if callable(accept):
                    accept()
                if (
                    isinstance(context.llm_client, GeminiTaggerClient)
                    and response_model == context.llm_client.fallback_model
                ):
                    context.llm_client.preferred_model = response_model
            checkpoint = TaggerBatchCheckpoint(
                run_id=context.run_id,
                input_fingerprint=context.input_fingerprint,
                policy_fingerprint=context.policy_fingerprint,
                batch_id=batch_id,
                parent_batch_id=parent_batch_id,
                article_ids=[article.id for article in articles],
                model=response_model,
                generated_at=datetime.now(UTC),
                articles=checkpoint_articles,
            )
        except BatchResponseError as error:
            errors.append(error)
            continue
        except (ValidationError, ValueError):
            errors.append(
                BatchResponseError(
                    "invalid_schema",
                    "LLM response does not satisfy the checkpoint schema",
                )
            )
            continue
        except Exception:
            raise _terminal_error(
                context,
                batch_id,
                articles,
                attempt_count=_attempt,
                category="internal_error",
            ) from None

        try:
            _write_checkpoint(checkpoint, context.state_path)
        except OSError:
            raise _terminal_error(
                context,
                batch_id,
                articles,
                attempt_count=_attempt,
                category="checkpoint_io",
            ) from None
        return checkpoint

    if len(articles) > 1:
        midpoint = start + (end - start) // 2
        left = _process_range(
            start,
            midpoint,
            parent_batch_id=batch_id,
            context=context,
        )
        right = _process_range(
            midpoint,
            end,
            parent_batch_id=batch_id,
            context=context,
        )
        return _combine_child_checkpoints(left, right, batch_id, context)

    category = errors[-1].category if errors else "invalid_schema"
    terminal = _terminal_error(
        context,
        batch_id,
        articles,
        attempt_count=MAX_BATCH_ATTEMPTS,
        category=category,
    )
    if errors:
        raise terminal from errors[-1]
    raise terminal


def _combine_child_checkpoints(
    left: TaggerBatchCheckpoint,
    right: TaggerBatchCheckpoint,
    parent_batch_id: str,
    context: _RunContext,
) -> TaggerBatchCheckpoint:
    """Return an in-memory aggregate; child checkpoints remain the durable state."""

    return TaggerBatchCheckpoint(
        run_id=context.run_id,
        input_fingerprint=context.input_fingerprint,
        policy_fingerprint=context.policy_fingerprint,
        batch_id=parent_batch_id,
        article_ids=left.article_ids + right.article_ids,
        model=right.model,
        generated_at=max(left.generated_at, right.generated_at),
        articles=left.articles + right.articles,
    )


def _validate_batch_output(
    articles: Sequence[NormalizedArticle],
    payload: Mapping[str, Any],
    *,
    model: str,
) -> list[CheckpointArticle]:
    if set(payload) != {"articles"}:
        raise BatchResponseError(
            "invalid_schema",
            "LLM response contains unexpected root fields",
        )
    items = _extract_tag_items(payload)
    requested_ids = [article.id for article in articles]
    received_ids = [item["id"] for item in items]
    if received_ids != requested_ids and set(received_ids) != set(requested_ids):
        raise BatchResponseError(
            "article_id_mismatch",
            "LLM response article IDs do not match the requested batch",
        )
    if len(received_ids) != len(requested_ids):
        raise BatchResponseError(
            "article_id_mismatch",
            "LLM response must contain every requested ID exactly once",
        )

    by_id = {item["id"]: item for item in items}
    validated: list[CheckpointArticle] = []
    for article_id in requested_ids:
        item = dict(by_id[article_id])
        expected_fields = {
            "id",
            "tags",
            "technologies",
            "domains",
            "ai_metadata",
        }
        if set(item) != expected_fields:
            raise BatchResponseError(
                "invalid_schema",
                f"Article {article_id} contains unexpected or missing fields",
            )
        raw_metadata = item.get("ai_metadata")
        if not isinstance(raw_metadata, Mapping):
            raise BatchResponseError(
                "invalid_schema",
                f"Article {article_id} is missing ai_metadata",
            )
        metadata = dict(raw_metadata)
        if not {"confidence"} <= set(metadata) <= {"confidence", "rationale"}:
            raise BatchResponseError(
                "invalid_schema",
                f"Article {article_id} contains invalid ai_metadata fields",
            )
        metadata["model"] = model
        try:
            validated.append(
                CheckpointArticle(
                    id=article_id,
                    tags=_normalize_string_list(item.get("tags")),
                    technologies=_normalize_string_list(item.get("technologies")),
                    domains=_normalize_string_list(item.get("domains")),
                    ai_metadata=CheckpointAIMetadata.model_validate(metadata),
                )
            )
        except ValidationError:
            raise BatchResponseError(
                "invalid_schema",
                "LLM response does not satisfy the checkpoint article schema",
            ) from None
    return validated


def _load_checkpoints(context: _RunContext) -> list[TaggerBatchCheckpoint]:
    if not context.state_path.exists():
        return []
    checkpoints: list[TaggerBatchCheckpoint] = []
    for path in sorted(context.state_path.glob("*.json")):
        try:
            checkpoint = TaggerBatchCheckpoint.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as error:
            raise ValueError(f"Invalid Tagger checkpoint: {path.name}") from error
        if path.name != f"{checkpoint.batch_id}.json":
            raise ValueError(
                f"Checkpoint filename does not match batch_id: {path.name}"
            )
        if checkpoint.run_id != context.run_id:
            raise ValueError(f"Checkpoint run_id mismatch: {path.name}")
        if checkpoint.input_fingerprint != context.input_fingerprint:
            raise ValueError(f"Checkpoint input fingerprint mismatch: {path.name}")
        if checkpoint.policy_fingerprint != context.policy_fingerprint:
            raise ValueError(f"Checkpoint policy fingerprint mismatch: {path.name}")
        checkpoints.append(checkpoint)
    return checkpoints


def _index_completed_articles(
    checkpoints: Sequence[TaggerBatchCheckpoint],
    context: _RunContext,
) -> dict[int, CheckpointArticle]:
    input_ids = [article.id for article in context.input_articles]
    positions = {article_id: index for index, article_id in enumerate(input_ids)}
    if len(positions) != len(input_ids):
        raise ValueError("normalized_articles.json contains duplicate article IDs")

    completed: dict[int, CheckpointArticle] = {}
    ranges: list[tuple[int, int]] = []
    for checkpoint in checkpoints:
        start, end = _parse_batch_id(checkpoint.batch_id)
        if start >= end or end > len(input_ids):
            raise ValueError(
                "Checkpoint range is outside current input: "
                f"{checkpoint.batch_id}"
            )
        expected_ids = input_ids[start:end]
        result_ids = [article.id for article in checkpoint.articles]
        if checkpoint.article_ids != expected_ids or result_ids != expected_ids:
            raise ValueError(
                "Checkpoint article IDs do not match range: "
                f"{checkpoint.batch_id}"
            )
        if checkpoint.model != checkpoint.articles[0].ai_metadata.model or any(
            article.ai_metadata.model != checkpoint.model
            for article in checkpoint.articles
        ):
            raise ValueError(
                f"Checkpoint model metadata mismatch: {checkpoint.batch_id}"
            )
        overlaps = any(
            existing_start < end and start < existing_end
            for existing_start, existing_end in ranges
        )
        if overlaps:
            raise ValueError(
                f"Overlapping Tagger checkpoint range: {checkpoint.batch_id}"
            )
        ranges.append((start, end))
        for article in checkpoint.articles:
            position = positions.get(article.id)
            if position is None or position in completed:
                raise ValueError(f"Invalid checkpoint article ID: {article.id}")
            completed[position] = article
    return completed


def _finalize_document(
    input_articles: Sequence[NormalizedArticle],
    completed: Mapping[int, CheckpointArticle],
) -> TaggedArticlesDocument:
    if len(completed) != len(input_articles):
        raise ValueError("Tagger finalization requires exactly one result per article")
    tagged: list[TaggedArticle] = []
    for index, source in enumerate(input_articles):
        result = completed[index]
        if result.id != source.id:
            raise ValueError("Tagger checkpoint order does not match normalized input")
        tagged.append(
            TaggedArticle(
                id=source.id,
                source=source.source,
                title=source.title,
                canonical_url=source.canonical_url,
                summary=source.summary,
                published_at=source.published_at,
                tags=result.tags,
                technologies=result.technologies,
                domains=result.domains,
                ai_metadata=AIMetadata.model_validate(
                    result.ai_metadata.model_dump(mode="json", exclude_none=True)
                ),
            )
        )
    return TaggedArticlesDocument(
        generated_at=datetime.now(UTC),
        articles=tagged,
    )


def resolve_model(model: str | None = None) -> str:
    _load_dotenv()
    configured_model = (
        model
        or os.getenv(MODEL_ENV_VAR)
        or os.getenv(GEMINI_MODEL_ENV_VAR)
        or DEFAULT_MODEL
    ).strip()
    if not configured_model:
        raise ValueError(f"{MODEL_ENV_VAR} must not be empty when set")
    return configured_model


def _policy_fingerprint(*, model: str, fallback_model: str | None) -> str:
    resolved_fallback = fallback_model or DEFAULT_FALLBACK_MODEL
    policy = {
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "tagged_articles_schema_version": TAGGED_SCHEMA_VERSION,
        "normalized_articles_schema": _normalized_articles_schema(),
        "tagged_articles_schema": _tagged_articles_schema(),
        "prompt": PROMPT_PATH.read_text(encoding="utf-8"),
        "models": {
            "primary": model,
            "fallback": resolved_fallback,
        },
        "execution_policy_fingerprint": generation_policy_fingerprint(
            primary_model=model,
            fallback_model=resolved_fallback,
        ),
        "batch_size": TAGGER_BATCH_SIZE,
        "max_batch_attempts": MAX_BATCH_ATTEMPTS,
        "limits": {
            "tags": MAX_TAGS,
            "technologies": MAX_TECHNOLOGIES,
            "domains": MAX_DOMAINS,
            "rationale": MAX_RATIONALE_LENGTH,
            "output_tokens": MAX_OUTPUT_TOKENS,
        },
        "generation": {
            "temperature": GENERATION_TEMPERATURE,
            "response_mime_type": "application/json",
        },
        "response_schema": _tagger_response_schema(),
    }
    encoded = json.dumps(policy, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def tagger_policy_fingerprint(
    *, model: str | None = None, fallback_model: str | None = None
) -> str:
    """Return the complete Tagger policy identity used for checkpoint resume."""

    return _policy_fingerprint(
        model=resolve_model(model),
        fallback_model=fallback_model or resolve_fallback_model(),
    )


def _default_run_id(input_fingerprint: str, policy_fingerprint: str) -> str:
    return f"{input_fingerprint[:16]}-{policy_fingerprint[:16]}"


def _validate_run_id(run_id: str) -> None:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be a non-empty path segment")


def _terminal_error(
    context: _RunContext,
    batch_id: str,
    articles: Sequence[NormalizedArticle],
    *,
    attempt_count: int,
    category: str,
) -> TaggerRunError:
    return TaggerRunError(
        run_id=context.run_id,
        batch_id=batch_id,
        article_ids=[article.id for article in articles],
        models=_actual_attempted_models(context),
        attempt_count=attempt_count,
        category=category,
    )


def _actual_attempted_models(context: _RunContext) -> list[str]:
    models = list(context.attempted_models)
    for model in getattr(context.llm_client, "attempted_models", ()):
        _append_unique(models, model)
    return models


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _client_error_category(error: Exception) -> str:
    status_codes: set[int] = set()
    for current in _exception_chain(error):
        status_values = (
            getattr(current, "code", None),
            getattr(current, "status_code", None),
            getattr(getattr(current, "response", None), "status_code", None),
        )
        for value in status_values:
            try:
                status_codes.add(int(value))
            except (TypeError, ValueError):
                continue
    if status_codes & {429, 500, 502, 503, 504}:
        return "transient_api"
    if status_codes & {401, 403}:
        return "authentication"
    if status_codes:
        return "api_error"
    return "client_error"


def _exception_chain(error: Exception) -> list[BaseException]:
    chain: list[BaseException] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return chain


@lru_cache(maxsize=2)
def _contract_schema(filename: str) -> dict[str, Any]:
    schema_text = (
        files("dtns.contracts")
        .joinpath(filename)
        .read_text(encoding="utf-8")
    )
    return json.loads(schema_text)


def _normalized_articles_schema() -> dict[str, Any]:
    return _contract_schema("normalized_articles.schema.json")


def _tagged_articles_schema() -> dict[str, Any]:
    return _contract_schema("tagged_articles.schema.json")


@lru_cache(maxsize=1)
def _normalized_articles_validator() -> Draft202012Validator:
    schema = _normalized_articles_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@lru_cache(maxsize=1)
def _tagged_articles_validator() -> Draft202012Validator:
    schema = _tagged_articles_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_normalized_articles_schema(payload: Any) -> None:
    if not _normalized_articles_validator().is_valid(payload):
        raise ValueError("Tagger input violates normalized_articles JSON Schema")


def _validate_tagged_articles_schema(payload: Mapping[str, Any]) -> None:
    if not _tagged_articles_validator().is_valid(payload):
        raise ValueError("Final Tagger output violates tagged_articles JSON Schema")


def _restore_model_preference(
    client: LLMClient,
    checkpoints: Sequence[TaggerBatchCheckpoint],
) -> None:
    if not isinstance(client, GeminiTaggerClient):
        return
    if any(checkpoint.model == client.fallback_model for checkpoint in checkpoints):
        client.preferred_model = client.fallback_model


def _unpack_response(
    response: Mapping[str, Any] | TaggerResponse,
    client: LLMClient,
) -> tuple[Mapping[str, Any], str, GenerationResult | None]:
    if isinstance(response, TaggerResponse):
        return response.payload, response.model, response.generation
    if not isinstance(response, Mapping):
        raise BatchResponseError("invalid_schema", "LLM response must be an object")
    return response, client.model, None


def _extract_tag_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    articles = payload.get("articles")
    if not isinstance(articles, list) or not all(
        isinstance(item, Mapping) for item in articles
    ):
        raise BatchResponseError(
            "invalid_schema",
            "LLM response must contain an articles array of objects",
        )
    ids: list[str] = []
    for item in articles:
        article_id = item.get("id")
        if not isinstance(article_id, str) or not article_id:
            raise BatchResponseError(
                "invalid_schema",
                "LLM response article IDs must be non-empty strings",
            )
        ids.append(article_id)
    if len(ids) != len(set(ids)):
        raise BatchResponseError(
            "article_id_mismatch",
            "LLM response contains duplicate article IDs",
        )
    return articles


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise BatchResponseError("invalid_schema", "Tag fields must be string arrays")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise BatchResponseError("invalid_schema", "Tag values must be strings")
        text = re.sub(r"\s+", " ", item).strip()
        if not text:
            raise BatchResponseError("invalid_schema", "Tag values must not be empty")
        key = text.casefold()
        if key in seen:
            raise BatchResponseError("invalid_schema", "Tag values must be unique")
        seen.add(key)
        normalized.append(text)
    return normalized


def _reject_truncated_response(response: Any) -> None:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return
    reason = getattr(candidates[0], "finish_reason", None)
    reason_name = getattr(reason, "name", str(reason)).upper()
    if "MAX_TOKENS" in reason_name or "LENGTH" in reason_name:
        raise BatchResponseError("max_tokens", "LLM response was truncated")


def _parse_json_object(content: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise BatchResponseError("invalid_json", "LLM returned invalid JSON") from error
    if not isinstance(parsed, Mapping):
        raise BatchResponseError("invalid_json", "LLM response must be a JSON object")
    return parsed


def _tagger_response_schema() -> dict[str, Any]:
    string_array = {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "uniqueItems": True,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["articles"],
        "properties": {
            "articles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "tags",
                        "technologies",
                        "domains",
                        "ai_metadata",
                    ],
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "tags": {**string_array, "maxItems": MAX_TAGS},
                        "technologies": {
                            **string_array,
                            "maxItems": MAX_TECHNOLOGIES,
                        },
                        "domains": {**string_array, "maxItems": MAX_DOMAINS},
                        "ai_metadata": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["confidence"],
                            "properties": {
                                "confidence": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 1,
                                },
                                "rationale": {
                                    "type": ["string", "null"],
                                    "maxLength": MAX_RATIONALE_LENGTH,
                                },
                            },
                        },
                    },
                },
            }
        },
    }


def _batch_id(start: int, end: int) -> str:
    return f"articles-{start:06d}-{end:06d}"


def _parse_batch_id(batch_id: str) -> tuple[int, int]:
    match = re.fullmatch(r"articles-(\d{6})-(\d{6})", batch_id)
    if match is None:
        raise ValueError(f"Invalid checkpoint batch_id: {batch_id}")
    return int(match.group(1)), int(match.group(2))


def _write_checkpoint(
    checkpoint: TaggerBatchCheckpoint,
    state_path: Path,
) -> None:
    _atomic_write_json(
        state_path / f"{checkpoint.batch_id}.json",
        checkpoint.model_dump(mode="json", exclude_none=True),
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary_path: Path | None = None
    backup_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)

        if path.exists():
            backup_path = path.with_name(
                f".{path.name}.{uuid.uuid4().hex}.rollback"
            )
            os.link(path, backup_path)
        _fsync_directory(path.parent)

        os.replace(temporary_path, path)
        temporary_path = None
        try:
            _fsync_directory(path.parent)
        except OSError:
            rollback_path = backup_path
            backup_path = None
            _rollback_atomic_replace(path, rollback_path)
            raise
    finally:
        if temporary_path is not None:
            _remove_file(temporary_path)
        if backup_path is not None:
            _remove_file(backup_path)
            try:
                _fsync_directory(path.parent)
            except OSError:
                logger.warning("Rollback backup cleanup fsync failed")


def _rollback_atomic_replace(path: Path, backup_path: Path | None) -> None:
    try:
        if backup_path is None:
            path.unlink(missing_ok=True)
        else:
            os.replace(backup_path, path)
        _fsync_directory(path.parent)
    except OSError:
        raise OSError("Atomic write failed and rollback could not be confirmed") from None


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        try:
            os.close(directory_fd)
        except OSError:
            logger.warning("Directory descriptor close failed after fsync")


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to remove atomic-write artifact %s", path.name)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is project-level.
        return
    load_dotenv()
