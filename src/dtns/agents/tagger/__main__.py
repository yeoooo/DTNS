"""Run the tagger stage directly."""

from __future__ import annotations

import argparse
from pathlib import Path

from dtns.agents.tagger.stage import (
    NORMALIZED_ARTICLES_FILENAME,
    TAGGED_ARTICLES_FILENAME,
    MODEL_ENV_VAR,
    tag_articles,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dtns.agents.tagger")
    parser.add_argument(
        "--data-dir",
        default=Path("data"),
        type=Path,
        help=(
            "Directory containing normalized_articles.json and receiving "
            "tagged_articles.json."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Explicit input JSON path. Defaults to DATA_DIR/normalized_articles.json.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Explicit output JSON path. Defaults to DATA_DIR/tagged_articles.json.",
    )
    parser.add_argument(
        "--model",
        help=(
            f"LLM model to use. Defaults to ${MODEL_ENV_VAR}, $GEMINI_MODEL, "
            "or the stage default."
        ),
    )
    parser.add_argument(
        "--run-id",
        help="Resumable run ID. Defaults to an input and policy fingerprint ID.",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        help="Explicit checkpoint directory for the selected run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input or args.data_dir / NORMALIZED_ARTICLES_FILENAME
    output_path = args.output or args.data_dir / TAGGED_ARTICLES_FILENAME
    tag_articles(
        input_path,
        output_path,
        model=args.model,
        run_id=args.run_id,
        state_path=args.state_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
