"""Source-specific article fetching.

Collectors preserve feed/API metadata and avoid editorial decisions. Any
summary field comes directly from the upstream source.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import feedparser
import httpx

from dtns.collectors.models import RawArticle, SourceType


DEFAULT_INFOQ_FEEDS = (
    "https://feed.infoq.com/",
)

DEFAULT_OSS_INSIGHT_FEEDS = (
    "https://ossinsight.io/blog/rss.xml",
)

DEFAULT_ENGINEERING_BLOG_FEEDS = (
    "https://netflixtechblog.com/feed",
    "https://engineering.fb.com/feed/",
    "https://github.blog/engineering.atom",
    "https://www.uber.com/blog/engineering/rss/",
)

DEFAULT_GITHUB_RELEASE_REPOSITORIES = (
    "python/cpython",
    "nodejs/node",
    "kubernetes/kubernetes",
    "pytorch/pytorch",
    "tensorflow/tensorflow",
)


@dataclass(frozen=True)
class FeedSource:
    name: str
    url: str
    source_type: SourceType | None = None


@dataclass(frozen=True)
class GitHubReleaseSource:
    repository: str

    @property
    def name(self) -> str:
        return f"GitHub Releases: {self.repository}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repository}/releases.atom"


def default_feed_sources() -> tuple[FeedSource, ...]:
    sources: list[FeedSource] = []
    sources.extend(
        FeedSource("InfoQ", url, SourceType.RSS) for url in DEFAULT_INFOQ_FEEDS
    )
    sources.extend(
        FeedSource("OSS Insight", url, SourceType.RSS)
        for url in DEFAULT_OSS_INSIGHT_FEEDS
    )
    sources.extend(
        FeedSource("Engineering Blog", url) for url in DEFAULT_ENGINEERING_BLOG_FEEDS
    )
    return tuple(sources)


def default_github_release_sources() -> tuple[GitHubReleaseSource, ...]:
    return tuple(
        GitHubReleaseSource(repository)
        for repository in DEFAULT_GITHUB_RELEASE_REPOSITORIES
    )


def fetch_feed_articles(
    client: httpx.Client,
    source: FeedSource,
    collected_at: datetime,
    *,
    limit: int | None = None,
) -> list[RawArticle]:
    response = client.get(source.url)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    source_type = source.source_type or _source_type_from_feed(feed)

    articles: list[RawArticle] = []
    for entry in _limited(feed.entries, limit):
        title = _clean_text(entry.get("title"))
        url = _entry_url(entry)
        if not title or not url:
            continue

        articles.append(
            RawArticle(
                source=_source_name(source, feed),
                source_type=source_type,
                title=title,
                url=url,
                summary=_clean_text(
                    entry.get("summary")
                    or entry.get("description")
                    or entry.get("subtitle")
                ),
                author=_entry_author(entry),
                published_at=_entry_datetime(entry),
                collected_at=collected_at,
                raw=_feed_entry_raw(entry),
            )
        )
    return articles


def fetch_github_release_articles(
    client: httpx.Client,
    source: GitHubReleaseSource,
    collected_at: datetime,
    *,
    limit: int | None = None,
) -> list[RawArticle]:
    response = client.get(source.url)
    response.raise_for_status()
    feed = feedparser.parse(response.content)

    articles: list[RawArticle] = []
    for entry in _limited(feed.entries, limit):
        title = _clean_text(entry.get("title"))
        url = _entry_url(entry)
        if not title or not url:
            continue

        articles.append(
            RawArticle(
                source=source.name,
                source_type=SourceType.GITHUB_RELEASE,
                title=title,
                url=url,
                summary=_clean_text(entry.get("summary")),
                author=_entry_author(entry),
                published_at=_entry_datetime(entry),
                collected_at=collected_at,
                raw=_feed_entry_raw(entry),
            )
        )
    return articles


def _limited(items: Iterable[Any], limit: int | None) -> Iterable[Any]:
    if limit is None:
        return items
    return list(items)[:limit]


def _source_type_from_feed(feed: Any) -> SourceType:
    version = str(getattr(feed, "version", "")).lower()
    if "atom" in version:
        return SourceType.ATOM
    return SourceType.RSS


def _source_name(source: FeedSource, feed: Any) -> str:
    title = _clean_text(getattr(feed, "feed", {}).get("title"))
    return title or source.name


def _entry_url(entry: Any) -> str | None:
    if entry.get("link"):
        return str(entry["link"])
    for link in entry.get("links", []):
        href = link.get("href")
        if href:
            return str(href)
    return None


def _entry_author(entry: Any) -> str | None:
    author = _clean_text(entry.get("author"))
    if author:
        return author
    authors = entry.get("authors") or []
    names = [_clean_text(author.get("name")) for author in authors]
    names = [name for name in names if name]
    return ", ".join(names) or None


def _entry_datetime(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _feed_entry_raw(entry: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for key in (
        "id",
        "guidislink",
        "tags",
        "links",
        "published",
        "updated",
        "content",
    ):
        if key in entry:
            raw[key] = entry[key]
    return raw
