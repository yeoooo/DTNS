from __future__ import annotations

from pathlib import Path

from dtns import cli


def test_run_all_executes_complete_pipeline_in_order(monkeypatch, tmp_path):
    calls: list[tuple[str, Path, str | None]] = []
    run_ids: list[str] = []

    def execute_pipeline(data_dir, run_id, stages):
        assert data_dir == tmp_path
        run_ids.append(run_id)
        for stage in stages:
            stage.action()

    monkeypatch.setattr(cli, "run_pipeline", execute_pipeline)

    monkeypatch.setattr(
        cli,
        "_run_collect",
        lambda data_dir, **kwargs: calls.append(
            ("collect", data_dir, str(kwargs["limit_per_source"]))
        ),
    )
    monkeypatch.setattr(
        cli,
        "_run_preprocess",
        lambda data_dir: calls.append(("preprocess", data_dir, None)),
    )
    monkeypatch.setattr(
        cli,
        "_run_tag",
        lambda data_dir, **kwargs: calls.append(("tag", data_dir, None)),
    )
    monkeypatch.setattr(
        cli,
        "_run_classify",
        lambda data_dir: calls.append(("classify", data_dir, None)),
    )
    monkeypatch.setattr(
        cli,
        "_run_trend",
        lambda data_dir, topic, **kwargs: calls.append(("trend", data_dir, topic)),
    )
    monkeypatch.setattr(
        cli,
        "_run_edit",
        lambda data_dir, topic, **kwargs: calls.append(("edit", data_dir, topic)),
    )
    monkeypatch.setattr(
        cli,
        "_run_publish",
        lambda data_dir, topic, **kwargs: calls.append(("publish", data_dir, topic)),
    )

    exit_code = cli.main(["--data-dir", str(tmp_path), "run-all"])

    assert exit_code == 0
    assert len(run_ids) == 1
    assert calls == [
        ("collect", tmp_path, "10"),
        ("preprocess", tmp_path, None),
        ("tag", tmp_path, None),
        ("classify", tmp_path, None),
        ("trend", tmp_path, "technology"),
        ("edit", tmp_path, "technology"),
        ("publish", tmp_path, "technology"),
        ("trend", tmp_path, "backend"),
        ("edit", tmp_path, "backend"),
        ("publish", tmp_path, "backend"),
        ("trend", tmp_path, "qa"),
        ("edit", tmp_path, "qa"),
        ("publish", tmp_path, "qa"),
    ]


def test_run_all_passes_explicit_run_id_to_every_ai_stage(monkeypatch, tmp_path):
    observed: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda data_dir, run_id, stages: [stage.action() for stage in stages],
    )
    monkeypatch.setattr(cli, "_run_collect", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_preprocess", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_classify", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli,
        "_run_tag",
        lambda *args, **kwargs: observed.append(("tag", kwargs.get("run_id"))),
    )
    monkeypatch.setattr(
        cli,
        "_run_trend",
        lambda *args, **kwargs: observed.append(("trend", kwargs.get("run_id"))),
    )
    monkeypatch.setattr(
        cli,
        "_run_edit",
        lambda *args, **kwargs: observed.append(("edit", kwargs.get("run_id"))),
    )
    monkeypatch.setattr(
        cli,
        "_run_publish",
        lambda *args, **kwargs: observed.append(("publish", kwargs.get("run_id"))),
    )

    assert cli.main(
        ["--data-dir", str(tmp_path), "run-all", "--run-id", "shared-run"]
    ) == 0
    assert observed
    assert {run_id for _, run_id in observed} == {"shared-run"}
