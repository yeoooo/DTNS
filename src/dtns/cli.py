"""CLI contract for the DTNS newsletter pipeline.

The command surface is intentionally defined before full implementation so each
stage can be implemented independently in future sessions.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from dtns.pipeline import PipelineStage, run_pipeline


TOPICS = ("technology", "backend", "qa")
RUN_ALL_LIMIT_PER_SOURCE = 10
ARTICLES_FILENAME = "articles.json"
NORMALIZED_ARTICLES_FILENAME = "normalized_articles.json"
TAGGED_ARTICLES_FILENAME = "tagged_articles.json"
TOPIC_ARTICLES_FILENAME_TEMPLATE = "{topic}_articles.json"
TOPIC_TRENDS_FILENAME_TEMPLATE = "{topic}_trends.json"
NEWSLETTER_FILENAME_TEMPLATE = "{topic}_newsletter.md"
ArtifactModel = TypeVar("ArtifactModel", bound=BaseModel)


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
            configuration=_collector_configuration(limit_per_source),
            validate_outputs=_validate_articles,
        ),
        PipelineStage(
            stage_id="preprocess",
            action=lambda: _run_preprocess(data_dir),
            inputs=(article_path,),
            outputs=lambda: (normalized_path,),
            configuration=_preprocessor_configuration(),
            validate_outputs=_validate_normalized_articles,
            dependencies=("collect",),
        ),
        PipelineStage(
            stage_id="tag",
            action=lambda: _run_tag(data_dir, run_id=run_id),
            inputs=(normalized_path,),
            outputs=lambda: (tagged_path,),
            configuration=_ai_configuration("TAGGER"),
            validate_outputs=_validate_tagged_articles,
            dependencies=("preprocess",),
        ),
        PipelineStage(
            stage_id="classify",
            action=lambda: _run_classify(data_dir),
            inputs=(tagged_path,),
            outputs=lambda: tuple(topic_article_paths),
            configuration=_classifier_configuration(),
            validate_outputs=_validate_topic_articles,
            dependencies=("tag",),
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
                    dependencies=("classify",),
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
                    dependencies=(f"trend:{topic}",),
                ),
                PipelineStage(
                    stage_id=f"publish:{topic}",
                    action=lambda topic=topic: _run_publish(
                        data_dir, topic, run_id=run_id
                    ),
                    inputs=(newsletter_path,),
                    outputs=lambda topic=topic, path=newsletter_path: (
                        _completed_publish_receipt(data_dir, topic, path),
                    ),
                    configuration=_publisher_configuration(topic),
                    validate_outputs=(
                        lambda paths, topic=topic, newsletter=newsletter_path: (
                            _validate_publish_receipt(paths, topic, newsletter)
                        )
                    ),
                    dependencies=(f"edit:{topic}",),
                ),
            ]
        )
    run_pipeline(data_dir, run_id, stages)


def _ai_configuration(stage: str, *, topic: str | None = None) -> dict[str, str]:
    if stage == "TAGGER":
        from dtns.agents.tagger.stage import tagger_policy_fingerprint

        fingerprint = tagger_policy_fingerprint()
    elif stage == "TREND" and topic is not None:
        from dtns.agents.trend.runner import trend_policy_fingerprint

        fingerprint = trend_policy_fingerprint(topic)
    elif stage == "EDITOR" and topic is not None:
        from dtns.agents.editor.runner import editor_policy_fingerprint

        fingerprint = editor_policy_fingerprint(topic)
    else:
        raise ValueError("Unknown AI stage configuration")
    return {"topic": topic or "", "policy_fingerprint": fingerprint}


def _collector_configuration(limit_per_source: int | None) -> dict[str, str]:
    from dtns.collectors.runner import collector_policy_fingerprint

    return {
        "policy_fingerprint": collector_policy_fingerprint(
            limit_per_source=limit_per_source
        )
    }


def _preprocessor_configuration() -> dict[str, str]:
    from dtns.preprocessors.stage import preprocessor_policy_fingerprint

    return {"policy_fingerprint": preprocessor_policy_fingerprint()}


def _classifier_configuration() -> dict[str, str]:
    from dtns.classifier.stage import classifier_policy_fingerprint

    return {"policy_fingerprint": classifier_policy_fingerprint()}


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
    from dtns.publisher.stage import build_delivery_content

    delivery_bytes = build_delivery_content(
        newsletter_path.read_text(encoding="utf-8")
    ).encode("utf-8")
    newsletter_fingerprint = hashlib.sha256(delivery_bytes).hexdigest()
    webhook_fingerprint = _publisher_configuration(topic)["webhook_fingerprint"]
    path = (
        data_dir
        / ".state"
        / "publisher"
        / topic
        / f"{newsletter_fingerprint}.{webhook_fingerprint}.json"
    )
    if not _publish_receipt_matches(path, topic, newsletter_path):
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

    _validate_pydantic_artifact(RawArticlesFile, paths[0], "RawArticlesFile")


def _validate_normalized_articles(paths: tuple[Path, ...] | list[Path]) -> None:
    from dtns.contracts.tagged_articles import NormalizedArticlesDocument

    _validate_pydantic_artifact(
        NormalizedArticlesDocument,
        paths[0],
        "NormalizedArticlesDocument",
    )


def _validate_tagged_articles(paths: tuple[Path, ...] | list[Path]) -> None:
    from dtns.contracts.tagged_articles import TaggedArticlesDocument

    _validate_pydantic_artifact(
        TaggedArticlesDocument,
        paths[0],
        "TaggedArticlesDocument",
    )


def _validate_topic_articles(paths: tuple[Path, ...] | list[Path]) -> None:
    from dtns.classifier.stage import TopicArticlesDocument

    for path, topic in zip(paths, TOPICS, strict=True):
        document = _validate_pydantic_artifact(
            TopicArticlesDocument,
            path,
            "TopicArticlesDocument",
        )
        if document.topic != topic:
            raise ValueError("Classifier topic artifact mismatch")


def _validate_trends(
    paths: tuple[Path, ...] | list[Path], topic: str
) -> None:
    from dtns.agents.trend.runner import TrendsFile

    document = _validate_pydantic_artifact(TrendsFile, paths[0], "TrendsFile")
    if document.topic != topic:
        raise ValueError("Trend artifact topic mismatch")


def _validate_newsletter(
    paths: tuple[Path, ...] | list[Path], articles_path: Path
) -> None:
    from dtns.agents.editor.runner import (
        TopicArticlesFile,
        normalize_markdown,
        validate_markdown,
    )

    articles = _validate_pydantic_artifact(
        TopicArticlesFile,
        articles_path,
        "TopicArticlesFile",
    )
    known_urls = {article.canonical_url for article in articles.articles}
    markdown = paths[0].read_text(encoding="utf-8").strip()
    normalized = normalize_markdown(markdown)
    if normalized != markdown:
        raise ValueError("Newsletter artifact is not normalized")
    validate_markdown(normalized, known_urls=known_urls)


def _validate_pydantic_artifact(
    model: type[ArtifactModel],
    path: Path,
    contract_name: str,
) -> ArtifactModel:
    try:
        return model.model_validate_json(path.read_bytes())
    except ValidationError as error:
        failures = "; ".join(
            f"{'.'.join(_safe_error_location(part) for part in failure['loc']) or '<root>'}: "
            f"{failure['msg']}"
            for failure in error.errors(include_input=False, include_url=False)
        )
        raise ValueError(
            f"Invalid {contract_name} artifact at {path}: {failures}"
        ) from None


def _safe_error_location(part: str | int) -> str:
    value = str(part)
    if isinstance(part, int) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", value):
        return value
    return "<field>"


def _validate_publish_receipt(
    paths: tuple[Path, ...] | list[Path],
    topic: str,
    newsletter_path: Path,
) -> None:
    if not _publish_receipt_matches(paths[0], topic, newsletter_path):
        raise ValueError("Publish receipt does not match the current delivery")


def _publish_receipt_matches(
    receipt_path: Path,
    topic: str,
    newsletter_path: Path,
) -> bool:
    from dtns.publisher.receipt import read_publish_receipt
    from dtns.publisher.stage import build_delivery_content, split_discord_messages

    receipt = read_publish_receipt(receipt_path)
    if receipt is None:
        return False
    delivery_content = build_delivery_content(
        newsletter_path.read_text(encoding="utf-8")
    )
    newsletter_fingerprint = hashlib.sha256(
        delivery_content.encode("utf-8")
    ).hexdigest()
    webhook_fingerprint = _publisher_configuration(topic)["webhook_fingerprint"]
    messages = split_discord_messages(delivery_content)
    if (
        receipt.status != "completed"
        or receipt.topic != topic
        or receipt.newsletter_fingerprint != newsletter_fingerprint
        or receipt.webhook_fingerprint != webhook_fingerprint
        or len(receipt.chunks) != len(messages)
    ):
        return False
    return all(
        chunk.index == index
        and chunk.fingerprint
        == hashlib.sha256(message.encode("utf-8")).hexdigest()
        and chunk.character_count == len(message)
        and chunk.status == "delivered"
        for index, (chunk, message) in enumerate(
            zip(receipt.chunks, messages, strict=True)
        )
    )


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is project-level.
        return
    load_dotenv()


if __name__ == "__main__":
    raise SystemExit(main())
