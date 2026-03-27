"""Google Gemini LLM provider."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

try:
    import google.generativeai as genai
except ImportError:  # pragma: no cover - exercised in environments without deps
    genai = None

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a runtime dependency
    yaml = None

from pipeline.llm.base import LLMProvider

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"


def _load_llm_defaults() -> dict[str, Any]:
    """Load llm defaults from config.yaml when callers use the legacy interface."""
    if yaml is None or not _CONFIG_PATH.exists():
        return {}
    try:
        payload = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("Could not read Gemini defaults from %s: %s", _CONFIG_PATH, exc)
        return {}
    llm_config = payload.get("llm", {})
    return llm_config if isinstance(llm_config, dict) else {}


class GeminiProvider(LLMProvider):
    def __init__(self, model: str = "", fallback_models: list[str] | None = None):
        llm_defaults = _load_llm_defaults()
        self.model_name = model or llm_defaults.get("model", "gemini-2.5-pro")
        configured_fallbacks = llm_defaults.get("fallback_models", [])
        raw_fallbacks = fallback_models if fallback_models is not None else configured_fallbacks
        self.fallback_models = [
            candidate
            for candidate in raw_fallbacks
            if candidate and candidate != self.model_name
        ]
        self._system_cache = ""
        self._model_cache: dict[tuple[str, str], Any] = {}

        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if genai is None:
            raise ImportError("google-generativeai is not installed")
        genai.configure(api_key=api_key)

    def _candidate_models(self) -> list[str]:
        ordered = [self.model_name, *self.fallback_models]
        deduped: list[str] = []
        for candidate in ordered:
            if candidate and candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def _get_model(self, model_name: str, system: str):
        cache_key = (model_name, system)
        model = self._model_cache.get(cache_key)
        if model is None:
            model = genai.GenerativeModel(model_name, system_instruction=system or None)
            self._model_cache[cache_key] = model
        self._system_cache = system
        return model

    def complete(self, system: str, user: str) -> str:
        last_error: Exception | None = None
        for model_name in self._candidate_models():
            try:
                model = self._get_model(model_name, system)
                response = model.generate_content(
                    user,
                    generation_config=genai.GenerationConfig(temperature=0),
                )
                text = response.text or ""
                logger.info("Gemini completion used model: %s", model_name)
                return text
            except Exception as exc:
                last_error = exc
                logger.warning("Gemini completion failed with %s: %s", model_name, exc)

        if last_error is not None:
            logger.error("Gemini completion exhausted all models: %s", last_error)
        return ""

    def complete_json(self, system: str, user: str, max_retries: int = 3) -> dict:
        """Use Gemini's JSON response mime type with fallback models."""
        last_error: Exception | None = None
        for model_name in self._candidate_models():
            model = self._get_model(model_name, system)
            for attempt in range(max_retries):
                try:
                    response = model.generate_content(
                        user,
                        generation_config=genai.GenerationConfig(
                            response_mime_type="application/json",
                            temperature=0,
                        ),
                    )
                    text = response.text or "{}"
                    payload = json.loads(text)
                    logger.info("Gemini JSON completion used model: %s", model_name)
                    return payload
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "Gemini JSON attempt %d failed with %s: %s",
                        attempt + 1,
                        model_name,
                        exc,
                    )
                    if attempt == max_retries - 1:
                        break

        if last_error is not None:
            logger.error("Gemini JSON completion exhausted all models: %s", last_error)
        return {}
