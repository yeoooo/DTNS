"""Deterministic article preprocessing.

The preprocessor reads the Collector's ``articles.json`` contract and writes
``normalized_articles.json``. It does not use AI and does not classify content.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


ARTICLES_FILENAME = "articles.json"
NORMALIZED_ARTICLES_FILENAME = "normalized_articles.json"
SCHEMA_VERSION = "1.0"
PREPROCESSOR_POLICY_VERSION = "1"

SourceType = Literal["rss", "atom", "github_release", "api", "html"]

TRACKING_QUERY_KEYS = {
    "_hsenc",
    "_hsmi",
    "fbclid",
    "gclid",
    "gbraid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "oly_anon_id",
    "oly_enc_id",
    "vero_id",
    "wbraid",
}

IGNORED_QUERY_PREFIXES = ("utm_",)
HTTP_SCHEMES = {"http", "https"}
WHITESPACE_RE = re.compile(r"\s+")


class ArtifactValidationError(ValueError):
    """A sanitized persisted-artifact contract validation failure."""


class RawArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_type: SourceType | None = None
    title: str
    url: str
    summary: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    collected_at: datetime
    raw: dict[str, Any] | None = None

    @field_validator("source", "title", "url")
    @classmethod
    def require_non_empty_string(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value


class RawArticlesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    generated_at: datetime
    source_run_id: str | None = None
    articles: list[dict[str, Any]] = Field(default_factory=list)


class NormalizedArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    title: str
    canonical_url: str
    published_at: datetime | None
    collected_at: datetime
    source_type: SourceType | None = None
    original_url: str | None = None
    summary: str | None = None
    author: str | None = None
    language: str | None = None


class NormalizedArticlesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    generated_at: datetime
    articles: list[NormalizedArticle]


def preprocess(
    input_path: Path | str,
    output_path: Path | str,
) -> NormalizedArticlesFile:
    """Read raw articles, normalize them, and write the preprocessor contract."""

    input_path = Path(input_path)
    output_path = Path(output_path)

    try:
        raw_articles = RawArticlesFile.model_validate_json(input_path.read_bytes())
    except FileNotFoundError:
        raise FileNotFoundError(f"Input file not found: {input_path}") from None
    except ValidationError as error:
        raise _artifact_validation_error(
            input_path,
            contract_name=RawArticlesFile.__name__,
            error=error,
        ) from None
    normalized_articles = normalize_articles(raw_articles.articles)
    output = NormalizedArticlesFile(
        generated_at=datetime.now(UTC),
        articles=normalized_articles,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            _normalized_output_payload(output),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def normalize_articles(
    articles: list[RawArticle | dict[str, Any]],
) -> list[NormalizedArticle]:
    """Normalize, validate, and deduplicate raw article records."""

    normalized: list[NormalizedArticle] = []
    seen_urls: set[str] = set()

    for raw_article in articles:
        try:
            article = (
                raw_article
                if isinstance(raw_article, RawArticle)
                else RawArticle.model_validate(raw_article)
            )
        except ValidationError:
            continue

        normalized_article = normalize_article(article)
        if normalized_article is None:
            continue

        dedupe_key = normalized_article.canonical_url
        if dedupe_key in seen_urls:
            continue

        seen_urls.add(dedupe_key)
        normalized.append(normalized_article)

    return normalized


def normalize_article(article: RawArticle) -> NormalizedArticle | None:
    """Normalize one raw article. Return ``None`` when it is invalid."""

    title = normalize_title(article.title)
    source = normalize_text(article.source)
    canonical_url = canonicalize_url(article.url)

    if not title or not source or canonical_url is None:
        return None

    summary = normalize_text(article.summary)
    author = normalize_text(article.author)

    return NormalizedArticle(
        id=stable_article_id(canonical_url),
        source=source,
        source_type=article.source_type,
        title=title,
        canonical_url=canonical_url,
        original_url=article.url,
        summary=summary,
        author=author,
        published_at=article.published_at,
        collected_at=article.collected_at,
    )


def normalize_title(title: str) -> str:
    """Decode HTML entities and collapse whitespace in article titles."""

    return normalize_text(html.unescape(title)) or ""


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = WHITESPACE_RE.sub(" ", html.unescape(value)).strip()
    return normalized or None


def canonicalize_url(url: str) -> str | None:
    """Return a deterministic canonical HTTP(S) URL or ``None`` if invalid."""

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None

    scheme = parts.scheme.lower()
    if scheme not in HTTP_SCHEMES or not parts.netloc:
        return None

    username = parts.username
    password = parts.password
    hostname = parts.hostname
    if hostname is None:
        return None

    netloc = hostname.lower().rstrip(".")
    port = parts.port
    if username or password:
        return None
    if port and not _is_default_port(scheme, port):
        netloc = f"{netloc}:{port}"

    path = _normalize_path(parts.path)
    query = _normalize_query(parts.query)

    return urlunsplit((scheme, netloc, path, query, ""))


def stable_article_id(canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    return f"article_{digest[:16]}"


def preprocessor_policy_fingerprint() -> str:
    """Return the normalization and contract policy identity."""

    policy = {
        "policy_version": PREPROCESSOR_POLICY_VERSION,
        "schema_version": SCHEMA_VERSION,
        "input_schema": RawArticlesFile.model_json_schema(),
        "output_schema": NormalizedArticlesFile.model_json_schema(),
        "http_schemes": sorted(HTTP_SCHEMES),
        "tracking_query_keys": sorted(TRACKING_QUERY_KEYS),
        "ignored_query_prefixes": list(IGNORED_QUERY_PREFIXES),
        "whitespace_pattern": WHITESPACE_RE.pattern,
        "rules": {
            "decode_html_entities": True,
            "reject_url_credentials": True,
            "remove_default_ports": True,
            "remove_fragments": True,
            "remove_trailing_slashes": True,
            "sort_query_pairs": True,
            "stable_id": "sha256-canonical-url-prefix-16",
            "deduplicate_by": "canonical_url",
        },
    }
    encoded = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_path(path: str) -> str:
    if not path:
        return ""
    decoded = unquote(path)
    quoted = quote(decoded, safe="/:@-._~!$&'()*+,;=")
    if quoted != "/" and quoted.endswith("/"):
        return quoted.rstrip("/")
    return quoted


def _normalize_query(query: str) -> str:
    if not query:
        return ""

    pairs = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in TRACKING_QUERY_KEYS:
            continue
        if any(lower_key.startswith(prefix) for prefix in IGNORED_QUERY_PREFIXES):
            continue
        pairs.append((key, value))

    pairs.sort(key=lambda item: (item[0], item[1]))
    return urlencode(pairs, doseq=True)


def _is_default_port(scheme: str, port: int) -> bool:
    return (scheme == "http" and port == 80) or (scheme == "https" and port == 443)


def _artifact_validation_error(
    path: Path,
    *,
    contract_name: str,
    error: ValidationError,
) -> ArtifactValidationError:
    failures = []
    for detail in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = _format_error_location(detail["loc"])
        failures.append(f"{location} ({detail['type']})")

    joined_failures = ", ".join(failures) or "<document> (validation_error)"
    return ArtifactValidationError(
        f"Artifact validation failed: path={path}; contract={contract_name}; "
        f"fields={joined_failures}"
    )


def _format_error_location(location: tuple[int | str, ...]) -> str:
    formatted = ""
    for part in location:
        if isinstance(part, int):
            formatted += f"[{part}]"
        elif formatted:
            formatted += f".{part}"
        else:
            formatted = part
    return formatted or "<document>"


def _normalized_output_payload(output: NormalizedArticlesFile) -> dict[str, Any]:
    payload = output.model_dump(mode="json", exclude_none=True)
    for index, article in enumerate(output.articles):
        if article.published_at is None:
            payload["articles"][index]["published_at"] = None
    return payload


__all__ = [
    "ARTICLES_FILENAME",
    "ArtifactValidationError",
    "NORMALIZED_ARTICLES_FILENAME",
    "NormalizedArticle",
    "NormalizedArticlesFile",
    "RawArticle",
    "RawArticlesFile",
    "ValidationError",
    "canonicalize_url",
    "normalize_article",
    "normalize_articles",
    "normalize_title",
    "preprocess",
    "stable_article_id",
]
