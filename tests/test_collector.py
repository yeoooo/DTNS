from __future__ import annotations

import logging

import pytest

from dtns.collectors import runner
from dtns.collectors.sources import (
    FeedSource,
    default_feed_sources,
    default_github_release_sources,
)


EXPECTED_FEED_URLS = {
    "https://www.infoq.com/feed",
    "https://thenewstack.io/feed/",
    "https://martinfowler.com/feed.atom",
    "https://netflixtechblog.com/feed",
    "https://engineering.fb.com/feed/",
    "https://github.blog/engineering/feed/",
    "https://blog.cloudflare.com/rss/",
    "https://spring.io/blog.atom",
    "https://kubernetes.io/feed.xml",
    "https://opentelemetry.io/blog/index.xml",
    "https://dev.to/feed/playwright",
    "https://www.postgresql.org/news.rss",
}


def test_collect_articles_continues_when_one_feed_fails(monkeypatch, caplog):
    attempted_sources: list[str] = []

    def fetch_feed_articles(client, source, collected_at, *, limit=None):
        attempted_sources.append(source.name)
        if source.name == "unavailable":
            raise RuntimeError("404 Not Found")
        return []

    monkeypatch.setattr(runner, "fetch_feed_articles", fetch_feed_articles)
    caplog.set_level(logging.WARNING, logger=runner.__name__)

    document = runner.collect_articles(
        feed_sources=(
            FeedSource("unavailable", "https://example.com/missing.xml"),
            FeedSource("available", "https://example.com/feed.xml"),
        ),
        github_release_sources=(),
    )

    assert attempted_sources == ["unavailable", "available"]
    assert document.articles == []
    assert "Skipping unavailable feed unavailable" in caplog.text


def test_collect_articles_fails_when_all_sources_fail(monkeypatch):
    def fetch_feed_articles(client, source, collected_at, *, limit=None):
        raise RuntimeError("service unavailable")

    monkeypatch.setattr(runner, "fetch_feed_articles", fetch_feed_articles)

    with pytest.raises(RuntimeError, match="All 1 configured article sources failed"):
        runner.collect_articles(
            feed_sources=(
                FeedSource("unavailable", "https://example.com/missing.xml"),
            ),
            github_release_sources=(),
        )


def test_default_sources_match_configured_source_list():
    assert {source.url for source in default_feed_sources()} == EXPECTED_FEED_URLS
    assert {
        source.url for source in default_github_release_sources()
    } == {
        "https://github.com/moby/moby/releases.atom",
        "https://github.com/redis/redis/releases.atom",
    }
