from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from dtns import cli
from dtns.publisher.receipt import (
    PublishChunkReceipt,
    PublishReceipt,
    write_publish_receipt,
)
from dtns.publisher.stage import (
    build_delivery_content,
    publish_newsletter,
    split_discord_messages,
)


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


def test_validate_trends_accepts_strict_json_dates(tmp_path):
    path = tmp_path / "technology_trends.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-06-29T00:00:00Z",
                "topic": "technology",
                "period": {"start": "2026-06-23", "end": "2026-06-29"},
                "trends": [],
            }
        ),
        encoding="utf-8",
    )

    cli._validate_trends([path], "technology")


def test_pipeline_validator_sanitizes_pydantic_input_values(tmp_path):
    path = tmp_path / "technology_trends.json"
    secret_webhook = "https://discord.com/api/webhooks/123/secret-token"
    path.write_text(
        json.dumps(
            {
                "schema_version": "invalid",
                "generated_at": "not-a-datetime",
                "topic": "technology",
                "trends": [],
                "webhook_url": secret_webhook,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as captured:
        cli._validate_trends([path], "technology")

    message = str(captured.value)
    assert str(path) in message
    assert "TrendsFile" in message
    assert "generated_at" in message
    assert secret_webhook not in message
    assert "secret-token" not in message
    assert captured.value.__cause__ is None


def test_ai_configuration_uses_complete_agent_policy_fingerprint(
    monkeypatch, tmp_path
):
    from dtns.agents.tagger import stage

    prompt = tmp_path / "tagger.md"
    prompt.write_text("first policy", encoding="utf-8")
    monkeypatch.setattr(stage, "PROMPT_PATH", prompt)
    first = cli._ai_configuration("TAGGER")

    prompt.write_text("changed policy", encoding="utf-8")
    second = cli._ai_configuration("TAGGER")

    assert set(first) == {"topic", "policy_fingerprint"}
    assert first["policy_fingerprint"] != second["policy_fingerprint"]


def test_deterministic_stage_configurations_track_rules(monkeypatch):
    from dtns.classifier import stage as classifier
    from dtns.collectors import runner as collector
    from dtns.collectors.sources import FeedSource
    from dtns.preprocessors import stage as preprocessor

    collector_before = cli._collector_configuration(10)
    original_sources = collector.default_feed_sources()
    monkeypatch.setattr(
        collector,
        "default_feed_sources",
        lambda: (*original_sources, FeedSource("New", "https://example.com/feed")),
    )
    assert cli._collector_configuration(10) != collector_before

    preprocessor_before = cli._preprocessor_configuration()
    monkeypatch.setattr(
        preprocessor,
        "TRACKING_QUERY_KEYS",
        {*preprocessor.TRACKING_QUERY_KEYS, "new_tracking_key"},
    )
    assert cli._preprocessor_configuration() != preprocessor_before

    classifier_before = cli._classifier_configuration()
    monkeypatch.setitem(
        classifier.TERM_RULES,
        "technology",
        {*classifier.TERM_RULES["technology"], "new technology term"},
    )
    assert cli._classifier_configuration() != classifier_before


@pytest.mark.parametrize(
    "render",
    [
        lambda markdown: f"---\nlayout: newsletter\n---\n{markdown}",
        lambda markdown: f"```markdown\n{markdown}\n```",
        lambda markdown: '{"newsletter": true}',
        lambda markdown: markdown.replace("\n\n", "\n\n---\n\n", 1),
        lambda markdown: markdown.replace(
            "## 📌 주요 트렌드", "#### 📌 주요 트렌드"
        ),
    ],
)
def test_newsletter_revalidation_rejects_forbidden_or_unnormalized_markdown(
    tmp_path, render
):
    from dtns.agents.editor.runner import _empty_newsletter

    articles_path = tmp_path / "technology_articles.json"
    newsletter_path = tmp_path / "technology_newsletter.md"
    articles_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-06-29T00:00:00Z",
                "topic": "technology",
                "articles": [],
            }
        ),
        encoding="utf-8",
    )
    newsletter_path.write_text(
        render(_empty_newsletter("technology")),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        cli._validate_newsletter([newsletter_path], articles_path)


def test_publish_receipt_requires_matching_identity_and_chunks(monkeypatch, tmp_path):
    webhook = "https://discord.com/api/webhooks/1/token"
    monkeypatch.setenv("DISCORD_WEBHOOK_TECHNOLOGY", webhook)
    newsletter = tmp_path / "technology_newsletter.md"
    newsletter.write_text("# newsletter", encoding="utf-8")
    delivery_content = build_delivery_content("# newsletter")
    message = split_discord_messages(delivery_content)[0]
    newsletter_fingerprint = hashlib.sha256(
        delivery_content.encode("utf-8")
    ).hexdigest()
    webhook_fingerprint = cli._publisher_configuration("technology")[
        "webhook_fingerprint"
    ]
    receipt_path = tmp_path / "receipt.json"

    receipt = PublishReceipt(
        run_id="run-1",
        topic="backend",
        newsletter_fingerprint=newsletter_fingerprint,
        webhook_fingerprint=webhook_fingerprint,
        status="completed",
        chunks=[
            PublishChunkReceipt(
                index=0,
                fingerprint=hashlib.sha256(message.encode()).hexdigest(),
                character_count=len(message),
                status="delivered",
                delivered_at=datetime.now(UTC),
            )
        ],
        updated_at=datetime.now(UTC),
    )
    write_publish_receipt(receipt_path, receipt)
    assert not cli._publish_receipt_matches(
        receipt_path, "technology", newsletter
    )

    receipt.topic = "technology"
    receipt.chunks[0].fingerprint = "0" * 64
    write_publish_receipt(receipt_path, receipt)
    assert not cli._publish_receipt_matches(
        receipt_path, "technology", newsletter
    )

    receipt.chunks[0].fingerprint = hashlib.sha256(message.encode()).hexdigest()
    write_publish_receipt(receipt_path, receipt)
    assert cli._publish_receipt_matches(receipt_path, "technology", newsletter)


def test_pipeline_resolves_receipt_from_exact_labeled_delivery(monkeypatch, tmp_path):
    webhook = "https://discord.com/api/webhooks/1/token"
    monkeypatch.setenv("DISCORD_WEBHOOK_TECHNOLOGY", webhook)
    monkeypatch.setenv("DTNS_PUBLISH_LABEL", "🧪 테스트 발행")
    newsletter = tmp_path / "technology_newsletter.md"
    newsletter.write_text("# newsletter", encoding="utf-8")

    def handler(request):
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publish_newsletter(
            newsletter,
            topic="technology",
            webhook_url=webhook,
            client=client,
            receipt_root=tmp_path,
            run_id="run-1",
        )

    receipt_path = cli._completed_publish_receipt(
        tmp_path,
        "technology",
        newsletter,
    )
    assert receipt_path.is_file()
    cli._validate_publish_receipt([receipt_path], "technology", newsletter)
