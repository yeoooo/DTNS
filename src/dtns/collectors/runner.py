"""Collector entry points."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from dtns.collectors.models import RawArticle, RawArticlesDocument
from dtns.collectors.sources import (
    FeedSource,
    GitHubReleaseSource,
    default_feed_sources,
    default_github_release_sources,
    fetch_feed_articles,
    fetch_github_release_articles,
)

DEFAULT_ARTICLES_FILENAME = "articles.json"
logger = logging.getLogger(__name__)


def collect_articles(
    *,
    feed_sources: tuple[FeedSource, ...] | None = None,
    github_release_sources: tuple[GitHubReleaseSource, ...] | None = None,
    limit_per_source: int | None = None,
    timeout_seconds: float = 20.0,
    source_run_id: str | None = None,
) -> RawArticlesDocument:
    """Fetch raw article candidates from supported collector sources."""

    generated_at = datetime.now(timezone.utc)
    if feed_sources is None:
        feed_sources = default_feed_sources()
    if github_release_sources is None:
        github_release_sources = default_github_release_sources()
    source_run_id = source_run_id or str(uuid.uuid4())

    articles: list[RawArticle] = []
    successful_sources = 0
    headers = {"User-Agent": "dtns-collector/0.1 (+https://github.com/dtns)"}
    with httpx.Client(
        follow_redirects=True,
        timeout=timeout_seconds,
        headers=headers,
    ) as client:
        for source in feed_sources:
            try:
                articles.extend(
                    fetch_feed_articles(
                        client,
                        source,
                        generated_at,
                        limit=limit_per_source,
                    )
                )
                successful_sources += 1
            except Exception as exc:
                logger.warning(
                    "Skipping unavailable feed %s (%s): %s",
                    source.name,
                    source.url,
                    exc,
                )

        for source in github_release_sources:
            try:
                articles.extend(
                    fetch_github_release_articles(
                        client,
                        source,
                        generated_at,
                        limit=limit_per_source,
                    )
                )
                successful_sources += 1
            except Exception as exc:
                logger.warning(
                    "Skipping unavailable GitHub release feed %s (%s): %s",
                    source.name,
                    source.url,
                    exc,
                )

    source_count = len(feed_sources) + len(github_release_sources)
    if source_count and successful_sources == 0:
        raise RuntimeError(f"All {source_count} configured article sources failed")

    return RawArticlesDocument(
        generated_at=generated_at,
        source_run_id=source_run_id,
        articles=_dedupe_by_source_url(articles),
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

    document = collect_articles(
        feed_sources=feed_sources,
        github_release_sources=github_release_sources,
        limit_per_source=limit_per_source,
        timeout_seconds=timeout_seconds,
        source_run_id=source_run_id,
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


def _dedupe_by_source_url(articles: list[RawArticle]) -> list[RawArticle]:
    seen: set[tuple[str, str]] = set()
    deduped: list[RawArticle] = []
    for article in articles:
        key = (article.source, str(article.url))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(article)
    return deduped
