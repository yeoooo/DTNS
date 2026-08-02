"""Source-specific article fetching.

Collectors preserve feed/API metadata and avoid editorial decisions. Any
summary field comes directly from the upstream source.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urljoin

import feedparser
import httpx

from dtns.collectors.models import RawArticle, SourceType


DEFAULT_GITHUB_RELEASE_REPOSITORIES = (
    "moby/moby",
    "redis/redis",
)


class InvalidFeedError(ValueError):
    """Raised when a response cannot be interpreted as a feed."""


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


@dataclass(frozen=True)
class HtmlSource:
    name: str
    url: str
    parser: str


DEFAULT_FEED_SOURCES = (
    FeedSource("InfoQ", "https://www.infoq.com/feed", SourceType.RSS),
    FeedSource("The New Stack", "https://thenewstack.io/feed/", SourceType.RSS),
    FeedSource("Martin Fowler", "https://martinfowler.com/feed.atom", SourceType.ATOM),
    FeedSource("Netflix TechBlog", "https://netflixtechblog.com/feed"),
    FeedSource("Meta Engineering", "https://engineering.fb.com/feed/", SourceType.RSS),
    FeedSource(
        "GitHub Engineering",
        "https://github.blog/engineering/feed/",
        SourceType.RSS,
    ),
    FeedSource("Cloudflare Blog", "https://blog.cloudflare.com/rss/", SourceType.RSS),
    FeedSource("Spring Blog", "https://spring.io/blog.atom", SourceType.ATOM),
    FeedSource("Kubernetes Blog", "https://kubernetes.io/feed.xml"),
    FeedSource(
        "OpenTelemetry Blog",
        "https://opentelemetry.io/blog/index.xml",
    ),
    FeedSource("Playwright", "https://dev.to/feed/playwright", SourceType.RSS),
    FeedSource(
        "Software Testing Weekly",
        "https://softwaretestingweekly.com/issues/rss/",
        SourceType.RSS,
    ),
    FeedSource(
        "PostgreSQL News",
        "https://www.postgresql.org/news.rss",
        SourceType.RSS,
    ),
    FeedSource("ByteByteGo", "https://blog.bytebytego.com/feed", SourceType.RSS),
)

DEFAULT_HTML_SOURCES = (
    HtmlSource(
        "Ministry of Testing",
        "https://www.ministryoftesting.com/",
        "ministry_of_testing",
    ),
    HtmlSource(
        "GitHub Trending (weekly)",
        "https://github.com/trending?since=weekly",
        "github_trending",
    ),
)


def default_feed_sources() -> tuple[FeedSource, ...]:
    return DEFAULT_FEED_SOURCES


def default_github_release_sources() -> tuple[GitHubReleaseSource, ...]:
    return tuple(
        GitHubReleaseSource(repository)
        for repository in DEFAULT_GITHUB_RELEASE_REPOSITORIES
    )


def default_html_sources() -> tuple[HtmlSource, ...]:
    return DEFAULT_HTML_SOURCES


def fetch_feed_articles(
    client: httpx.Client,
    source: FeedSource,
    collected_at: datetime,
    *,
    limit: int | None = None,
) -> list[RawArticle]:
    response = client.get(source.url)
    response.raise_for_status()
    feed = _parse_feed(response.content)
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
    feed = _parse_feed(response.content)

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


def fetch_html_articles(
    client: httpx.Client,
    source: HtmlSource,
    collected_at: datetime,
    *,
    limit: int | None = None,
) -> list[RawArticle]:
    response = client.get(source.url)
    response.raise_for_status()
    parser = _ArticleLinkParser(source.parser)
    parser.feed(response.text)

    return [
        RawArticle(
            source=source.name,
            source_type=SourceType.HTML,
            title=title,
            url=urljoin(source.url, href),
            collected_at=collected_at,
            raw={"href": href},
        )
        for href, title in _limited(parser.articles, limit)
    ]


class _ArticleLinkParser(HTMLParser):
    def __init__(self, parser: str) -> None:
        super().__init__(convert_charrefs=True)
        self.parser = parser
        self.articles: list[tuple[str, str]] = []
        self._in_article = False
        self._capture_depth = 0
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if self.parser == "github_trending":
            if tag == "article" and "Box-row" in classes:
                self._in_article = True
        if self._capture_depth:
            self._capture_depth += 1
            return
        if tag != "a":
            return
        href = attributes.get("href")
        is_ministry_article = (
            self.parser == "ministry_of_testing"
            and "stretched-link" in classes
            and bool(href)
        )
        is_trending_repository = (
            self.parser == "github_trending"
            and self._in_article
            and bool(href)
            and href.count("/") == 2
            and not href.startswith(("/sponsors/", "/topics/"))
        )
        if is_ministry_article or is_trending_repository:
            self._capture_depth = 1
            self._href = href
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth:
            if tag == "a" and self._capture_depth == 1:
                title = " ".join("".join(self._text).split())
                if self._href and title:
                    self.articles.append((self._href, title))
                self._capture_depth = 0
                self._href = None
                self._text = []
            else:
                self._capture_depth -= 1
        if self.parser == "github_trending" and tag == "article":
            self._in_article = False

    def handle_data(self, data: str) -> None:
        if self._capture_depth:
            self._text.append(data)


def _limited(items: Iterable[Any], limit: int | None) -> Iterable[Any]:
    if limit is None:
        return items
    return list(items)[:limit]


def _parse_feed(content: bytes) -> Any:
    feed = feedparser.parse(content)
    version = str(getattr(feed, "version", "")).lower()
    is_supported_feed = version.startswith(("rss", "atom"))
    if not is_supported_feed or (
        getattr(feed, "bozo", False) and not feed.entries
    ):
        raise InvalidFeedError("response is not a valid RSS or Atom feed")
    return feed


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
