from __future__ import annotations

from types import SimpleNamespace

from pipeline.llm import gemini as gemini_module


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeModel:
    def __init__(self, name: str, system_instruction: str | None, behavior: dict[str, object]):
        self.name = name
        self.system_instruction = system_instruction
        self.behavior = behavior

    def generate_content(self, user: str, generation_config=None):
        outcome = self.behavior.get(self.name, "")
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(str(outcome))


class _FakeGenAI:
    def __init__(self, behavior: dict[str, object]):
        self.behavior = behavior
        self.configured_api_key = None

    def configure(self, api_key: str = ""):
        self.configured_api_key = api_key

    def GenerativeModel(self, name: str, system_instruction=None):
        return _FakeModel(name, system_instruction, self.behavior)

    def GenerationConfig(self, **kwargs):
        return SimpleNamespace(**kwargs)


def test_primary_model_success(monkeypatch):
    fake_genai = _FakeGenAI({"primary-model": "ok"})
    monkeypatch.setattr(gemini_module, "genai", fake_genai)
    provider = gemini_module.GeminiProvider(model="primary-model", fallback_models=["fallback-model"])
    assert provider.complete("system", "user") == "ok"


def test_primary_fails_fallback_succeeds(monkeypatch):
    fake_genai = _FakeGenAI({"primary-model": RuntimeError("boom"), "fallback-model": "fallback-ok"})
    monkeypatch.setattr(gemini_module, "genai", fake_genai)
    provider = gemini_module.GeminiProvider(model="primary-model", fallback_models=["fallback-model"])
    assert provider.complete("system", "user") == "fallback-ok"


def test_all_models_fail_returns_empty(monkeypatch):
    fake_genai = _FakeGenAI({"primary-model": RuntimeError("boom"), "fallback-model": RuntimeError("still-boom")})
    monkeypatch.setattr(gemini_module, "genai", fake_genai)
    provider = gemini_module.GeminiProvider(model="primary-model", fallback_models=["fallback-model"])
    assert provider.complete("system", "user") == ""


def test_fallback_models_from_config(monkeypatch):
    fake_genai = _FakeGenAI({"gemini-2.5-pro": "ok"})
    monkeypatch.setattr(gemini_module, "genai", fake_genai)
    monkeypatch.setattr(
        gemini_module,
        "_load_llm_defaults",
        lambda: {"model": "gemini-2.5-pro", "fallback_models": ["gemini-2.5-flash"]},
    )
    provider = gemini_module.GeminiProvider()
    assert provider.model_name == "gemini-2.5-pro"
    assert provider.fallback_models == ["gemini-2.5-flash"]
