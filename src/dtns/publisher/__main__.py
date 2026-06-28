"""Run the Discord publisher stage directly."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from dtns.publisher.stage import (
    NEWSLETTER_FILENAME_TEMPLATE,
    WEBHOOK_ENV_VARS,
    Topic,
    publish_newsletter,
)


TOPICS = tuple(WEBHOOK_ENV_VARS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dtns.publisher")
    parser.add_argument(
        "--topic",
        required=True,
        choices=TOPICS,
        help="Newsletter topic whose Discord Webhook should receive the message.",
    )
    parser.add_argument(
        "--data-dir",
        default=Path("data"),
        type=Path,
        help="Directory containing <topic>_newsletter.md.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Explicit Markdown input path. Defaults to DATA_DIR/<topic>_newsletter.md.",
    )
    parser.add_argument(
        "--webhook-url",
        help="Explicit Discord Webhook URL. Defaults to the topic env var.",
    )
    parser.add_argument(
        "--timeout",
        default=20.0,
        type=float,
        help="HTTP timeout in seconds.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    topic = cast(Topic, args.topic)
    input_path = args.input or args.data_dir / NEWSLETTER_FILENAME_TEMPLATE.format(
        topic=topic
    )
    result = publish_newsletter(
        input_path,
        topic=topic,
        webhook_url=args.webhook_url,
        timeout_seconds=args.timeout,
        receipt_root=args.data_dir,
    )
    print(f"Published {result.message_count} Discord message(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
