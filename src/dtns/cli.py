"""CLI contract for the DTNS newsletter pipeline.

The command surface is intentionally defined before full implementation so each
stage can be implemented independently in future sessions.
"""

from __future__ import annotations

import argparse
from pathlib import Path


TOPICS = ("technology", "backend", "qa")
RUN_ALL_LIMIT_PER_SOURCE = 10
ARTICLES_FILENAME = "articles.json"
NORMALIZED_ARTICLES_FILENAME = "normalized_articles.json"
TAGGED_ARTICLES_FILENAME = "tagged_articles.json"
TOPIC_ARTICLES_FILENAME_TEMPLATE = "{topic}_articles.json"
TOPIC_TRENDS_FILENAME_TEMPLATE = "{topic}_trends.json"
NEWSLETTER_FILENAME_TEMPLATE = "{topic}_newsletter.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="newsletter")
    parser.add_argument(
        "--data-dir",
        default="data",
        type=Path,
        help="Directory for pipeline input and output files.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument(
        "--limit-per-source",
        type=int,
        default=None,
        help="Maximum articles to collect from each source.",
    )

    for command in ("preprocess", "tag", "classify"):
        subparsers.add_parser(command)

    run_all_parser = subparsers.add_parser("run-all")
    run_all_parser.add_argument(
        "--limit-per-source",
        type=int,
        default=RUN_ALL_LIMIT_PER_SOURCE,
        help="Maximum articles to collect from each source.",
    )

    for command in ("trend", "edit", "publish"):
        topic_parser = subparsers.add_parser(command)
        topic_parser.add_argument("--topic", required=True, choices=TOPICS)

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    data_dir = args.data_dir

    if args.command == "collect":
        _run_collect(data_dir, limit_per_source=args.limit_per_source)
        return 0

    if args.command == "preprocess":
        _run_preprocess(data_dir)
        return 0

    if args.command == "tag":
        _run_tag(data_dir)
        return 0

    if args.command == "classify":
        _run_classify(data_dir)
        return 0

    if args.command == "trend":
        _run_trend(data_dir, args.topic)
        return 0

    if args.command == "edit":
        _run_edit(data_dir, args.topic)
        return 0

    if args.command == "publish":
        _run_publish(data_dir, args.topic)
        return 0

    if args.command == "run-all":
        _run_collect(data_dir, limit_per_source=args.limit_per_source)
        _run_preprocess(data_dir)
        _run_tag(data_dir)
        _run_classify(data_dir)
        for topic in TOPICS:
            _run_trend(data_dir, topic)
            _run_edit(data_dir, topic)
            _run_publish(data_dir, topic)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _run_collect(data_dir: Path, *, limit_per_source: int | None = None) -> None:
    from dtns.collectors.runner import write_articles

    write_articles(
        data_dir / ARTICLES_FILENAME,
        limit_per_source=limit_per_source,
    )


def _run_preprocess(data_dir: Path) -> None:
    from dtns.preprocessors import preprocess

    preprocess(
        data_dir / ARTICLES_FILENAME,
        data_dir / NORMALIZED_ARTICLES_FILENAME,
    )


def _run_tag(data_dir: Path) -> None:
    from dtns.agents.tagger import tag_articles

    tag_articles(
        data_dir / NORMALIZED_ARTICLES_FILENAME,
        data_dir / TAGGED_ARTICLES_FILENAME,
    )


def _run_classify(data_dir: Path) -> None:
    from dtns.classifier import classify_articles

    classify_articles(data_dir / TAGGED_ARTICLES_FILENAME, data_dir)


def _run_trend(data_dir: Path, topic: str) -> None:
    from dtns.agents.trend import discover_trends

    discover_trends(
        topic,
        data_dir / TOPIC_ARTICLES_FILENAME_TEMPLATE.format(topic=topic),
        data_dir / TOPIC_TRENDS_FILENAME_TEMPLATE.format(topic=topic),
    )


def _run_edit(data_dir: Path, topic: str) -> None:
    from dtns.agents.editor import write_newsletter

    write_newsletter(
        data_dir / TOPIC_TRENDS_FILENAME_TEMPLATE.format(topic=topic),
        data_dir / NEWSLETTER_FILENAME_TEMPLATE.format(topic=topic),
        articles_path=data_dir / TOPIC_ARTICLES_FILENAME_TEMPLATE.format(topic=topic),
    )


def _run_publish(data_dir: Path, topic: str) -> None:
    from typing import cast

    from dtns.publisher.stage import Topic, publish_topic_newsletter

    publish_topic_newsletter(data_dir, cast(Topic, topic))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is project-level.
        return
    load_dotenv()


if __name__ == "__main__":
    raise SystemExit(main())
