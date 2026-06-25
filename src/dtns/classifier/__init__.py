"""Deterministic classifier stage."""

from dtns.classifier.stage import (
    TAGGED_ARTICLES_FILENAME,
    TOPIC_ARTICLES_FILENAME_TEMPLATE,
    TOPICS,
    ClassificationMetadata,
    Topic,
    TopicArticle,
    TopicArticlesDocument,
    classify_article_for_topic,
    classify_articles,
    classify_tagged_articles,
)

__all__ = [
    "TAGGED_ARTICLES_FILENAME",
    "TOPIC_ARTICLES_FILENAME_TEMPLATE",
    "TOPICS",
    "ClassificationMetadata",
    "Topic",
    "TopicArticle",
    "TopicArticlesDocument",
    "classify_article_for_topic",
    "classify_articles",
    "classify_tagged_articles",
]
