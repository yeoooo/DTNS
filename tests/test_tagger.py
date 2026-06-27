from __future__ import annotations

import json
from datetime import UTC, datetime

from dtns.agents.tagger.stage import TAGGER_BATCH_SIZE, tag_articles


class FakeTaggerClient:
    model = "fake-model"

    def __init__(self, *, fail_first_call: bool = False):
        self.batch_sizes: list[int] = []
        self.fail_first_call = fail_first_call

    def tag(self, articles):
        self.batch_sizes.append(len(articles))
        if self.fail_first_call:
            self.fail_first_call = False
            raise ValueError("invalid JSON")
        return {
            "articles": [
                {
                    "id": article.id,
                    "tags": ["Python"],
                    "technologies": ["Python"],
                    "domains": ["Backend"],
                    "ai_metadata": {
                        "model": "hallucinated-model",
                        "confidence": 0.9,
                    },
                }
                for article in articles
            ]
        }


def test_tag_articles_batches_large_inputs_and_preserves_order(tmp_path):
    article_count = TAGGER_BATCH_SIZE + 3
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    _write_normalized_articles(input_path, article_count)
    client = FakeTaggerClient()

    result = tag_articles(input_path, output_path, llm_client=client)

    assert client.batch_sizes == [TAGGER_BATCH_SIZE, 3]
    assert [article.id for article in result.articles] == [
        f"article-{index}" for index in range(article_count)
    ]
    assert {article.ai_metadata.model for article in result.articles} == {"fake-model"}


def test_tag_articles_retries_invalid_batch_response_once(tmp_path):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    _write_normalized_articles(input_path, 1)
    client = FakeTaggerClient(fail_first_call=True)

    result = tag_articles(input_path, output_path, llm_client=client)

    assert client.batch_sizes == [1, 1]
    assert len(result.articles) == 1


def _write_normalized_articles(path, article_count):
    now = datetime(2026, 6, 27, tzinfo=UTC).isoformat()
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": now,
                "articles": [
                    {
                        "id": f"article-{index}",
                        "source": "Example",
                        "title": f"Article {index}",
                        "canonical_url": f"https://example.com/{index}",
                        "published_at": now,
                        "collected_at": now,
                    }
                    for index in range(article_count)
                ],
            }
        ),
        encoding="utf-8",
    )
