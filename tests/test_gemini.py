from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from google.genai.errors import ServerError
from jsonschema import Draft202012Validator, FormatChecker

from dtns.agents import gemini


class FakeModels:
    def __init__(self):
        self.calls: list[str] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        if model == "primary-model":
            raise ServerError(
                503,
                {
                    "error": {
                        "code": 503,
                        "message": "high demand",
                        "status": "UNAVAILABLE",
                    }
                },
            )
        return SimpleNamespace(text="OK")


def test_generate_content_falls_back_after_primary_503(monkeypatch):
    models = FakeModels()
    client = SimpleNamespace(models=models)
    monkeypatch.setattr(gemini.time, "sleep", lambda _: None)

    result = gemini.generate_content_with_fallback(
        primary_model="primary-model",
        fallback_model="fallback-model",
        contents="test",
        config={},
        client=client,
    )

    assert models.calls == ["primary-model", "primary-model", "fallback-model"]
    assert result.model == "fallback-model"
    assert result.response.text == "OK"


def test_fallback_does_not_open_circuit_until_output_is_accepted(
    monkeypatch, tmp_path
):
    models = FakeModels()
    client = SimpleNamespace(models=models)
    state_path = tmp_path / "execution_state.json"
    monkeypatch.setattr(gemini.time, "sleep", lambda _: None)

    result = gemini.generate_content_with_fallback(
        primary_model="primary-model",
        fallback_model="fallback-model",
        contents="test",
        config={},
        client=client,
        run_id="run-1",
        execution_state_path=state_path,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert result.model == "fallback-model"
    assert state["circuit_state"] == "closed"
    assert state["fallback_successes"] == 0

    result.accept()

    accepted = json.loads(state_path.read_text(encoding="utf-8"))
    assert accepted["circuit_state"] == "open"
    assert accepted["preferred_model"] == "fallback-model"
    assert accepted["fallback_successes"] == 1
    schema = json.loads(
        (Path(__file__).parents[1] / "docs/contracts/ai_execution_state.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(accepted)
