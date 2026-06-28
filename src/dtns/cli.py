"""CLI contract for the DTNS newsletter pipeline.

The command surface is intentionally defined before full implementation so each
stage can be implemented independently in future sessions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from dtns.pipeline import PipelineStage, run_pipeline


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
    run_all_parser.add_argument(
        "--run-id",
        default=None,
        help="Pipeline run ID to create or resume. Defaults to a new UUID.",
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
        _run_all(
            data_dir,
            run_id=args.run_id or uuid4().hex,
            limit_per_source=args.limit_per_source,
        )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _run_collect(
    data_dir: Path,
    *,
    limit_per_source: int | None = None,
    run_id: str | None = None,
) -> None:
    from dtns.collectors.runner import write_articles

    write_articles(
        data_dir / ARTICLES_FILENAME,
        limit_per_source=limit_per_source,
        source_run_id=run_id,
    )


def _run_preprocess(data_dir: Path) -> None:
    from dtns.preprocessors import preprocess

    preprocess(
        data_dir / ARTICLES_FILENAME,
        data_dir / NORMALIZED_ARTICLES_FILENAME,
    )


def _run_tag(data_dir: Path, *, run_id: str | None = None) -> None:
    from dtns.agents.tagger import tag_articles

    tag_articles(
        data_dir / NORMALIZED_ARTICLES_FILENAME,
        data_dir / TAGGED_ARTICLES_FILENAME,
        run_id=run_id,
    )


def _run_classify(data_dir: Path) -> None:
    from dtns.classifier import classify_articles

    classify_articles(data_dir / TAGGED_ARTICLES_FILENAME, data_dir)


def _run_trend(
    data_dir: Path, topic: str, *, run_id: str | None = None
) -> None:
    from dtns.agents.trend import discover_trends

    discover_trends(
        topic,
        data_dir / TOPIC_ARTICLES_FILENAME_TEMPLATE.format(topic=topic),
        data_dir / TOPIC_TRENDS_FILENAME_TEMPLATE.format(topic=topic),
        run_id=run_id,
    )


def _run_edit(
    data_dir: Path, topic: str, *, run_id: str | None = None
) -> None:
    from dtns.agents.editor import write_newsletter

    write_newsletter(
        data_dir / TOPIC_TRENDS_FILENAME_TEMPLATE.format(topic=topic),
        data_dir / NEWSLETTER_FILENAME_TEMPLATE.format(topic=topic),
        articles_path=data_dir / TOPIC_ARTICLES_FILENAME_TEMPLATE.format(topic=topic),
        run_id=run_id,
    )


def _run_publish(
    data_dir: Path, topic: str, *, run_id: str | None = None
) -> None:
    from typing import cast

    from dtns.publisher.stage import Topic, publish_topic_newsletter

    publish_topic_newsletter(data_dir, cast(Topic, topic), run_id=run_id)


def _run_all(
    data_dir: Path,
    *,
    run_id: str,
    limit_per_source: int,
) -> None:
    article_path = data_dir / ARTICLES_FILENAME
    normalized_path = data_dir / NORMALIZED_ARTICLES_FILENAME
    tagged_path = data_dir / TAGGED_ARTICLES_FILENAME
    topic_article_paths = [
        data_dir / TOPIC_ARTICLES_FILENAME_TEMPLATE.format(topic=topic)
        for topic in TOPICS
    ]
    stages = [
        PipelineStage(
            stage_id="collect",
            action=lambda: _run_collect(
                data_dir,
                limit_per_source=limit_per_source,
                run_id=run_id,
            ),
            inputs=(),
            outputs=lambda: (article_path,),
            configuration={"limit_per_source": limit_per_source},
            validate_outputs=_validate_articles,
        ),
        PipelineStage(
            stage_id="preprocess",
            action=lambda: _run_preprocess(data_dir),
            inputs=(article_path,),
            outputs=lambda: (normalized_path,),
            validate_outputs=_validate_normalized_articles,
        ),
        PipelineStage(
            stage_id="tag",
            action=lambda: _run_tag(data_dir, run_id=run_id),
            inputs=(normalized_path,),
            outputs=lambda: (tagged_path,),
            configuration=_ai_configuration("TAGGER"),
            validate_outputs=_validate_tagged_articles,
        ),
        PipelineStage(
            stage_id="classify",
            action=lambda: _run_classify(data_dir),
            inputs=(tagged_path,),
            outputs=lambda: tuple(topic_article_paths),
            validate_outputs=_validate_topic_articles,
        ),
    ]
    for topic, topic_articles_path in zip(TOPICS, topic_article_paths, strict=True):
        trends_path = data_dir / TOPIC_TRENDS_FILENAME_TEMPLATE.format(topic=topic)
        newsletter_path = data_dir / NEWSLETTER_FILENAME_TEMPLATE.format(topic=topic)
        stages.extend(
            [
                PipelineStage(
                    stage_id=f"trend:{topic}",
                    action=lambda topic=topic: _run_trend(
                        data_dir, topic, run_id=run_id
                    ),
                    inputs=(topic_articles_path,),
                    outputs=lambda path=trends_path: (path,),
                    configuration=_ai_configuration("TREND", topic=topic),
                    validate_outputs=lambda paths, topic=topic: _validate_trends(
                        paths, topic
                    ),
                ),
                PipelineStage(
                    stage_id=f"edit:{topic}",
                    action=lambda topic=topic: _run_edit(
                        data_dir, topic, run_id=run_id
                    ),
                    inputs=(trends_path, topic_articles_path),
                    outputs=lambda path=newsletter_path: (path,),
                    configuration=_ai_configuration("EDITOR", topic=topic),
                    validate_outputs=lambda paths, articles=topic_articles_path: (
                        _validate_newsletter(paths, articles)
                    ),
                ),
                PipelineStage(
                    stage_id=f"publish:{topic}",
                    action=lambda topic=topic: _run_publish(
                        data_dir, topic, run_id=run_id
                    ),
                    inputs=(newsletter_path,),
                    outputs=lambda topic=topic, path=newsletter_path: (
                        _completed_publish_receipt(data_dir, topic, path)
                    ),
                    configuration=_publisher_configuration(topic),
                    validate_outputs=_validate_publish_receipt,
                ),
            ]
        )
    run_pipeline(data_dir, run_id, stages)


def _ai_configuration(stage: str, *, topic: str | None = None) -> dict[str, str]:
    return {
        "topic": topic or "",
        "model": os.getenv(f"DTNS_{stage}_MODEL", ""),
        "gemini_model": os.getenv("GEMINI_MODEL", ""),
        "fallback_model": os.getenv("GEMINI_FALLBACK_MODEL", ""),
    }


def _publisher_configuration(topic: str) -> dict[str, str]:
    env_names = {
        "technology": "DISCORD_WEBHOOK_TECHNOLOGY",
        "backend": "DISCORD_WEBHOOK_BACKEND",
        "qa": "DISCORD_WEBHOOK_QA",
    }
    webhook = os.getenv(env_names[topic], "").strip()
    return {
        "topic": topic,
        "webhook_fingerprint": hashlib.sha256(
            _normalize_webhook_url(webhook).encode("utf-8")
        ).hexdigest(),
    }


def _completed_publish_receipt(
    data_dir: Path,
    topic: str,
    newsletter_path: Path,
) -> Path:
    newsletter_fingerprint = hashlib.sha256(newsletter_path.read_bytes()).hexdigest()
    webhook_fingerprint = _publisher_configuration(topic)["webhook_fingerprint"]
    path = (
        data_dir
        / ".state"
        / "publisher"
        / topic
        / f"{newsletter_fingerprint}.{webhook_fingerprint}.json"
    )
    from dtns.publisher.receipt import read_publish_receipt

    receipt = read_publish_receipt(path)
    if receipt is None or receipt.status != "completed":
        return Path()
    return path


def _normalize_webhook_url(webhook_url: str) -> str:
    parsed = urlsplit(webhook_url)
    normalized_path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            parsed.query,
            "",
        )
    )


def _validate_articles(paths: tuple[Path, ...] | list[Path]) -> None:
    from dtns.preprocessors.stage import RawArticlesFile

    RawArticlesFile.model_validate(json.loads(paths[0].read_bytes()))


def _validate_normalized_articles(paths: tuple[Path, ...] | list[Path]) -> None:
    from dtns.contracts.tagged_articles import NormalizedArticlesDocument

    NormalizedArticlesDocument.model_validate(json.loads(paths[0].read_bytes()))


def _validate_tagged_articles(paths: tuple[Path, ...] | list[Path]) -> None:
    from dtns.contracts.tagged_articles import TaggedArticlesDocument

    TaggedArticlesDocument.model_validate(json.loads(paths[0].read_bytes()))


def _validate_topic_articles(paths: tuple[Path, ...] | list[Path]) -> None:
    from dtns.classifier.stage import TopicArticlesDocument

    for path, topic in zip(paths, TOPICS, strict=True):
        document = TopicArticlesDocument.model_validate(json.loads(path.read_bytes()))
        if document.topic != topic:
            raise ValueError("Classifier topic artifact mismatch")


def _validate_trends(
    paths: tuple[Path, ...] | list[Path], topic: str
) -> None:
    from dtns.agents.trend.runner import TrendsFile

    document = TrendsFile.model_validate(json.loads(paths[0].read_bytes()))
    if document.topic != topic:
        raise ValueError("Trend artifact topic mismatch")


def _validate_newsletter(
    paths: tuple[Path, ...] | list[Path], articles_path: Path
) -> None:
    from dtns.agents.editor.runner import TopicArticlesFile, validate_markdown

    articles = TopicArticlesFile.model_validate(json.loads(articles_path.read_bytes()))
    known_urls = {article.canonical_url for article in articles.articles}
    validate_markdown(paths[0].read_text(encoding="utf-8").strip(), known_urls=known_urls)


def _validate_publish_receipt(paths: tuple[Path, ...] | list[Path]) -> None:
    from dtns.publisher.receipt import read_publish_receipt

    receipt = read_publish_receipt(paths[0])
    if receipt is None or receipt.status != "completed":
        raise ValueError("Publish receipt is not completed")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is project-level.
        return
    load_dotenv()


if __name__ == "__main__":
    raise SystemExit(main())
