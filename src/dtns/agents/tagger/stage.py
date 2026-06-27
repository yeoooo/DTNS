"""AI-assisted article tagging stage.

The tagger reads ``normalized_articles.json`` and writes ``tagged_articles.json``.
It enriches articles with technical tags, technologies, engineering domains, and
LLM metadata only. It does not classify articles into newsletter topics.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from dtns.contracts.tagged_articles import (
    AIMetadata,
    NormalizedArticle,
    NormalizedArticlesDocument,
    SCHEMA_VERSION,
    TaggedArticle,
    TaggedArticlesDocument,
)


NORMALIZED_ARTICLES_FILENAME = "normalized_articles.json"
TAGGED_ARTICLES_FILENAME = "tagged_articles.json"
DEFAULT_MODEL = "gemini-3.5-flash"
MODEL_ENV_VAR = "DTNS_TAGGER_MODEL"
GEMINI_MODEL_ENV_VAR = "GEMINI_MODEL"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "tagger.md"
MAX_ITEMS_PER_FIELD = 12


class LLMClient(Protocol):
    model: str

    def tag(self, articles: Sequence[NormalizedArticle]) -> Mapping[str, Any]:
        """Return JSON-compatible tag data for the supplied articles."""


@dataclass(frozen=True)
class GeminiTaggerClient:
    """Gemini-backed tagger client using the configured model."""

    model: str
    prompt_path: Path = PROMPT_PATH

    def tag(self, articles: Sequence[NormalizedArticle]) -> Mapping[str, Any]:
        _load_dotenv()
        try:
            from google import genai
        except ImportError as error:  # pragma: no cover - dependency is project-level.
            raise RuntimeError(
                "The 'google-genai' package is required for tagger LLM calls."
            ) from error

        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model=self.model,
            contents=[
                self.prompt_path.read_text(encoding="utf-8"),
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "instructions": [
                            "Return JSON only.",
                            "Do not classify articles into newsletter topics.",
                            (
                                "Identify frameworks and programming languages "
                                "as technologies."
                            ),
                            (
                                "Use domains as broad engineering domains, "
                                "not topic labels."
                            ),
                        ],
                        "articles": [
                            article.model_dump(mode="json", exclude_none=True)
                            for article in articles
                        ],
                    },
                    ensure_ascii=False,
                ),
            ],
            config={"temperature": 0.1, "response_mime_type": "application/json"},
        )
        content = getattr(response, "text", None)
        if not content:
            raise ValueError("LLM returned an empty tagger response.")
        return _parse_json_object(content)


def tag_articles(
    input_path: Path | str,
    output_path: Path | str,
    *,
    llm_client: LLMClient | None = None,
    model: str | None = None,
) -> TaggedArticlesDocument:
    """Read normalized articles, call the configured LLM, and write tagged JSON."""

    input_path = Path(input_path)
    output_path = Path(output_path)
    document = NormalizedArticlesDocument.model_validate(_read_json(input_path))
    llm_client = llm_client or GeminiTaggerClient(model=resolve_model(model))

    llm_payload = llm_client.tag(document.articles)
    tagged_articles = merge_tagger_output(
        document.articles,
        llm_payload,
        model=llm_client.model,
    )
    output = TaggedArticlesDocument(
        generated_at=datetime.now(UTC),
        articles=tagged_articles,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            output.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def merge_tagger_output(
    articles: Sequence[NormalizedArticle],
    llm_payload: Mapping[str, Any],
    *,
    model: str,
) -> list[TaggedArticle]:
    """Validate and attach LLM tag data while preserving the input article order."""

    tag_items = _extract_tag_items(llm_payload)
    tags_by_id = {str(item.get("id", "")): item for item in tag_items}
    missing_ids = [article.id for article in articles if article.id not in tags_by_id]
    if missing_ids:
        raise ValueError(f"LLM response omitted article IDs: {', '.join(missing_ids)}")

    tagged_articles: list[TaggedArticle] = []
    for article in articles:
        item = tags_by_id[article.id]
        ai_metadata = _coerce_ai_metadata(item.get("ai_metadata"), model=model)
        tagged_articles.append(
            TaggedArticle(
                id=article.id,
                source=article.source,
                title=article.title,
                canonical_url=article.canonical_url,
                summary=article.summary,
                published_at=article.published_at,
                tags=normalize_string_list(item.get("tags")),
                technologies=normalize_string_list(item.get("technologies")),
                domains=normalize_string_list(item.get("domains")),
                ai_metadata=ai_metadata,
            )
        )
    return tagged_articles


def resolve_model(model: str | None = None) -> str:
    _load_dotenv()
    configured_model = (
        model
        or os.getenv(MODEL_ENV_VAR)
        or os.getenv(GEMINI_MODEL_ENV_VAR)
        or DEFAULT_MODEL
    )
    configured_model = configured_model.strip()
    if not configured_model:
        raise ValueError(f"{MODEL_ENV_VAR} must not be empty when set.")
    return configured_model


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is project-level.
        return
    load_dotenv()


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        raise ValueError("LLM tag fields must be arrays of strings.")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = normalize_text(str(item))
        if text is None:
            continue
        dedupe_key = text.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(text)
        if len(normalized) >= MAX_ITEMS_PER_FIELD:
            break
    return normalized


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized or None


def _coerce_ai_metadata(value: Any, *, model: str) -> AIMetadata:
    if not isinstance(value, Mapping):
        raise ValueError("LLM response must include ai_metadata for each article.")

    metadata = dict(value)
    metadata["model"] = normalize_text(str(metadata.get("model") or model)) or model
    return AIMetadata.model_validate(metadata)


def _extract_tag_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    articles = payload.get("articles", payload.get("tagged_articles"))
    if not isinstance(articles, list):
        raise ValueError("LLM response must contain an 'articles' array.")
    if not all(isinstance(item, Mapping) for item in articles):
        raise ValueError("Every LLM article tag item must be an object.")
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for item in articles:
        article_id = str(item.get("id", ""))
        if article_id in seen_ids:
            duplicate_ids.add(article_id)
        seen_ids.add(article_id)
    if duplicate_ids:
        raise ValueError(
            "LLM response included duplicate article IDs: "
            + ", ".join(sorted(duplicate_ids))
        )
    return articles


def _parse_json_object(content: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("LLM tagger response was not valid JSON.") from error
    if isinstance(parsed, list):
        return {"articles": parsed}
    if not isinstance(parsed, Mapping):
        raise ValueError("LLM tagger response must be a JSON object.")
    return parsed


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}") from error
