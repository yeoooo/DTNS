from __future__ import annotations

from pathlib import Path

from dtns import cli


def test_run_all_executes_complete_pipeline_in_order(monkeypatch, tmp_path):
    calls: list[tuple[str, Path, str | None]] = []

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
        lambda data_dir: calls.append(("tag", data_dir, None)),
    )
    monkeypatch.setattr(
        cli,
        "_run_classify",
        lambda data_dir: calls.append(("classify", data_dir, None)),
    )
    monkeypatch.setattr(
        cli,
        "_run_trend",
        lambda data_dir, topic: calls.append(("trend", data_dir, topic)),
    )
    monkeypatch.setattr(
        cli,
        "_run_edit",
        lambda data_dir, topic: calls.append(("edit", data_dir, topic)),
    )
    monkeypatch.setattr(
        cli,
        "_run_publish",
        lambda data_dir, topic: calls.append(("publish", data_dir, topic)),
    )

    exit_code = cli.main(["--data-dir", str(tmp_path), "run-all"])

    assert exit_code == 0
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
