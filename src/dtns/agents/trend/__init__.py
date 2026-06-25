"""Trend Agent package."""

from .runner import (
    TOPIC_ARTICLES_FILENAME,
    TOPIC_TRENDS_FILENAME,
    discover_trends,
)

__all__ = [
    "TOPIC_ARTICLES_FILENAME",
    "TOPIC_TRENDS_FILENAME",
    "discover_trends",
]
