from __future__ import annotations

import json
from datetime import UTC, datetime

from dtns.agents.editor.runner import write_newsletter


def test_editor_writes_deterministic_empty_newsletter_without_client(tmp_path):
    input_path = tmp_path / "technology_trends.json"
    output_path = tmp_path / "technology_newsletter.md"
    now = datetime(2026, 6, 25, 0, 0, tzinfo=UTC).isoformat()
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": now,
                "topic": "technology",
                "trends": [],
            }
        ),
        encoding="utf-8",
    )

    markdown = write_newsletter(input_path, output_path)

    assert markdown.startswith("# 🗞️ 이번 주 Technology Trends 뉴스레터")
    assert "분류된 주요 기사가 없습니다" in markdown
    assert output_path.read_text(encoding="utf-8").strip() == markdown
