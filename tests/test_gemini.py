from __future__ import annotations

from types import SimpleNamespace

from google.genai.errors import ServerError

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
