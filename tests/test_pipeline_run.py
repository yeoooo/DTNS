from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from dtns.pipeline import PipelineStage, run_pipeline


def test_pipeline_manifest_resumes_and_invalidates_dependents(tmp_path):
    source = tmp_path / "articles.json"
    normalized = tmp_path / "normalized_articles.json"
    calls: list[str] = []

    def collect():
        calls.append("collect")
        source.write_text("articles", encoding="utf-8")

    def preprocess():
        calls.append("preprocess")
        normalized.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    stages = [
        PipelineStage("collect", collect, (), lambda: (source,)),
        PipelineStage(
            "preprocess",
            preprocess,
            (source,),
            lambda: (normalized,),
        ),
    ]

    completed = run_pipeline(tmp_path, "run-1", stages)
    assert completed.status == "completed"
    assert calls == ["collect", "preprocess"]

    calls.clear()
    resumed = run_pipeline(tmp_path, "run-1", stages)
    assert resumed.status == "completed"
    assert calls == []

    source.write_text("tampered", encoding="utf-8")
    rerun = run_pipeline(tmp_path, "run-1", stages)
    assert calls == ["collect", "preprocess"]
    assert [stage.attempts for stage in rerun.stages] == [2, 2]

    manifest_path = (
        tmp_path / ".state/pipeline/run-1/pipeline_run.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    schema = json.loads(
        (Path(__file__).parents[1] / "docs/contracts/pipeline_run.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
