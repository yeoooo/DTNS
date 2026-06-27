"""Shared Gemini generation policy for AI agents."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any


DEFAULT_FALLBACK_MODEL = "gemini-3.1-flash-lite"
FALLBACK_MODEL_ENV_VAR = "GEMINI_FALLBACK_MODEL"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS_PER_MODEL = 2
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationResult:
    response: Any
    model: str


def generate_content_with_fallback(
    *,
    primary_model: str,
    contents: Any,
    config: Any,
    fallback_model: str | None = None,
    client: Any | None = None,
) -> GenerationResult:
    """Generate content, falling back only after transient API failures."""

    fallback_model = fallback_model or resolve_fallback_model()
    models = [primary_model]
    if fallback_model and fallback_model != primary_model:
        models.append(fallback_model)

    owns_client = client is None
    if client is None:
        from google import genai

        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    last_error: Exception | None = None
    try:
        for model in models:
            for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    )
                    return GenerationResult(response=response, model=model)
                except Exception as error:
                    if not is_retryable_api_error(error):
                        raise
                    last_error = error
                    logger.warning(
                        "Gemini model %s returned a transient error "
                        "(attempt %d/%d).",
                        model,
                        attempt,
                        MAX_ATTEMPTS_PER_MODEL,
                    )
                    if attempt < MAX_ATTEMPTS_PER_MODEL:
                        time.sleep(2**attempt)
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


def is_retryable_api_error(error: Exception) -> bool:
    status_codes = (
        getattr(error, "code", None),
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    )
    return any(code in RETRYABLE_STATUS_CODES for code in status_codes)
