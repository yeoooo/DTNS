"""Collector entry points."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import ValidationError

from dtns.collectors.models import RawArticle, RawArticlesDocument, SourceType
from dtns.collectors.sources import (
    FeedSource,
    GitHubReleaseSource,
    InvalidFeedError,
    default_feed_sources,
    default_github_release_sources,
    fetch_feed_articles,
    fetch_github_release_articles,
)
from dtns.contracts.collection_report import (
    CollectionReport,
    CollectionSourceReport,
    ErrorCategory,
)

DEFAULT_ARTICLES_FILENAME = "articles.json"
COLLECTION_REPORT_FILENAME = "collection_report.json"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CollectionResult:
    document: RawArticlesDocument
    report: CollectionReport


def collect_articles(
    *,
    feed_sources: tuple[FeedSource, ...] | None = None,
    github_release_sources: tuple[GitHubReleaseSource, ...] | None = None,
    limit_per_source: int | None = None,
    timeout_seconds: float = 20.0,
    source_run_id: str | None = None,
    report_path: str | Path | None = None,
) -> RawArticlesDocument:
    """Fetch raw article candidates from supported collector sources."""

    if feed_sources is None:
        feed_sources = default_feed_sources()
    if github_release_sources is None:
        github_release_sources = default_github_release_sources()
    source_run_id = source_run_id or str(uuid.uuid4())

    result = _collect_articles(
        feed_sources=feed_sources,
        github_release_sources=github_release_sources,
        limit_per_source=limit_per_source,
        timeout_seconds=timeout_seconds,
        source_run_id=source_run_id,
    )
    if report_path is not None:
        write_collection_report(report_path, result.report)
    if result.report.status == "failed":
        raise RuntimeError(
            f"All {len(result.report.sources)} configured article sources failed"
        )
    return result.document


def _collect_articles(
    *,
    feed_sources: tuple[FeedSource, ...],
    github_release_sources: tuple[GitHubReleaseSource, ...],
    limit_per_source: int | None,
    timeout_seconds: float,
    source_run_id: str,
) -> _CollectionResult:
    started_at = datetime.now(UTC)
    configured_sources: list[FeedSource | GitHubReleaseSource] = [
        *feed_sources,
        *github_release_sources,
    ]
    if not configured_sources:
        raise ValueError("At least one article source must be configured")

    articles: list[RawArticle] = []
    source_reports: list[CollectionSourceReport] = []
    headers = {"User-Agent": "dtns-collector/0.1 (+https://github.com/dtns)"}
    with httpx.Client(
        follow_redirects=True,
        timeout=timeout_seconds,
        headers=headers,
    ) as client:
        for source in feed_sources:
            try:
                fetched = fetch_feed_articles(
                    client,
                    source,
                    started_at,
                    limit=limit_per_source,
                )
                accepted = _dedupe_by_url(fetched)
                articles.extend(accepted)
                source_reports.append(
                    _successful_source_report(
                        source.name,
                        _reported_feed_source_type(source, fetched),
                        fetched_count=len(fetched),
                        accepted_count=len(accepted),
                    )
                )
            except Exception as exc:
                error_category, http_status = _classify_error(exc)
                source_reports.append(
                    _failed_source_report(
                        source.name,
                        _feed_source_type(source),
                        error_category,
                        http_status,
                    )
                )
                logger.warning(
                    "Skipping unavailable feed %s (category=%s, status=%s)",
                    source.name,
                    error_category,
                    http_status,
                )

        for source in github_release_sources:
            try:
                fetched = fetch_github_release_articles(
                    client,
                    source,
                    started_at,
                    limit=limit_per_source,
                )
                accepted = _dedupe_by_url(fetched)
                articles.extend(accepted)
                source_reports.append(
                    _successful_source_report(
                        source.name,
                        SourceType.GITHUB_RELEASE,
                        fetched_count=len(fetched),
                        accepted_count=len(accepted),
                    )
                )
            except Exception as exc:
                error_category, http_status = _classify_error(exc)
                source_reports.append(
                    _failed_source_report(
                        source.name,
                        SourceType.GITHUB_RELEASE,
                        error_category,
                        http_status,
                    )
                )
                logger.warning(
                    "Skipping unavailable GitHub release feed %s "
                    "(category=%s, status=%s)",
                    source.name,
                    error_category,
                    http_status,
                )

    successful_sources = sum(
        source.status != "failed" for source in source_reports
    )
    status: Literal["completed", "partial", "failed"]
    if successful_sources == len(source_reports):
        status = "completed"
    elif successful_sources:
        status = "partial"
    else:
        status = "failed"

    return _CollectionResult(
        document=RawArticlesDocument(
            generated_at=started_at,
            source_run_id=source_run_id,
            articles=articles,
        ),
        report=CollectionReport(
            run_id=source_run_id,
            source_config_fingerprint=_source_config_fingerprint(
                configured_sources
            ),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            status=status,
            sources=source_reports,
        ),
    )


def write_articles(
    output_path: str | Path,
    *,
    feed_sources: tuple[FeedSource, ...] | None = None,
    github_release_sources: tuple[GitHubReleaseSource, ...] | None = None,
    limit_per_source: int | None = None,
    timeout_seconds: float = 20.0,
    source_run_id: str | None = None,
) -> RawArticlesDocument:
    """Collect articles and write an `articles.json` contract document."""

    output_path = Path(output_path)
    if output_path.is_dir() or output_path.suffix == "":
        output_path = output_path / DEFAULT_ARTICLES_FILENAME

    run_id = source_run_id or str(uuid.uuid4())
    report_path = collection_report_path(output_path.parent, run_id)
    document = collect_articles(
        feed_sources=feed_sources,
        github_release_sources=github_release_sources,
        limit_per_source=limit_per_source,
        timeout_seconds=timeout_seconds,
        source_run_id=run_id,
        report_path=report_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            document.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return document


def collection_report_path(data_dir: str | Path, run_id: str) -> Path:
    """Return the contract-defined report path for one collector run."""

    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be a non-empty path segment")
    return (
        Path(data_dir)
        / ".state"
        / "collector"
        / run_id
        / COLLECTION_REPORT_FILENAME
    )


def write_collection_report(
    path: str | Path,
    report: CollectionReport,
) -> None:
    """Validate and atomically replace a collection report."""

    path = Path(path)
    validated = CollectionReport.model_validate(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                validated.model_dump(mode="json"),
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _dedupe_by_url(articles: list[RawArticle]) -> list[RawArticle]:
    seen: set[str] = set()
    deduped: list[RawArticle] = []
    for article in articles:
        key = str(article.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(article)
    return deduped


def _successful_source_report(
    name: str,
    source_type: SourceType,
    *,
    fetched_count: int,
    accepted_count: int,
) -> CollectionSourceReport:
    return CollectionSourceReport(
        name=name,
        source_type=source_type,
        status="success" if accepted_count else "empty",
        fetched_count=fetched_count,
        accepted_count=accepted_count,
    )


def _failed_source_report(
    name: str,
    source_type: SourceType,
    error_category: ErrorCategory,
    http_status: int | None,
) -> CollectionSourceReport:
    return CollectionSourceReport(
        name=name,
        source_type=source_type,
        status="failed",
        fetched_count=0,
        accepted_count=0,
        http_status=http_status,
        error_category=error_category,
    )


def _feed_source_type(source: FeedSource) -> SourceType:
    if source.source_type is not None:
        return source.source_type
    return SourceType.ATOM if ".atom" in source.url.lower() else SourceType.RSS


def _reported_feed_source_type(
    source: FeedSource,
    articles: list[RawArticle],
) -> SourceType:
    for article in articles:
        if article.source_type is not None:
            return article.source_type
    return _feed_source_type(source)


def _source_config_fingerprint(
    sources: list[FeedSource | GitHubReleaseSource],
) -> str:
    payload = [
        {
            "name": source.name,
            "source_type": (
                SourceType.GITHUB_RELEASE
                if isinstance(source, GitHubReleaseSource)
                else _feed_source_type(source)
            ).value,
            "url": source.url,
        }
        for source in sources
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _classify_error(
    error: Exception,
) -> tuple[ErrorCategory, int | None]:
    if isinstance(error, httpx.TimeoutException):
        return "timeout", None
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if 400 <= status < 500:
            return "http_client", status
        if 500 <= status < 600:
            return "http_server", status
        return "unknown", status
    if _caused_by(error, socket.gaierror):
        return "dns", None
    if isinstance(error, InvalidFeedError):
        return "invalid_feed", None
    if isinstance(error, ValidationError):
        return "validation", None
    return "unknown", None


def _caused_by(error: BaseException, error_type: type[BaseException]) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, error_type):
            return True
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return False
