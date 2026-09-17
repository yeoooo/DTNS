"""Source-specific article fetching.

Collectors preserve feed/API metadata and avoid editorial decisions. Any
summary field comes directly from the upstream source.
"""

from __future__ import annotations

import calendar
import html
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import feedparser
import httpx

from dtns.collectors.models import RawArticle, SourceType
from dtns.contracts.content import (
    EditorialSourceType,
    SourceMetadata,
    SourcePriority,
)


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


@dataclass(frozen=True)
class XSource:
    username: str

    @property
    def name(self) -> str:
        return f"X: @{self.username}"

    @property
    def url(self) -> str:
        return f"https://x.com/{self.username}"


DEFAULT_FEED_SOURCES = (
    FeedSource("OpenAI News", "https://openai.com/news/rss.xml", SourceType.RSS),
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
    FeedSource(
        "AWS Architecture Blog",
        "https://aws.amazon.com/blogs/architecture/feed/",
        SourceType.RSS,
    ),
    FeedSource("Spring Blog", "https://spring.io/blog.atom", SourceType.ATOM),
    FeedSource("Kubernetes Blog", "https://kubernetes.io/feed.xml"),
    FeedSource(
        "OpenTelemetry Blog",
        "https://opentelemetry.io/blog/index.xml",
    ),
    FeedSource(
        "Unity LTS Releases",
        "https://unity.com/releases/editor/lts-releases.xml",
        SourceType.RSS,
    ),
    FeedSource(
        "Unreal Engine",
        "https://www.unrealengine.com/rss",
        SourceType.RSS,
    ),
    FeedSource("Godot Engine", "https://godotengine.org/rss.xml", SourceType.RSS),
    FeedSource("AMD GPUOpen", "https://gpuopen.com/feed.xml", SourceType.RSS),
    FeedSource(
        "NVIDIA Developer Blog: Graphics",
        "https://developer.nvidia.com/blog/category/graphics/feed/",
        SourceType.ATOM,
    ),
    FeedSource(
        "Microsoft DirectX Developer Blog",
        "https://devblogs.microsoft.com/directx/feed/",
        SourceType.RSS,
    ),
    FeedSource(
        "Game Developer",
        "https://www.gamedeveloper.com/rss.xml",
        SourceType.RSS,
    ),
    FeedSource(
        "Game From Scratch",
        "https://gamefromscratch.com/feed/",
        SourceType.RSS,
    ),
    FeedSource(
        "How To Market A Game",
        "https://howtomarketagame.com/feed/",
        SourceType.RSS,
    ),
    FeedSource(
        "itch.io Devlogs",
        "https://itch.io/devlogs.xml",
        SourceType.RSS,
    ),
    FeedSource(
        "Android Developers Blog: Games",
        "https://android-developers.googleblog.com/feeds/posts/default/-/Games",
        SourceType.ATOM,
    ),
    FeedSource(
        "Apple Developer News",
        "https://developer.apple.com/news/rss/news.rss",
        SourceType.RSS,
    ),
    FeedSource(
        "Hugging Face Blog",
        "https://huggingface.co/blog/feed.xml",
        SourceType.RSS,
    ),
    FeedSource("Inside.java", "https://inside.java/feed.xml"),
    FeedSource(
        "우아한형제들 기술블로그",
        "https://techblog.woowahan.com/feed/",
        SourceType.RSS,
    ),
    FeedSource("NAVER D2", "https://d2.naver.com/d2.atom", SourceType.ATOM),
    FeedSource(
        "LINE Engineering",
        "https://engineering.linecorp.com/ko/feed/",
        SourceType.RSS,
    ),
    FeedSource("Toss Tech", "https://toss.tech/rss.xml", SourceType.RSS),
    FeedSource(
        "당근 기술 블로그",
        "https://medium.com/feed/daangn",
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
        "GDC Vault",
        "https://gdcvault.com/free/recent/?media=va",
        "gdc_vault",
    ),
    HtmlSource(
        "Advances in Real-Time Rendering",
        "https://www.advances.realtimerendering.com/",
        "realtime_rendering",
    ),
    HtmlSource(
        "LinkedIn Engineering: Feed",
        "https://www.linkedin.com/blog/engineering/feed",
        "linkedin_engineering_feed",
    ),
    HtmlSource(
        "GitHub Trending (weekly)",
        "https://github.com/trending?since=weekly",
        "github_trending",
    ),
    HtmlSource(
        "80 LEVEL",
        "https://80.lv/",
        "eighty_level",
    ),
)

DEFAULT_X_SOURCES = (
    XSource("dair_ai"),
    XSource("Weyaxi"),
    XSource("rasbt"),
    XSource("karpathy"),
    XSource("huggingface"),
)


_HIGH_ENGINEERING_SOURCES = {
    "AWS Architecture Blog",
    "Cloudflare Blog",
    "GitHub Engineering",
    "LINE Engineering",
    "LinkedIn Engineering: Feed",
    "Meta Engineering",
    "NAVER D2",
    "Netflix TechBlog",
    "Toss Tech",
    "당근 기술 블로그",
    "우아한형제들 기술블로그",
}
_HIGH_DEVELOPER_SOURCES = {
    "AMD GPUOpen",
    "Android Developers Blog: Games",
    "Apple Developer News",
    "Microsoft DirectX Developer Blog",
    "NVIDIA Developer Blog: Graphics",
    "Unreal Engine",
}
_HIGH_SYNTHESIS_SOURCES = {"Martin Fowler"}
_HIGH_RESEARCH_SOURCES = {"Hugging Face Blog", "OpenAI News"}
_HIGH_CONFERENCE_SOURCES = {"Advances in Real-Time Rendering", "GDC Vault"}
_MEDIUM_TECHNICAL_MEDIA_SOURCES = {
    "80 LEVEL",
    "Game Developer",
    "InfoQ",
    "The New Stack",
}


def _source_metadata(
    source: FeedSource | GitHubReleaseSource | HtmlSource | XSource,
) -> SourceMetadata:
    """Return deterministic editorial provenance for every configured source."""

    if isinstance(source, GitHubReleaseSource) or source.name in {
        "Godot Engine",
        "Inside.java",
        "Kubernetes Blog",
        "OpenTelemetry Blog",
        "PostgreSQL News",
        "Spring Blog",
        "Unity LTS Releases",
    }:
        source_type = EditorialSourceType.OFFICIAL_RELEASE
        priority = SourcePriority.HIGH
    elif source.name in _HIGH_ENGINEERING_SOURCES:
        source_type = EditorialSourceType.ENGINEERING_BLOG
        priority = SourcePriority.HIGH
    elif source.name in _HIGH_DEVELOPER_SOURCES:
        source_type = EditorialSourceType.DEVELOPER_BLOG
        priority = SourcePriority.HIGH
    elif source.name in _HIGH_SYNTHESIS_SOURCES:
        source_type = EditorialSourceType.TECHNICAL_SYNTHESIS
        priority = SourcePriority.HIGH
    elif source.name in _HIGH_RESEARCH_SOURCES:
        source_type = EditorialSourceType.RESEARCH_LAB
        priority = SourcePriority.HIGH
    elif source.name in _HIGH_CONFERENCE_SOURCES:
        source_type = EditorialSourceType.CONFERENCE
        priority = SourcePriority.HIGH
    elif source.name in _MEDIUM_TECHNICAL_MEDIA_SOURCES:
        source_type = EditorialSourceType.TECHNICAL_MEDIA
        priority = SourcePriority.MEDIUM
    else:
        source_type = EditorialSourceType.DISCOVERY
        priority = SourcePriority.LOW

    return SourceMetadata(
        source_type=source_type,
        source_priority=priority,
        source_name=source.name,
        source_url=source.url,
        is_discovery=source_type == EditorialSourceType.DISCOVERY,
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


def default_x_sources() -> tuple[XSource, ...]:
    return DEFAULT_X_SOURCES


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
                source_metadata=_source_metadata(source),
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
                source_metadata=_source_metadata(source),
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
            source_metadata=_source_metadata(source),
            title=title,
            url=urljoin(source.url, href),
            collected_at=collected_at,
            raw={"href": href},
        )
        for href, title in _limited(parser.articles, limit)
    ]


def fetch_x_articles(
    client: httpx.Client,
    source: XSource,
    collected_at: datetime,
    bearer_token: str,
    *,
    limit: int | None = None,
) -> list[RawArticle]:
    """Fetch recent public posts from one account through the official X API."""

    max_results = max(10, min(limit or 10, 100))
    response = client.get(
        "https://api.x.com/2/tweets/search/recent",
        headers={"Authorization": f"Bearer {bearer_token}"},
        params={
            "query": f"from:{source.username} -is:retweet",
            "max_results": max_results,
            "tweet.fields": "created_at,entities",
        },
    )
    response.raise_for_status()
    payload = response.json()
    posts = payload.get("data", []) if isinstance(payload, dict) else []

    articles: list[RawArticle] = []
    for post in _limited(posts, limit):
        post_id = str(post.get("id", "")).strip()
        text = _clean_text(post.get("text"))
        if not post_id or not text:
            continue
        title = " ".join(text.split())
        if len(title) > 160:
            title = f"{title[:157].rstrip()}..."
        published_at = None
        if post.get("created_at"):
            published_at = datetime.fromisoformat(
                str(post["created_at"]).replace("Z", "+00:00")
            )
        articles.append(
            RawArticle(
                source=source.name,
                source_type=SourceType.API,
                source_metadata=_source_metadata(source),
                title=title,
                url=f"https://x.com/{source.username}/status/{post_id}",
                summary=text,
                author=f"@{source.username}",
                published_at=published_at,
                collected_at=collected_at,
                raw=post,
            )
        )
    return articles


class _ArticleLinkParser(HTMLParser):
    def __init__(self, parser: str) -> None:
        super().__init__(convert_charrefs=True)
        self.parser = parser
        self.articles: list[tuple[str, str]] = []
        self._seen_hrefs: set[str] = set()
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
            if tag == "img" and attributes.get("alt"):
                self._text.append(attributes["alt"] or "")
            if tag in {
                "area",
                "base",
                "br",
                "col",
                "embed",
                "hr",
                "img",
                "input",
                "link",
                "meta",
                "source",
                "track",
                "wbr",
            }:
                return
            self._capture_depth += 1
            return
        if tag != "a":
            return
        href = attributes.get("href")
        is_trending_repository = (
            self.parser == "github_trending"
            and self._in_article
            and bool(href)
            and href.count("/") == 2
            and not href.startswith(("/sponsors/", "/topics/"))
        )
        normalized_href = urlparse(href or "").path.lower().lstrip("/")
        is_gdc_session = (
            self.parser == "gdc_vault"
            and bool(href)
            and normalized_href.startswith("play/")
        )
        is_rendering_course = (
            self.parser == "realtime_rendering"
            and bool(href)
            and normalized_href.startswith("s20")
            and normalized_href.endswith(("/", ".html", ".htm"))
        )
        is_linkedin_article = (
            self.parser == "linkedin_engineering_feed"
            and "grid-post__link" in classes
            and bool(href)
        )
        is_eighty_level_article = (
            self.parser == "eighty_level"
            and bool(href)
            and normalized_href.startswith("articles/")
            and normalized_href != "articles/category"
            and normalized_href.count("/") == 1
        )
        if (
            is_trending_repository
            or is_gdc_session
            or is_rendering_course
            or is_linkedin_article
            or is_eighty_level_article
        ):
            self._capture_depth = 1
            self._href = href
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture_depth:
            if tag == "a" and self._capture_depth == 1:
                title = " ".join("".join(self._text).split())
                if self._href and title and self._href not in self._seen_hrefs:
                    self.articles.append((self._href, title))
                    self._seen_hrefs.add(self._href)
                self._capture_depth = 0
                self._href = None
                self._text = []
            else:
                self._capture_depth -= 1
        if self.parser == "github_trending" and tag == "article":
            self._in_article = False

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self._capture_depth and tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self._text.append(alt)

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
    parser = _PlainTextParser()
    parser.feed(html.unescape(str(value)))
    parser.close()
    text = " ".join("".join(parser.parts).split())
    return text or None


class _PlainTextParser(HTMLParser):
    _BLOCK_TAGS = {
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in self._BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


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
