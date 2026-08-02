from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from dtns.collectors import runner
from dtns.collectors.sources import (
    FeedSource,
    HtmlSource,
    InvalidFeedError,
    _parse_feed,
    default_html_sources,
    default_feed_sources,
    default_github_release_sources,
    fetch_html_articles,
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
    "https://softwaretestingweekly.com/issues/rss/",
    "https://www.postgresql.org/news.rss",
    "https://blog.bytebytego.com/feed",
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
        html_sources=(),
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
            html_sources=(),
        )


def test_default_sources_match_configured_source_list():
    assert {source.url for source in default_feed_sources()} == EXPECTED_FEED_URLS
    assert {
        source.url for source in default_github_release_sources()
    } == {
        "https://github.com/moby/moby/releases.atom",
        "https://github.com/redis/redis/releases.atom",
    }
    assert {source.url for source in default_html_sources()} == {
        "https://www.ministryoftesting.com/",
        "https://github.com/trending?since=weekly",
    }


@pytest.mark.parametrize(
    ("source", "html", "expected_url", "expected_title"),
    [
        (
            HtmlSource(
                "Ministry of Testing",
                "https://www.ministryoftesting.com/",
                "ministry_of_testing",
            ),
            '<div class="title"><a class="stretched-link" '
            'href="/articles/testing-well">Testing <em>Well</em></a></div>',
            "https://www.ministryoftesting.com/articles/testing-well",
            "Testing Well",
        ),
        (
            HtmlSource(
                "GitHub Trending (weekly)",
                "https://github.com/trending?since=weekly",
                "github_trending",
            ),
            '<article class="Box-row"><h2><a href="/owner/repo">'
            "owner / repo</a></h2></article>",
            "https://github.com/owner/repo",
            "owner / repo",
        ),
    ],
)
def test_fetch_html_articles_extracts_source_links(
    source, html, expected_url, expected_title
):
    class Response:
        text = html

        @staticmethod
        def raise_for_status():
            return None

    class Client:
        @staticmethod
        def get(url):
            return Response()

    articles = fetch_html_articles(
        Client(), source, runner.datetime.now(runner.UTC), limit=1
    )

    assert str(articles[0].url) == expected_url
    assert articles[0].title == expected_title
    assert articles[0].source_type.value == "html"


def test_html_response_is_not_treated_as_an_empty_feed():
    with pytest.raises(InvalidFeedError, match="valid RSS or Atom"):
        _parse_feed(b"<html><body>upstream error</body></html>")


def test_valid_empty_rss_feed_remains_a_successful_empty_feed():
    feed = _parse_feed(
        b'<?xml version="1.0"?>'
        b'<rss version="2.0"><channel><title>Empty</title>'
        b'<link>https://example.com</link><description>Empty</description>'
        b"</channel></rss>"
    )

    assert feed.version == "rss20"
    assert feed.entries == []


def test_collection_report_schema_rejects_contradictory_statuses():
    schema_path = (
        Path(__file__).parents[1]
        / "docs"
        / "contracts"
        / "collection_report.schema.json"
    )
    validator = Draft202012Validator(
        json.loads(schema_path.read_text(encoding="utf-8"))
    )
    base_payload = {
        "schema_version": "1.0",
        "run_id": "test-run",
        "source_config_fingerprint": "a" * 64,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
        "status": "completed",
        "sources": [
            {
                "name": "failed-feed",
                "source_type": "rss",
                "status": "failed",
                "fetched_count": 0,
                "accepted_count": 0,
                "http_status": 500,
                "error_category": "http_server",
            }
        ],
    }
    failed_without_error = {
        **base_payload,
        "status": "failed",
        "sources": [
            {
                **base_payload["sources"][0],
                "error_category": None,
            }
        ],
    }

    assert list(validator.iter_errors(base_payload))
    assert list(validator.iter_errors(failed_without_error))
