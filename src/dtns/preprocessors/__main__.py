"""Run the preprocessor stage directly."""

from __future__ import annotations

import argparse
from pathlib import Path

from dtns.preprocessors.stage import (
    ARTICLES_FILENAME,
    NORMALIZED_ARTICLES_FILENAME,
    preprocess,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dtns.preprocessors")
    parser.add_argument(
        "--data-dir",
        default=Path("data"),
        type=Path,
        help=(
            "Directory containing articles.json and receiving "
            "normalized_articles.json."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Explicit input JSON path. Defaults to DATA_DIR/articles.json.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Explicit output JSON path. Defaults to "
            "DATA_DIR/normalized_articles.json."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input or args.data_dir / ARTICLES_FILENAME
    output_path = args.output or args.data_dir / NORMALIZED_ARTICLES_FILENAME
    preprocess(input_path, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
