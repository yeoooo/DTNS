from __future__ import annotations

import json
from datetime import UTC, datetime

from dtns.agents.trend.runner import MAP_BATCH_SIZE, discover_trends
from dtns.agents.trend.runner import (
    _gemini_candidate_schema,
    _validate_candidates,
    TrendResponseError,
)
import pytest


def test_gemini_schema_keeps_structure_without_expensive_bounds():
    schema = _gemini_candidate_schema(4)
    candidate = schema["properties"]["candidates"]
    assert "maxItems" not in candidate
    assert "article_roles" in candidate["items"]["required"]
    assert candidate["items"]["properties"]["article_roles"]["items"][
        "properties"
    ]["role"]["enum"]


def test_local_validation_still_enforces_candidate_limit():
    with pytest.raises(TrendResponseError):
        _validate_candidates(
            {"candidates": [_candidate("a", ["article"])] * 5},
            allowed_article_ids={"article"},
            candidate_limit=4,
        )


class RepeatedCandidateIdClient:
    model = "fake-model"

    def __init__(self):
        self.phases: list[str] = []

    def discover(self, *, topic, phase, sources, candidate_limit):
        self.phases.append(phase)
        if phase == "map":
            return {
                "candidates": [
                    _candidate(f"trend-{index}", [sources[index]["id"]])
                    for index in range(candidate_limit)
                ]
            }

        article_ids = list(
            dict.fromkeys(
                article_id
                for source in sources
                for article_id in source["article_ids"]
            )
        )
        return {"candidates": [_candidate("merged-trend", article_ids)]}


class NoCallClient:
    model = "fake-model"

    def discover(self, *, topic, phase, sources, candidate_limit):
        raise AssertionError("valid Trend checkpoints must be reused")


def test_trend_scopes_repeated_candidate_ids_across_map_batches(tmp_path):
    input_path = tmp_path / "topic_articles.json"
    output_path = tmp_path / "topic_trends.json"
    state_path = tmp_path / "state"
    _write_topic_articles(input_path, MAP_BATCH_SIZE * 5)
    client = RepeatedCandidateIdClient()

    result = discover_trends(
        "game_client",
        input_path,
        output_path,
        llm_client=client,
        run_id="repeated-candidate-ids",
        state_path=state_path,
    )

    assert client.phases == ["map"] * 5 + ["reduce"] * 3
    assert len(result.trends) == 1
    assert result.trends[0].id == "merged-trend"

    resumed = discover_trends(
        "game_client",
        input_path,
        output_path,
        llm_client=NoCallClient(),
        run_id="repeated-candidate-ids",
        state_path=state_path,
    )

    assert resumed.trends[0].id == result.trends[0].id


def _candidate(candidate_id: str, article_ids: list[str]) -> dict[str, object]:
    return {
        "id": candidate_id,
        "title": candidate_id,
        "importance": "high",
        "summary": "summary",
        "why_it_matters": "why it matters",
        "article_ids": article_ids,
        "keywords": [],
    }


def _write_topic_articles(path, article_count: int) -> None:
    generated_at = datetime(2026, 8, 24, tzinfo=UTC)
    payload = {
        "schema_version": "1.0",
        "generated_at": generated_at.isoformat(),
        "topic": "game_client",
        "articles": [
            {
                "id": f"article-{index}",
                "source": "test",
                "title": f"Article {index}",
                "canonical_url": f"https://example.com/{index}",
                "published_at": generated_at.isoformat(),
                "tags": [],
                "technologies": [],
                "domains": [],
                "ai_metadata": {"model": "fake", "confidence": 1.0},
                "classification": {"matched_rules": [], "score": 1.0},
            }
            for index in range(article_count)
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
