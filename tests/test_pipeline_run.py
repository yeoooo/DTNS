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
            dependencies=("collect",),
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


def test_pipeline_invalidates_only_topic_dependents(tmp_path):
    classified = tmp_path / "classified.json"
    technology_trends = tmp_path / "technology_trends.json"
    technology_newsletter = tmp_path / "technology_newsletter.md"
    backend_trends = tmp_path / "backend_trends.json"
    calls: list[str] = []

    def action(name, path, content):
        def run():
            calls.append(name)
            path.write_text(content, encoding="utf-8")

        return run

    stages = [
        PipelineStage(
            "classify",
            action("classify", classified, "classified"),
            (),
            lambda: (classified,),
        ),
        PipelineStage(
            "trend:technology",
            action("trend:technology", technology_trends, "technology"),
            (classified,),
            lambda: (technology_trends,),
            dependencies=("classify",),
        ),
        PipelineStage(
            "edit:technology",
            action("edit:technology", technology_newsletter, "newsletter"),
            (technology_trends,),
            lambda: (technology_newsletter,),
            dependencies=("trend:technology",),
        ),
        PipelineStage(
            "trend:backend",
            action("trend:backend", backend_trends, "backend"),
            (classified,),
            lambda: (backend_trends,),
            dependencies=("classify",),
        ),
    ]
    run_pipeline(tmp_path, "topic-run", stages)
    calls.clear()
    technology_trends.write_text("invalid", encoding="utf-8")

    resumed = run_pipeline(tmp_path, "topic-run", stages)

    assert calls == ["trend:technology", "edit:technology"]
    attempts = {stage.stage_id: stage.attempts for stage in resumed.stages}
    assert attempts["trend:technology"] == 2
    assert attempts["edit:technology"] == 2
    assert attempts["trend:backend"] == 1
