"""Shared Gemini generation policy for AI agents."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dtns.agents.execution_state import AIExecutionState, AIExecutionStateStore


DEFAULT_FALLBACK_MODEL = "gemini-3.1-flash-lite"
FALLBACK_MODEL_ENV_VAR = "GEMINI_FALLBACK_MODEL"
RETRYABLE_STATUS_CODES = {429, *range(500, 600)}
MAX_ATTEMPTS_PER_MODEL = 2
logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """A model response whose success is committed after content validation."""

    response: Any
    model: str
    _accept_callback: Callable[[], None] | None = field(default=None, repr=False)
    _accepted: bool = field(default=False, init=False, repr=False)

    def accept(self) -> None:
        """Record this response as accepted after caller-side validation."""

        if self._accepted:
            return
        if self._accept_callback is not None:
            self._accept_callback()
        self._accepted = True


def generate_content_with_fallback(
    *,
    primary_model: str,
    contents: Any,
    config: Any,
    fallback_model: str | None = None,
    client: Any | None = None,
    run_id: str | None = None,
    execution_state_path: Path | str | None = None,
    policy_primary_model: str | None = None,
    use_requested_model_when_closed: bool = False,
) -> GenerationResult:
    """Generate content, falling back only after transient API failures."""

    fallback_model = fallback_model or resolve_fallback_model()
    canonical_primary_model = policy_primary_model or primary_model
    state_store: AIExecutionStateStore | None = None
    state: AIExecutionState | None = None
    if (run_id is None) != (execution_state_path is None):
        raise ValueError(
            "run_id and execution_state_path must be supplied together"
        )
    if run_id is not None and execution_state_path is not None:
        state_store = AIExecutionStateStore(
            execution_state_path,
            run_id=run_id,
            policy_fingerprint=generation_policy_fingerprint(
                primary_model=canonical_primary_model,
                fallback_model=fallback_model,
            ),
            primary_model=canonical_primary_model,
            fallback_model=fallback_model,
        )
        state = state_store.load()

    preferred_model = (
        state.preferred_model
        if state is not None and state.circuit_state == "open"
        else (
            primary_model
            if state is None or use_requested_model_when_closed
            else canonical_primary_model
        )
    )
    models = [preferred_model]
    if (
        preferred_model == canonical_primary_model
        and fallback_model
        and fallback_model != canonical_primary_model
    ):
        models.append(fallback_model)

    owns_client = client is None
    if client is None:
        from google import genai

        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    last_error: Exception | None = None
    primary_exhausted = False
    try:
        for model in models:
            for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    )
                except Exception as error:
                    if not is_retryable_api_error(error):
                        _record_observed_outcome(state_store, state)
                        raise
                    last_error = error
                    if state_store is not None and state is not None:
                        state = state.model_copy(
                            update={
                                "primary_failures": state.primary_failures
                                + int(model == canonical_primary_model),
                                "updated_at": datetime.now(UTC),
                            }
                        )
                        state_store.save(state)
                    logger.warning(
                        "Gemini model %s returned a transient error "
                        "(attempt %d/%d).",
                        model,
                        attempt,
                        MAX_ATTEMPTS_PER_MODEL,
                    )
                    if attempt < MAX_ATTEMPTS_PER_MODEL:
                        time.sleep(2**attempt)
                    continue
                accept_callback = None
                if state_store is not None and state is not None:
                    accept_callback = _accepted_outcome_callback(
                        store=state_store,
                        state=state,
                        model=model,
                        fallback_model=fallback_model,
                        primary_exhausted=primary_exhausted,
                    )
                return GenerationResult(
                    response=response,
                    model=model,
                    _accept_callback=accept_callback,
                )
            if model == canonical_primary_model:
                primary_exhausted = True
            if model != models[-1]:
                logger.warning("Falling back from %s to %s.", model, models[-1])
    finally:
        if owns_client:
            client.close()

    raise RuntimeError(
        "Gemini primary and fallback models failed after transient API errors."
    ) from last_error


def resolve_fallback_model() -> str:
    return os.getenv(FALLBACK_MODEL_ENV_VAR) or DEFAULT_FALLBACK_MODEL


def generation_policy_fingerprint(
    *, primary_model: str, fallback_model: str | None = None
) -> str:
    """Fingerprint the shared model selection and retry policy."""

    policy = {
        "schema_version": "1.0",
        "primary_model": primary_model,
        "fallback_model": fallback_model or resolve_fallback_model(),
        "max_attempts_per_model": MAX_ATTEMPTS_PER_MODEL,
        "retryable_status_codes": sorted(RETRYABLE_STATUS_CODES),
    }
    return hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _record_observed_outcome(
    store: AIExecutionStateStore | None,
    state: AIExecutionState | None,
) -> None:
    if store is None or state is None:
        return
    store.save(state.model_copy(update={"updated_at": datetime.now(UTC)}))


def _accepted_outcome_callback(
    *,
    store: AIExecutionStateStore,
    state: AIExecutionState,
    model: str,
    fallback_model: str,
    primary_exhausted: bool,
) -> Callable[[], None]:
    def accept() -> None:
        now = datetime.now(UTC)
        update = {
            "updated_at": now,
            "fallback_successes": state.fallback_successes
            + int(model == fallback_model),
        }
        if model == fallback_model and primary_exhausted:
            update.update(
                circuit_state="open",
                preferred_model=fallback_model,
                opened_at=now,
                open_reason="transient_api_exhausted",
            )
        store.save(AIExecutionState.model_validate(state.model_copy(update=update)))

    return accept


def is_retryable_api_error(error: Exception) -> bool:
    status_codes = (
        getattr(error, "code", None),
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    )
    for code in status_codes:
        try:
            if int(code) in RETRYABLE_STATUS_CODES:
                return True
        except (TypeError, ValueError):
            continue
    return False
