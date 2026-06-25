"""Run the classifier stage directly."""

from __future__ import annotations

import argparse
from pathlib import Path

from dtns.classifier.stage import TAGGED_ARTICLES_FILENAME, classify_articles


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m dtns.classifier")
    parser.add_argument(
        "--data-dir",
        default=Path("data"),
        type=Path,
        help="Directory containing tagged_articles.json and receiving topic files.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Explicit input JSON path. Defaults to DATA_DIR/tagged_articles.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Explicit output directory. Defaults to DATA_DIR.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input or args.data_dir / TAGGED_ARTICLES_FILENAME
    output_dir = args.output_dir or args.data_dir
    classify_articles(input_path, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
