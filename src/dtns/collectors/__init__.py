"""Article collector module."""

from dtns.collectors.runner import (
    collect_articles,
    collection_report_path,
    write_articles,
    write_collection_report,
)

__all__ = [
    "collect_articles",
    "collection_report_path",
    "write_articles",
    "write_collection_report",
]
