"""AI-backed Editor Agent.

The Editor Agent reads topic trend JSON and writes a Korean Markdown
newsletter. It does not publish and must not invent facts beyond the supplied
trend and article metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from dtns.agents.editor.checkpoint import EditorGenerationCheckpoint
from dtns.agents.execution_state import execution_state_path
from dtns.agents.gemini import (
    DEFAULT_FALLBACK_MODEL,
    GenerationResult,
    generate_content_with_fallback,
    generation_policy_fingerprint,
    resolve_fallback_model,
)


TOPIC_TRENDS_FILENAME = "topic_trends.json"
TOPIC_ARTICLES_FILENAME = "topic_articles.json"
NEWSLETTER_FILENAME = "newsletter.md"
SCHEMA_VERSION = "1.0"
DEFAULT_MODEL = "gemini-3.5-flash"
MODEL_ENV_VAR = "DTNS_EDITOR_MODEL"
GEMINI_MODEL_ENV_VAR = "GEMINI_MODEL"
FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*|\s*```\s*$", re.IGNORECASE)
HORIZONTAL_RULE_RE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)
LEVEL_FOUR_HEADING_RE = re.compile(r"^####\s+(.+?)\s*$", re.MULTILINE)
DISCORD_DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
STATE_DIRECTORY = Path(".state") / "editor"
MAX_TRENDS = 8
MAX_CHARACTERS = 12000
MAX_CONTENT_ATTEMPTS = 2
MAX_OUTPUT_TOKENS = 16384
GENERATION_TEMPERATURE = 0.4
VALIDATED_SECTIONS = ["title", "summary", "trends", "insights"]
MIN_KOREAN_BODY_CHARACTERS = 10
MIN_KOREAN_BODY_RATIO = 0.2
HANGUL_RE = re.compile(r"[가-힣]")
LATIN_RE = re.compile(r"[A-Za-z]")
ATX_HEADING_RE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
BOLD_LABEL_RE = re.compile(r"^\s*\*\*[^*]+\*\*\s*$", re.MULTILINE)
URL_START_RE = re.compile(r"https?://", re.IGNORECASE)
PROHIBITED_PROSE_RE = re.compile(
    r"https?://|\]\s*\(|<a\s|<https?://", re.IGNORECASE
)
HTML_TAG_RE = re.compile(r"<[^>]*>")
MARKDOWN_BLOCK_RE = re.compile(r"^\s*(?:#{1,6}\s|[-+*>]\s|\d+[.)]\s)")
INLINE_MARKDOWN_RE = re.compile(r"[*_~`]")
HORIZONTAL_RULE_PROSE_RE = re.compile(r"^\s*-{3,}\s*$")
REFERENCE_LINK_RE = re.compile(r"!?\[[^\]\r\n]+\]\[[^\]\r\n]*\]")
PLAIN_HEADING_PATTERN = (
    r"^(?!\s)(?!.*\s$)(?:(?![\r\n#]|https?://|\]\s*\(|<[^>]*>)[\s\S])+$"
)
FRONT_MATTER_RE = re.compile(r"\A\s*---\s*\n.*?\n---\s*(?:\n|\Z)", re.DOTALL)
SECTION_PATTERNS = {
    "title": re.compile(r"^#\s+\S+", re.MULTILINE),
    "summary": re.compile(r"^##\s+.*핵심\s*요약\s*$", re.MULTILINE),
    "trends": re.compile(r"^##\s+.*주요\s*트렌드\s*$", re.MULTILINE),
    "insights": re.compile(r"^##\s+.*이번\s*주\s*인사이트\s*$", re.MULTILINE),
}
URL_ADAPTER = TypeAdapter(AnyUrl)


class TrendPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    start: date
    end: date


class Trend(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    title: str
    importance: Literal["high", "medium", "low"]
    summary: str
    why_it_matters: str
    article_ids: list[str] = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)

    @field_validator("id", "title", "summary", "why_it_matters")
    @classmethod
    def require_non_empty_string(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("keywords")
    @classmethod
    def require_unique_keywords(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("keywords must be unique")
        return value


class TrendsFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"]
    generated_at: datetime
    topic: Literal["technology", "backend", "qa"]
    period: TrendPeriod | None = None
    trends: list[Trend] = Field(default_factory=list, max_length=MAX_TRENDS)

    @field_validator("topic")
    @classmethod
    def require_topic(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, "generated_at")

    @model_validator(mode="after")
    def reject_null_period(self) -> TrendsFile:
        if "period" in self.model_fields_set and self.period is None:
            raise ValueError("period must be omitted instead of null")
        return self


class AIMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model: str
    confidence: float = Field(ge=0, le=1)
    rationale: str | None = None

    @field_validator("rationale", mode="before")
    @classmethod
    def reject_null_rationale(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("rationale must be omitted instead of null")
        return value


class ClassificationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    matched_rules: list[str] = Field(default_factory=list)
    score: float | None = Field(default=None, ge=0)

    @field_validator("score", mode="before")
    @classmethod
    def reject_null_score(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("score must be omitted instead of null")
        return value


class TopicArticle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

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
    def require_non_empty_string(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("canonical_url")
    @classmethod
    def require_uri(cls, value: str) -> str:
        URL_ADAPTER.validate_python(value)
        return value

    @field_validator("tags", "technologies", "domains")
    @classmethod
    def require_unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value

    @field_validator("published_at")
    @classmethod
    def require_published_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            _require_timezone(value, "published_at")
        return value

    @field_validator("summary", mode="before")
    @classmethod
    def reject_null_summary(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("summary must be omitted instead of null")
        return value


class TopicArticlesFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"]
    generated_at: datetime
    topic: Literal["technology", "backend", "qa"]
    articles: list[TopicArticle] = Field(default_factory=list)

    @field_validator("topic")
    @classmethod
    def require_topic(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, "generated_at")


class DraftTrendSection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    trend_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    heading: str = Field(
        min_length=1,
        max_length=160,
        json_schema_extra={"pattern": PLAIN_HEADING_PATTERN},
    )
    overview: str = Field(min_length=1, max_length=500)
    why_it_matters: str = Field(min_length=1, max_length=500)
    article_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("heading")
    @classmethod
    def validate_heading(cls, value: str) -> str:
        return _reject_plain_heading(value)

    @field_validator("overview", "why_it_matters")
    @classmethod
    def reject_presentation_syntax(cls, value: str) -> str:
        return _reject_plain_text(value)

    @field_validator("article_ids")
    @classmethod
    def validate_article_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("article IDs must be unique")
        if any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", value)
            for value in values
        ):
            raise ValueError("article IDs have an invalid format")
        return values


class EditorDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    topic: Literal["technology", "backend", "qa"]
    generated_at: datetime
    title: str = Field(
        min_length=1,
        max_length=160,
        json_schema_extra={"pattern": PLAIN_HEADING_PATTERN},
    )
    summary_items: list[str] = Field(min_length=1, max_length=5)
    trend_sections: list[DraftTrendSection] = Field(min_length=1, max_length=8)
    insight_items: list[str] = Field(min_length=1, max_length=5)

    @field_validator("generated_at")
    @classmethod
    def require_generated_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, "generated_at")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _reject_plain_heading(value)

    @field_validator("summary_items", "insight_items")
    @classmethod
    def validate_prose_items(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value or len(value) > 500:
                raise ValueError("prose items must contain 1 to 500 characters")
            _reject_plain_text(value)
        return values


class EditorContentError(ValueError):
    """A recoverable model-content failure."""


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return value


def _reject_plain_text(value: str) -> str:
    if value != value.strip():
        raise ValueError("prose must not have leading or trailing whitespace")
    if "\r" in value or "\n" in value:
        raise ValueError("prose must be a single line")
    if PROHIBITED_PROSE_RE.search(value) or HTML_TAG_RE.search(value):
        raise ValueError("prose must not contain URLs, links, or HTML")
    if (
        MARKDOWN_BLOCK_RE.search(value)
        or INLINE_MARKDOWN_RE.search(value)
        or HORIZONTAL_RULE_PROSE_RE.search(value)
        or REFERENCE_LINK_RE.search(value)
    ):
        raise ValueError("prose must not contain Markdown syntax")
    if _contains_emoji(value):
        raise ValueError("prose must not contain emoji")
    return value


def _reject_plain_heading(value: str) -> str:
    _reject_plain_text(value)
    if "#" in value:
        raise ValueError("title and heading must not contain heading markers")
    return value


def _contains_emoji(value: str) -> bool:
    emoji_ranges = (
        (0x1F1E6, 0x1F1FF),
        (0x1F300, 0x1FAFF),
        (0x2600, 0x27BF),
    )
    emoji_codepoints = {
        0x00A9,
        0x00AE,
        0x200D,
        0x203C,
        0x2049,
        0x20E3,
        0x2122,
        0x2139,
        0x3030,
        0x303D,
        0x3297,
        0x3299,
        0xFE0F,
    }
    return any(
        codepoint in emoji_codepoints
        or any(start <= codepoint <= end for start, end in emoji_ranges)
        for codepoint in map(ord, value)
    )


def write_newsletter(
    input_path: Path | str,
    output_path: Path | str,
    *,
    articles_path: Path | str | None = None,
    model: str | None = None,
    client: Any | None = None,
    run_id: str | None = None,
    state_path: Path | str | None = None,
    ai_state_path: Path | str | None = None,
) -> str:
    """Generate, validate, checkpoint, and atomically finalize a newsletter."""

    _load_dotenv()
    input_path = Path(input_path)
    output_path = Path(output_path)
    model = resolve_model(model)

    input_bytes = input_path.read_bytes()
    trends_file = _validate_json_artifact(
        TrendsFile,
        input_path,
        input_bytes,
        contract_name="TrendsFile",
    )
    articles_bytes = b""
    if articles_path is None:
        topic_articles = []
    else:
        articles_path = Path(articles_path)
        articles_bytes = articles_path.read_bytes()
        topic_articles = _load_topic_articles(
            articles_path,
            articles_bytes,
            trends_file.topic,
        )
    _validate_article_references(trends_file, topic_articles)

    input_fingerprint = _fingerprint(input_bytes + b"\0" + articles_bytes)
    fallback_model = resolve_fallback_model()
    policy_fingerprint = _policy_fingerprint(
        topic=trends_file.topic,
        model=model,
        fallback_model=fallback_model,
    )
    selected_run_id = run_id or (
        f"{input_fingerprint[:16]}-{policy_fingerprint[:16]}"
    )
    _validate_run_id(selected_run_id)
    selected_ai_state_path = (
        Path(ai_state_path)
        if ai_state_path is not None
        else execution_state_path(output_path.parent, selected_run_id)
    )
    selected_state_path = Path(state_path) if state_path else (
        output_path.parent
        / STATE_DIRECTORY
        / trends_file.topic
        / selected_run_id
    )
    known_urls = {article.canonical_url for article in topic_articles}

    resumed = _load_valid_candidate(
        state_path=selected_state_path,
        run_id=selected_run_id,
        topic=trends_file.topic,
        input_fingerprint=input_fingerprint,
        policy_fingerprint=policy_fingerprint,
        known_urls=known_urls,
    )
    if resumed is not None:
        _atomic_write_text(output_path, resumed + "\n")
        return resumed

    if not trends_file.trends:
        markdown = _empty_newsletter(trends_file.topic)
        actual_model = "deterministic-empty"
        selected_urls: set[str] = set()
    else:
        draft, actual_model = _generate_valid_draft(
            trends_file,
            topic_articles=topic_articles,
            model=model,
            fallback_model=fallback_model,
            client=client,
            run_id=selected_run_id,
            ai_state_path=selected_ai_state_path,
        )
        markdown = render_newsletter(draft, trends_file, topic_articles)
        selected_urls = _selected_article_urls(draft, topic_articles)
    markdown = validate_markdown(normalize_markdown(markdown), known_urls=known_urls)
    _validate_rendered_urls_exact(markdown, known_urls=selected_urls)

    if trends_file.trends:
        _atomic_write_text(
            selected_state_path / "editor_draft.json",
            draft.model_dump_json(indent=2) + "\n",
        )

    _write_candidate_and_checkpoint(
        markdown=markdown,
        model=actual_model,
        state_path=selected_state_path,
        run_id=selected_run_id,
        topic=trends_file.topic,
        input_fingerprint=input_fingerprint,
        policy_fingerprint=policy_fingerprint,
    )
    _atomic_write_text(output_path, markdown + "\n")
    return markdown


def _empty_newsletter(topic: str) -> str:
    topic_names = {
        "technology": "Technology Trends",
        "backend": "Backend",
        "qa": "QA / Quality Engineering",
    }
    topic_name = topic_names.get(topic, topic)
    return (
        f"# 🗞️ 이번 주 {topic_name} 뉴스레터\n\n"
        "## 🔎 핵심 요약\n\n"
        "- 이번 실행에서 해당 토픽으로 분류된 주요 기사가 없습니다.\n\n"
        "## 📌 주요 트렌드\n\n"
        "- 이번 주에 정리할 주요 트렌드가 없습니다.\n\n"
        "## 💡 이번 주 인사이트\n\n"
        "수집 소스, 태그 규칙, 분류 규칙을 점검한 뒤 다음 실행에서 다시 확인합니다."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dtns.agents.editor")
    parser.add_argument(
        "--input",
        default=TOPIC_TRENDS_FILENAME,
        type=Path,
        help="Path to topic_trends.json.",
    )
    parser.add_argument(
        "--output",
        default=NEWSLETTER_FILENAME,
        type=Path,
        help="Path to write newsletter.md.",
    )
    parser.add_argument(
        "--articles",
        default=None,
        type=Path,
        help="Optional topic_articles.json path for article titles and URLs.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            f"Gemini model name. Defaults to ${MODEL_ENV_VAR}, "
            f"$GEMINI_MODEL, or {DEFAULT_MODEL}."
        ),
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--state-path", default=None, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    write_newsletter(
        input_path=args.input,
        output_path=args.output,
        articles_path=args.articles,
        model=args.model,
        run_id=args.run_id,
        state_path=args.state_path,
    )
    return 0


def resolve_model(model: str | None = None) -> str:
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


def normalize_markdown(markdown: str) -> str:
    markdown = markdown.strip()
    if not markdown:
        raise ValueError("Editor model returned empty Markdown.")
    if "```" in markdown or FENCE_RE.search(markdown):
        raise ValueError("Editor model returned a code fence.")
    if FRONT_MATTER_RE.match(markdown):
        raise ValueError("Editor model returned front matter.")
    if markdown.startswith("{") or markdown.startswith("["):
        raise ValueError("Editor model returned JSON, but Markdown is required.")
    markdown = HORIZONTAL_RULE_RE.sub(DISCORD_DIVIDER, markdown)
    markdown = LEVEL_FOUR_HEADING_RE.sub(_bold_heading, markdown)
    return markdown


def validate_markdown(markdown: str, *, known_urls: set[str]) -> str:
    """Validate the public newsletter contract and return unchanged Markdown."""

    if not markdown or len(markdown) > MAX_CHARACTERS:
        raise ValueError("Editor Markdown must contain 1 to 12,000 characters.")
    h1_headings = re.findall(r"^#\s+.*$", markdown, re.MULTILINE)
    first_line = markdown.splitlines()[0]
    if len(h1_headings) != 1 or re.fullmatch(r"# 🗞️ [^#\r\n]+", first_line) is None:
        raise ValueError("Editor Markdown must contain exactly one valid title heading.")
    if markdown.count("🗞️") != 1:
        raise ValueError("Editor Markdown must contain the title emoji exactly once.")
    missing = [
        section for section, pattern in SECTION_PATTERNS.items()
        if len(pattern.findall(markdown)) != 1
    ]
    if missing:
        raise ValueError(
            "Editor Markdown is missing required sections: " + ", ".join(missing)
        )
    output_urls = _extract_article_urls(markdown)
    normalized_known_urls = {
        _normalize_url_for_allowlist(url) for url in known_urls
    }
    unknown_urls = sorted(
        url
        for url in output_urls
        if _normalize_url_for_allowlist(url) not in normalized_known_urls
    )
    if unknown_urls:
        raise ValueError(
            "Editor Markdown contains unknown article URLs: "
            + ", ".join(unknown_urls)
        )
    _validate_korean_body(markdown)
    return markdown


def _validate_rendered_urls_exact(markdown: str, *, known_urls: set[str]) -> None:
    unknown_urls = sorted(_extract_article_urls(markdown) - known_urls)
    if unknown_urls:
        raise ValueError(
            "Rendered Markdown contains non-canonical article URLs: "
            + ", ".join(unknown_urls)
        )


def _extract_article_urls(markdown: str) -> set[str]:
    """Extract inline-link destinations and bare URLs without regex truncation."""

    destinations, link_ranges = _parse_inline_link_destinations(markdown)
    masked = list(markdown)
    for start, end in link_ranges:
        masked[start:end] = " " * (end - start)
    return set(destinations) | set(_parse_bare_urls("".join(masked)))


def _parse_inline_link_destinations(
    markdown: str,
) -> tuple[list[str], list[tuple[int, int]]]:
    destinations: list[str] = []
    ranges: list[tuple[int, int]] = []
    cursor = 0
    while True:
        opener = markdown.find("](", cursor)
        if opener < 0:
            break
        parsed = _parse_link_destination(markdown, opener + 2)
        if parsed is None:
            cursor = opener + 2
            continue
        destination, end = parsed
        if destination.casefold().startswith(("http://", "https://")):
            destinations.append(destination)
        ranges.append((opener, end))
        cursor = end
    return destinations, ranges


def _parse_link_destination(markdown: str, start: int) -> tuple[str, int] | None:
    cursor = start
    while cursor < len(markdown) and markdown[cursor] in " \t\n":
        cursor += 1
    if cursor >= len(markdown):
        return None

    if markdown[cursor] == "<":
        destination_start = cursor + 1
        cursor = destination_start
        while cursor < len(markdown):
            if markdown[cursor] == "\\":
                cursor += 2
                continue
            if markdown[cursor] == ">":
                destination = markdown[destination_start:cursor]
                cursor += 1
                break
            if markdown[cursor] in "\n<":
                return None
            cursor += 1
        else:
            return None
    else:
        destination_start = cursor
        depth = 0
        while cursor < len(markdown):
            character = markdown[cursor]
            if character == "\\" and cursor + 1 < len(markdown):
                cursor += 2
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                if depth == 0:
                    break
                depth -= 1
            elif character.isspace() and depth == 0:
                break
            cursor += 1
        if cursor == destination_start or depth != 0:
            return None
        destination = markdown[destination_start:cursor]

    while cursor < len(markdown) and markdown[cursor].isspace():
        cursor += 1
    if cursor < len(markdown) and markdown[cursor] in {'"', "'"}:
        quote = markdown[cursor]
        cursor = _skip_link_title(markdown, cursor + 1, quote)
        if cursor < 0:
            return None
    elif cursor < len(markdown) and markdown[cursor] == "(":
        cursor = _skip_link_title(markdown, cursor + 1, ")")
        if cursor < 0:
            return None
    while cursor < len(markdown) and markdown[cursor].isspace():
        cursor += 1
    if cursor >= len(markdown) or markdown[cursor] != ")":
        return None
    return _unescape_markdown(destination), cursor + 1


def _skip_link_title(markdown: str, cursor: int, closing: str) -> int:
    while cursor < len(markdown):
        if markdown[cursor] == "\\" and cursor + 1 < len(markdown):
            cursor += 2
            continue
        if markdown[cursor] == closing:
            return cursor + 1
        if markdown[cursor] == "\n":
            return -1
        cursor += 1
    return -1


def _unescape_markdown(value: str) -> str:
    unescaped = re.sub(
        r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])",
        r"\1",
        value,
    )
    return html.unescape(unescaped)


def _normalize_url_for_allowlist(url: str) -> str:
    """Normalize only URL components whose syntax is case-insensitive."""

    match = re.match(
        r"^(?P<scheme>https?)://(?P<authority>[^/?#]*)(?P<rest>.*)$",
        url,
        re.IGNORECASE,
    )
    if match is None:
        return url
    authority = match.group("authority")
    userinfo, separator, host_port = authority.rpartition("@")
    if not separator:
        userinfo = ""
        host_port = authority
    if host_port.startswith("[") and "]" in host_port:
        closing = host_port.index("]")
        normalized_host_port = (
            host_port[: closing + 1].casefold() + host_port[closing + 1 :]
        )
    else:
        host, port_separator, port = host_port.rpartition(":")
        normalized_host_port = (
            f"{host.casefold()}:{port}"
            if port_separator and port.isdigit()
            else host_port.casefold()
        )
    normalized_authority = (
        f"{userinfo}@{normalized_host_port}"
        if separator
        else normalized_host_port
    )
    return (
        f"{match.group('scheme').casefold()}://"
        f"{normalized_authority}{match.group('rest')}"
    )


def _parse_bare_urls(markdown: str) -> list[str]:
    urls: list[str] = []
    search_start = 0
    while match := URL_START_RE.search(markdown, search_start):
        cursor = match.start()
        depth = 0
        while cursor < len(markdown):
            character = markdown[cursor]
            if character.isspace() or character in '<>"':
                break
            if character == "(":
                depth += 1
            elif character == ")":
                if depth == 0:
                    break
                depth -= 1
            cursor += 1
        url = markdown[match.start():cursor].rstrip(".,:")
        if url:
            urls.append(url)
        search_start = max(cursor, match.end())
    return urls


def _validate_korean_body(markdown: str) -> None:
    body = ATX_HEADING_RE.sub("", markdown)
    body = BOLD_LABEL_RE.sub("", body)
    body = re.sub(r"https?://\S+", "", body, flags=re.IGNORECASE)
    hangul_count = len(HANGUL_RE.findall(body))
    latin_count = len(LATIN_RE.findall(body))
    language_characters = hangul_count + latin_count
    korean_ratio = hangul_count / language_characters if language_characters else 0
    if (
        hangul_count < MIN_KOREAN_BODY_CHARACTERS
        or korean_ratio < MIN_KOREAN_BODY_RATIO
    ):
        raise ValueError("Editor Markdown body must be written in Korean.")


def _bold_heading(match: re.Match[str]) -> str:
    heading = match.group(1).strip()
    if heading.startswith("**") and heading.endswith("**"):
        return heading
    return f"**{heading}**"


def _request_draft(
    trends_file: TrendsFile,
    *,
    topic_articles: list[TopicArticle],
    model: str,
    fallback_model: str,
    client: Any | None,
    policy_primary_model: str,
    run_id: str,
    ai_state_path: Path,
    validation_feedback: str | None = None,
) -> tuple[dict[str, Any], str, GenerationResult]:
    payload = _build_input_payload(trends_file, topic_articles)
    if validation_feedback is not None:
        payload["validation_feedback"] = validation_feedback
    generation = generate_content_with_fallback(
        primary_model=model,
        fallback_model=fallback_model,
        contents=[
            _build_system_prompt(trends_file.topic),
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
        ],
        config={
            "temperature": GENERATION_TEMPERATURE,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "response_mime_type": "application/json",
            "response_json_schema": _draft_response_schema(),
        },
        client=client,
        run_id=run_id,
        execution_state_path=ai_state_path,
        policy_primary_model=policy_primary_model,
        use_requested_model_when_closed=True,
    )
    response = generation.response

    _reject_truncated_response(response)

    content = getattr(response, "text", None)
    if not content:
        raise EditorContentError("empty_response")
    try:
        payload = json.loads(str(content))
    except json.JSONDecodeError:
        raise EditorContentError("invalid_json") from None
    if not isinstance(payload, dict):
        raise EditorContentError("invalid_json")
    return payload, generation.model, generation


def _generate_valid_draft(
    trends_file: TrendsFile,
    *,
    topic_articles: list[TopicArticle],
    model: str,
    fallback_model: str,
    client: Any | None,
    run_id: str,
    ai_state_path: Path,
) -> tuple[EditorDraft, str]:
    """Retry one content failure, then make one explicit fallback attempt."""

    last_error: ValueError | None = None
    requested_models = [model] * MAX_CONTENT_ATTEMPTS
    if fallback_model != model:
        requested_models.append(fallback_model)
    validation_feedback: str | None = None
    for requested_model in requested_models:
        try:
            payload, actual_model, generation = _request_draft(
                trends_file,
                topic_articles=topic_articles,
                model=requested_model,
                fallback_model=fallback_model,
                client=client,
                policy_primary_model=model,
                run_id=run_id,
                ai_state_path=ai_state_path,
                validation_feedback=validation_feedback,
            )
        except EditorContentError as error:
            last_error = error
            validation_feedback = str(error)
            continue
        try:
            runtime_fields = {"schema_version", "topic", "generated_at"}
            if runtime_fields.intersection(payload):
                raise EditorContentError("runtime_owned_fields")
            draft = EditorDraft.model_validate(
                {
                    **payload,
                    "schema_version": SCHEMA_VERSION,
                    "topic": trends_file.topic,
                    "generated_at": datetime.now(UTC),
                }
            )
            _validate_draft_references(draft, trends_file, topic_articles)
            accept = getattr(generation, "accept", None)
            if callable(accept):
                accept()
            return draft, actual_model
        except (ValidationError, ValueError) as error:
            last_error = error
            validation_feedback = _draft_validation_feedback(error)
    raise ValueError("Editor draft generation failed content validation.") from last_error


def _draft_response_schema() -> dict[str, Any]:
    """Gemini response schema without runtime-owned envelope fields."""

    schema = EditorDraft.model_json_schema()
    properties = schema["properties"]
    for field in ("schema_version", "topic", "generated_at"):
        properties.pop(field, None)
    schema["required"] = [
        field
        for field in schema["required"]
        if field not in {"schema_version", "topic", "generated_at"}
    ]
    return schema


def _validate_draft_references(
    draft: EditorDraft,
    trends_file: TrendsFile,
    articles: list[TopicArticle],
) -> None:
    trends_by_id = {trend.id: trend for trend in trends_file.trends}
    article_ids = {article.id for article in articles}
    section_ids = [section.trend_id for section in draft.trend_sections]
    if len(section_ids) != len(set(section_ids)):
        raise EditorContentError("duplicate_trend_ids")
    for section in draft.trend_sections:
        trend = trends_by_id.get(section.trend_id)
        if trend is None:
            raise EditorContentError(f"unknown_trend_id:{section.trend_id}")
        for article_id in section.article_ids:
            if article_id not in article_ids:
                raise EditorContentError(f"unknown_article_id:{article_id}")
            if article_id not in trend.article_ids:
                raise EditorContentError(
                    f"misplaced_article_id:{section.trend_id}:{article_id}"
                )


def _draft_validation_feedback(error: Exception) -> str:
    """Return corrective feedback without prose, URLs, or raw model output."""

    if isinstance(error, EditorContentError):
        return str(error)
    if isinstance(error, ValidationError):
        fields = sorted(
            {
                ".".join(str(part) for part in failure["loc"])
                for failure in error.errors(include_input=False, include_url=False)
            }
        )
        return "invalid_fields:" + ",".join(fields)
    return "invalid_draft"


def render_newsletter(
    draft: EditorDraft,
    trends_file: TrendsFile,
    articles: list[TopicArticle],
) -> str:
    """Render a validated draft using only contract-owned article URLs."""

    _validate_draft_references(draft, trends_file, articles)
    articles_by_id = {article.id: article for article in articles}
    trends_by_id = {trend.id: trend for trend in trends_file.trends}
    lines = [
        f"# 🗞️ {draft.title}",
        "",
        "## 🔎 핵심 요약",
        "",
        *[f"- {item}" for item in draft.summary_items],
        "",
        "## 📌 주요 트렌드",
    ]
    for index, section in enumerate(draft.trend_sections, start=1):
        importance_emoji = {
            "high": "🚀",
            "medium": "🧭",
            "low": "🔧",
        }[trends_by_id[section.trend_id].importance]
        lines.extend(
            [
                "",
                f"### {index}. {importance_emoji} {section.heading}",
                "",
                section.overview,
                "",
                "**왜 중요한가**",
                f"- {section.why_it_matters}",
                "",
                "**관련 글**",
            ]
        )
        for article_id in section.article_ids:
            article = articles_by_id[article_id]
            lines.append(
                f"- 🔗 [{_escape_link_label(article.title)}]"
                f"({article.canonical_url})"
            )
    lines.extend(
        [
            "",
            "## 💡 이번 주 인사이트",
            "",
            *[f"- {item}" for item in draft.insight_items],
        ]
    )
    return "\n".join(lines)


def _selected_article_urls(
    draft: EditorDraft,
    articles: list[TopicArticle],
) -> set[str]:
    articles_by_id = {article.id: article for article in articles}
    return {
        articles_by_id[article_id].canonical_url
        for section in draft.trend_sections
        for article_id in section.article_ids
    }


def _escape_link_label(value: str) -> str:
    return re.sub(r"([\\\[\]])", r"\\\1", value).replace("\n", " ")


def _build_system_prompt(topic: str) -> str:
    topic_prompt = _load_topic_prompt(topic)
    if topic_prompt:
        return f"{topic_prompt.rstrip()}\n\n{_universal_editor_rules()}"

    return _universal_editor_rules(
        "You are the DTNS Editor Agent. Write a weekly software engineering "
        "newsletter in natural Korean Markdown for any topic."
    )


def _universal_editor_rules(prefix: str | None = None) -> str:
    rules = (
        "Keep technical names in English. Summarize trends, summarize supplied "
        "articles, generate weekly insights, and explain why each trend matters. "
        "Do not fully translate articles. Do not fabricate information. If "
        "article metadata is missing, do not invent titles, dates, or claims. "
        "Return one JSON object matching the supplied schema. Never emit or "
        "reconstruct a URL, Markdown syntax, HTML, emoji, article title, "
        "timestamp, schema version, or topic. Every prose field must be trimmed, "
        "single-line plain text. Reference sources only by their exact article "
        "IDs and keep each article within its referenced trend."
    )
    if prefix is None:
        return rules
    return f"{prefix} {rules}"


def _build_input_payload(
    trends_file: TrendsFile,
    topic_articles: list[TopicArticle],
) -> dict[str, Any]:
    articles_by_id = {article.id: article for article in topic_articles}
    payload: dict[str, Any] = {
        "task": "write a Korean newsletter draft as JSON",
        "topic": trends_file.topic,
        "period": (
            trends_file.period.model_dump(mode="json")
            if trends_file.period is not None
            else None
        ),
        "trends": [
            {
                "id": trend.id,
                "title": trend.title,
                "importance": trend.importance,
                "summary": trend.summary,
                "why_it_matters": trend.why_it_matters,
                "keywords": trend.keywords,
                "articles": [
                    _article_payload(articles_by_id[article_id])
                    for article_id in trend.article_ids
                    if article_id in articles_by_id
                ],
            }
            for trend in trends_file.trends
        ],
        "requirements": [
            "Write natural Korean.",
            "Keep technical names in English.",
            "Output JSON only and omit schema_version, topic, and generated_at.",
            "Summarize trends and explain why they matter.",
            "Summarize supplied article metadata without fully translating articles.",
            "Generate weekly insights based only on supplied trend and article data.",
            "Do not emit URLs, links, article titles, or unknown IDs.",
            "Use trimmed single-line plain text without Markdown, HTML, or emoji.",
            "Select article IDs only from the corresponding trend.",
        ],
    }
    return payload


def _article_payload(article: TopicArticle) -> dict[str, Any]:
    return {
        "id": article.id,
        "source": article.source,
        "title": article.title,
        "published_at": (
            article.published_at.isoformat()
            if article.published_at is not None
            else None
        ),
        "summary": article.summary,
        "tags": article.tags,
        "technologies": article.technologies,
        "domains": article.domains,
    }


def _load_topic_prompt(topic: str) -> str | None:
    prompt_path = (
        Path(__file__).resolve().parents[2] / "prompts" / f"editor_{topic}.md"
    )
    if not prompt_path.exists():
        return None
    return prompt_path.read_text(encoding="utf-8")


def _load_topic_articles(
    articles_path: Path,
    articles_bytes: bytes,
    expected_topic: str,
) -> list[TopicArticle]:
    document = _validate_json_artifact(
        TopicArticlesFile,
        articles_path,
        articles_bytes,
        contract_name="TopicArticlesFile",
    )
    if document.topic != expected_topic:
        raise ValueError(
            f"articles topic '{document.topic}' does not match trends topic "
            f"'{expected_topic}'"
        )
    return document.articles


def _validate_json_artifact(
    model: type[BaseModel],
    path: Path,
    artifact_bytes: bytes,
    *,
    contract_name: str,
) -> Any:
    try:
        return model.model_validate_json(artifact_bytes)
    except ValidationError as error:
        failures = "; ".join(
            f"{'.'.join(str(part) for part in failure['loc']) or '<root>'}: "
            f"{failure['msg']}"
            for failure in error.errors(include_input=False, include_url=False)
        )
        raise ValueError(
            f"Invalid {contract_name} artifact at {path}: {failures}"
        ) from None


def _validate_article_references(
    trends_file: TrendsFile,
    articles: list[TopicArticle],
) -> None:
    article_id_list = [article.id for article in articles]
    if len(article_id_list) != len(set(article_id_list)):
        raise ValueError("topic_articles contains duplicate article IDs")
    trend_id_list = [trend.id for trend in trends_file.trends]
    if len(trend_id_list) != len(set(trend_id_list)):
        raise ValueError("trends contains duplicate trend IDs")
    article_ids = set(article_id_list)
    for trend in trends_file.trends:
        missing_ids = sorted(set(trend.article_ids) - article_ids)
        if missing_ids:
            raise ValueError(
                f"trend '{trend.id}' references unknown article IDs: "
                f"{', '.join(missing_ids)}"
            )


def _reject_truncated_response(response: Any) -> None:
    reasons: list[Any] = [getattr(response, "finish_reason", None)]
    candidates = getattr(response, "candidates", None) or []
    reasons.extend(getattr(candidate, "finish_reason", None) for candidate in candidates)
    for reason in reasons:
        if reason is None:
            continue
        name = getattr(reason, "name", str(reason)).upper()
        if "MAX_TOKENS" in name or "LENGTH" in name:
            raise EditorContentError("Editor model response was truncated.")


def _write_candidate_and_checkpoint(
    *,
    markdown: str,
    model: str,
    state_path: Path,
    run_id: str,
    topic: Literal["technology", "backend", "qa"],
    input_fingerprint: str,
    policy_fingerprint: str,
) -> None:
    candidate_path = state_path / "candidate.md"
    _atomic_write_text(candidate_path, markdown)
    checkpoint = EditorGenerationCheckpoint(
        run_id=run_id,
        topic=topic,
        input_fingerprint=input_fingerprint,
        policy_fingerprint=policy_fingerprint,
        model=model,
        candidate_fingerprint=_fingerprint(markdown.encode("utf-8")),
        character_count=len(markdown),
        validated_sections=VALIDATED_SECTIONS,
        generated_at=datetime.now(UTC),
    )
    _atomic_write_text(
        state_path / "checkpoint.json",
        checkpoint.model_dump_json(indent=2) + "\n",
    )


def _load_valid_candidate(
    *,
    state_path: Path,
    run_id: str,
    topic: Literal["technology", "backend", "qa"],
    input_fingerprint: str,
    policy_fingerprint: str,
    known_urls: set[str],
) -> str | None:
    checkpoint_path = state_path / "checkpoint.json"
    candidate_path = state_path / "candidate.md"
    if not checkpoint_path.is_file() or not candidate_path.is_file():
        return None
    try:
        checkpoint = EditorGenerationCheckpoint.model_validate_json(
            checkpoint_path.read_bytes()
        )
        candidate = candidate_path.read_text(encoding="utf-8")
        if (
            checkpoint.run_id != run_id
            or checkpoint.topic != topic
            or checkpoint.input_fingerprint != input_fingerprint
            or checkpoint.policy_fingerprint != policy_fingerprint
            or checkpoint.candidate_filename != candidate_path.name
            or checkpoint.candidate_fingerprint
            != _fingerprint(candidate.encode("utf-8"))
            or checkpoint.character_count != len(candidate)
        ):
            return None
        return validate_markdown(normalize_markdown(candidate), known_urls=known_urls)
    except (OSError, ValidationError, ValueError):
        return None


def _policy_fingerprint(
    *, topic: str, model: str, fallback_model: str | None
) -> str:
    resolved_fallback = fallback_model or DEFAULT_FALLBACK_MODEL
    policy = {
        "checkpoint_schema_version": SCHEMA_VERSION,
        "validation_version": "3",
        "editor_draft_schema": EditorDraft.model_json_schema(),
        "renderer_policy": "editor-draft-to-newsletter-v1",
        "newsletter_contract": {
            "required_sections": VALIDATED_SECTIONS,
            "divider": DISCORD_DIVIDER,
            "links_from_canonical_url_only": True,
        },
        "contract_documents": _editor_contract_documents(),
        "prompt": _build_system_prompt(topic),
        "models": {
            "primary": model,
            "fallback": resolved_fallback,
        },
        "execution_policy_fingerprint": generation_policy_fingerprint(
            primary_model=model,
            fallback_model=resolved_fallback,
        ),
        "limits": {
            "trends": MAX_TRENDS,
            "characters": MAX_CHARACTERS,
            "content_attempts": MAX_CONTENT_ATTEMPTS,
            "output_tokens": MAX_OUTPUT_TOKENS,
            "minimum_korean_body_characters": MIN_KOREAN_BODY_CHARACTERS,
            "minimum_korean_body_ratio": MIN_KOREAN_BODY_RATIO,
        },
        "generation": {"temperature": GENERATION_TEMPERATURE},
        "sections": VALIDATED_SECTIONS,
    }
    return _fingerprint(
        json.dumps(policy, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )


def _editor_contract_documents() -> dict[str, str]:
    contract_directory = Path(__file__).resolve().parents[4] / "docs" / "contracts"
    names = ("editor_draft.md", "editor_draft.schema.json", "newsletter.md")
    return {
        name: (contract_directory / name).read_text(encoding="utf-8")
        for name in names
    }


def editor_policy_fingerprint(
    topic: str,
    *,
    model: str | None = None,
    fallback_model: str | None = None,
) -> str:
    """Return the complete topic-specific Editor policy identity."""

    return _policy_fingerprint(
        topic=topic,
        model=resolve_model(model),
        fallback_model=fallback_model or resolve_fallback_model(),
    )


def _fingerprint(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_run_id(run_id: str) -> None:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be a non-empty path segment")


def _atomic_write_text(path: Path, content: str) -> None:
    _ensure_durable_directory(path.parent)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _ensure_durable_directory(directory: Path) -> None:
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir(exist_ok=True)
        _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is project-level.
        return
    load_dotenv()


if __name__ == "__main__":
    raise SystemExit(main())
