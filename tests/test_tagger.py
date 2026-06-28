from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib.resources import files
from types import SimpleNamespace

import pytest

from dtns.agents.tagger import stage
from dtns.agents.tagger.stage import (
    BatchResponseError,
    GeminiTaggerClient,
    TAGGER_BATCH_SIZE,
    TaggerRunError,
    tag_articles,
)


class FakeTaggerClient:
    model = "fake-model"

    def __init__(self, *, fail_first_call: bool = False):
        self.batch_sizes: list[int] = []
        self.fail_first_call = fail_first_call

    def tag(self, articles):
        self.batch_sizes.append(len(articles))
        if self.fail_first_call:
            self.fail_first_call = False
            raise BatchResponseError("invalid_json", "invalid JSON")
        return {
            "articles": [
                {
                    "id": article.id,
                    "tags": ["Python"],
                    "technologies": ["Python"],
                    "domains": ["Backend"],
                    "ai_metadata": {
                        "confidence": 0.9,
                    },
                }
                for article in articles
            ]
        }


class PayloadTaggerClient:
    model = "fake-model"

    def __init__(self, payload_factory):
        self.payload_factory = payload_factory

    def tag(self, articles):
        return {"articles": [self.payload_factory(articles[0])]}


class RaisingTaggerClient:
    model = "fake-model"

    def __init__(self, error):
        self.error = error

    def tag(self, articles):
        raise self.error


class StatusError(RuntimeError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class AdaptiveSplitClient:
    model = "fake-model"

    def __init__(self):
        self.batch_sizes = []

    def tag(self, articles):
        self.batch_sizes.append(len(articles))
        if len(articles) > 2:
            return {"articles": []}
        return _valid_response(articles)


class NoCallClient:
    model = "fake-model"

    def tag(self, articles):
        raise AssertionError("completed checkpoints must be reused")


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


@pytest.mark.parametrize(
    "invalid_field,invalid_value",
    [
        ("id", 123),
        ("tags", {"Python": 1}),
        ("confidence", "0.9"),
        ("model", "hallucinated-model"),
    ],
)
def test_tag_articles_rejects_non_schema_model_output(
    tmp_path,
    invalid_field,
    invalid_value,
):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    state_path = tmp_path / "state"
    _write_normalized_articles(input_path, 1)

    def invalid_payload(article):
        payload = {
            "id": article.id,
            "tags": ["Python"],
            "technologies": ["Python"],
            "domains": ["Backend"],
            "ai_metadata": {"confidence": 0.9},
        }
        if invalid_field in {"confidence", "model"}:
            payload["ai_metadata"][invalid_field] = invalid_value
        else:
            payload[invalid_field] = invalid_value
        return payload

    with pytest.raises(TaggerRunError) as caught:
        tag_articles(
            input_path,
            output_path,
            llm_client=PayloadTaggerClient(invalid_payload),
            state_path=state_path,
        )

    assert caught.value.category == "invalid_schema"
    assert not list(state_path.glob("*.json"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("confidence", "0.9"),
        ("generated_at", "2026-06-28T12:00:00"),
    ],
)
def test_tag_articles_rejects_invalid_checkpoint(tmp_path, field, value):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    state_path = tmp_path / "state"
    _write_normalized_articles(input_path, 1)
    tag_articles(
        input_path,
        output_path,
        llm_client=FakeTaggerClient(),
        run_id="strict-checkpoint",
        state_path=state_path,
    )
    checkpoint_path = next(state_path.glob("*.json"))
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if field == "confidence":
        checkpoint["articles"][0]["ai_metadata"][field] = value
    else:
        checkpoint[field] = value
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid Tagger checkpoint"):
        tag_articles(
            input_path,
            output_path,
            llm_client=FakeTaggerClient(),
            run_id="strict-checkpoint",
            state_path=state_path,
        )


def test_checkpoint_schema_version_changes_policy_fingerprint(monkeypatch):
    before = stage._policy_fingerprint(model="primary", fallback_model="fallback")
    monkeypatch.setattr(stage, "CHECKPOINT_SCHEMA_VERSION", "2.0")

    after = stage._policy_fingerprint(model="primary", fallback_model="fallback")

    assert before != after


@pytest.mark.parametrize(
    "error,category",
    [
        (StatusError(503, "request body contains secret"), "transient_api"),
        (StatusError(401, "API key is secret"), "authentication"),
        (RuntimeError("programming details"), "client_error"),
        (ValueError("programming details"), "client_error"),
    ],
)
def test_client_errors_are_classified_without_original_exception(
    tmp_path,
    error,
    category,
):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    _write_normalized_articles(input_path, 1)

    with pytest.raises(TaggerRunError) as caught:
        tag_articles(
            input_path,
            output_path,
            llm_client=RaisingTaggerClient(error),
        )

    assert caught.value.category == category
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "secret" not in str(caught.value)
    assert "programming details" not in str(caught.value)


def test_tag_articles_splits_left_first_and_resumes_checkpoints(tmp_path):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    state_path = tmp_path / "state"
    _write_normalized_articles(input_path, 5)
    client = AdaptiveSplitClient()

    first = tag_articles(
        input_path,
        output_path,
        llm_client=client,
        run_id="adaptive-run",
        state_path=state_path,
    )

    assert client.batch_sizes == [5, 5, 2, 3, 3, 1, 2]
    assert [article.id for article in first.articles] == [
        f"article-{index}" for index in range(5)
    ]
    assert sorted(path.name for path in state_path.glob("*.json")) == [
        "articles-000000-000002.json",
        "articles-000002-000003.json",
        "articles-000003-000005.json",
    ]

    resumed = tag_articles(
        input_path,
        output_path,
        llm_client=NoCallClient(),
        run_id="adaptive-run",
        state_path=state_path,
    )

    assert [article.id for article in resumed.articles] == [
        f"article-{index}" for index in range(5)
    ]


def test_terminal_failure_does_not_replace_existing_output(tmp_path):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    _write_normalized_articles(input_path, 1)
    output_path.write_text("existing-output", encoding="utf-8")

    with pytest.raises(TaggerRunError):
        tag_articles(
            input_path,
            output_path,
            llm_client=PayloadTaggerClient(
                lambda article: {
                    **_valid_response([article])["articles"][0],
                    "id": 123,
                }
            ),
        )

    assert output_path.read_text(encoding="utf-8") == "existing-output"


def test_gemini_client_uses_fallback_first_after_fallback_success(
    tmp_path,
    monkeypatch,
):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    _write_normalized_articles(input_path, TAGGER_BATCH_SIZE + 1)
    primary_models = []

    def fake_generate(*, primary_model, contents, **kwargs):
        primary_models.append(primary_model)
        input_payload = json.loads(contents[1])
        articles = [SimpleNamespace(**article) for article in input_payload["articles"]]
        return SimpleNamespace(
            model="fallback-model",
            response=SimpleNamespace(
                text=json.dumps(_valid_response(articles)),
                candidates=[],
            ),
        )

    monkeypatch.setattr(stage, "generate_content_with_fallback", fake_generate)
    client = GeminiTaggerClient(
        model="primary-model",
        fallback_model="fallback-model",
    )

    result = tag_articles(input_path, output_path, llm_client=client)

    assert primary_models == ["primary-model", "fallback-model"]
    assert {article.ai_metadata.model for article in result.articles} == {
        "fallback-model"
    }


def test_resume_restores_fallback_preference_from_checkpoint(
    tmp_path,
    monkeypatch,
):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    state_path = tmp_path / "state"
    _write_normalized_articles(input_path, TAGGER_BATCH_SIZE + 1)
    first_run_calls = []

    def fail_after_first_batch(*, primary_model, contents, **kwargs):
        first_run_calls.append(primary_model)
        if len(first_run_calls) > 1:
            raise StatusError(401, "authentication failed")
        input_payload = json.loads(contents[1])
        articles = [SimpleNamespace(**article) for article in input_payload["articles"]]
        return SimpleNamespace(
            model="fallback-model",
            response=SimpleNamespace(
                text=json.dumps(_valid_response(articles)),
                candidates=[],
            ),
        )

    monkeypatch.setattr(stage, "generate_content_with_fallback", fail_after_first_batch)
    with pytest.raises(TaggerRunError):
        tag_articles(
            input_path,
            output_path,
            llm_client=GeminiTaggerClient(
                model="primary-model",
                fallback_model="fallback-model",
            ),
            run_id="resume-fallback",
            state_path=state_path,
        )

    assert first_run_calls == ["primary-model", "fallback-model"]
    assert [path.name for path in state_path.glob("*.json")] == [
        "articles-000000-000008.json"
    ]

    resumed_calls = []

    def complete_resumed_batch(*, primary_model, contents, **kwargs):
        resumed_calls.append(primary_model)
        input_payload = json.loads(contents[1])
        articles = [SimpleNamespace(**article) for article in input_payload["articles"]]
        return SimpleNamespace(
            model="fallback-model",
            response=SimpleNamespace(
                text=json.dumps(_valid_response(articles)),
                candidates=[],
            ),
        )

    monkeypatch.setattr(stage, "generate_content_with_fallback", complete_resumed_batch)
    tag_articles(
        input_path,
        output_path,
        llm_client=GeminiTaggerClient(
            model="primary-model",
            fallback_model="fallback-model",
        ),
        run_id="resume-fallback",
        state_path=state_path,
    )

    assert resumed_calls == ["fallback-model"]


def test_error_reports_only_models_actually_attempted(tmp_path, monkeypatch):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    _write_normalized_articles(input_path, 1)

    def reject_authentication(**kwargs):
        raise StatusError(401, "API key is secret")

    monkeypatch.setattr(stage, "generate_content_with_fallback", reject_authentication)
    client = GeminiTaggerClient(
        model="primary-model",
        fallback_model="fallback-model",
    )

    with pytest.raises(TaggerRunError) as caught:
        tag_articles(input_path, output_path, llm_client=client)

    assert caught.value.category == "authentication"
    assert caught.value.models == ("primary-model",)


def test_transient_exhaustion_reports_primary_and_fallback(tmp_path, monkeypatch):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    _write_normalized_articles(input_path, 1)
    wrapper = RuntimeError("Gemini models exhausted")
    wrapper.__cause__ = StatusError(503, "request details")

    def exhaust_models(**kwargs):
        raise wrapper

    monkeypatch.setattr(stage, "generate_content_with_fallback", exhaust_models)
    client = GeminiTaggerClient(
        model="primary-model",
        fallback_model="fallback-model",
    )

    with pytest.raises(TaggerRunError) as caught:
        tag_articles(input_path, output_path, llm_client=client)

    assert caught.value.category == "transient_api"
    assert caught.value.models == ("primary-model", "fallback-model")


def test_invalid_input_is_rejected_before_llm_or_checkpoint_side_effects(tmp_path):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    state_path = tmp_path / "state"
    _write_normalized_articles(input_path, 1)
    document = json.loads(input_path.read_text(encoding="utf-8"))
    document["articles"][0]["canonical_url"] = "not-a-uri"
    input_path.write_text(json.dumps(document), encoding="utf-8")
    output_path.write_text("existing-output", encoding="utf-8")

    with pytest.raises(ValueError, match="normalized_articles JSON Schema"):
        tag_articles(
            input_path,
            output_path,
            llm_client=NoCallClient(),
            state_path=state_path,
        )

    assert output_path.read_text(encoding="utf-8") == "existing-output"
    assert not state_path.exists()


@pytest.mark.parametrize(
    "schema_filename",
    ["normalized_articles.schema.json", "tagged_articles.schema.json"],
)
def test_packaged_schema_matches_documented_contract(schema_filename):
    packaged_schema = json.loads(
        files("dtns.contracts")
        .joinpath(schema_filename)
        .read_text(encoding="utf-8")
    )
    documented_schema = json.loads(
        (stage.PROMPT_PATH.parents[3] / "docs/contracts" / schema_filename)
        .read_text(encoding="utf-8")
    )

    assert packaged_schema == documented_schema


def test_wrapped_transient_api_error_is_classified_from_cause(tmp_path):
    input_path = tmp_path / "normalized_articles.json"
    output_path = tmp_path / "tagged_articles.json"
    _write_normalized_articles(input_path, 1)
    wrapper = RuntimeError("Gemini models exhausted")
    wrapper.__cause__ = StatusError(503, "request details")

    with pytest.raises(TaggerRunError) as caught:
        tag_articles(
            input_path,
            output_path,
            llm_client=RaisingTaggerClient(wrapper),
        )

    assert caught.value.category == "transient_api"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_directory_fsync_failure_after_replace_restores_existing_output(
    tmp_path,
    monkeypatch,
):
    output_path = tmp_path / "output.json"
    output_path.write_text("old", encoding="utf-8")
    real_fsync_directory = stage._fsync_directory
    fsync_calls = 0

    def fail_post_replace_fsync(path):
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("directory fsync failed")
        return real_fsync_directory(path)

    monkeypatch.setattr(stage, "_fsync_directory", fail_post_replace_fsync)

    with pytest.raises(OSError, match="directory fsync failed"):
        stage._atomic_write_json(output_path, {"status": "committed"})

    assert output_path.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob("*.rollback"))


def _valid_response(articles):
    return {
        "articles": [
            {
                "id": article.id,
                "tags": ["Python"],
                "technologies": ["Python"],
                "domains": ["Backend"],
                "ai_metadata": {"confidence": 0.9},
            }
            for article in articles
        ]
    }


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
