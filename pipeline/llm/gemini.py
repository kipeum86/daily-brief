"""Google Gemini LLM provider."""

import json
import logging
import os

import google.generativeai as genai

from pipeline.llm.base import LLMProvider, extract_json

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiProvider(LLMProvider):
    def __init__(self, model: str = ""):
        self.model_name = model or DEFAULT_MODEL
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(self.model_name, system_instruction=None)
        self._system_cache = ""

    def complete(self, system: str, user: str) -> str:
        if system != self._system_cache:
            self.model = genai.GenerativeModel(self.model_name, system_instruction=system)
            self._system_cache = system
        response = self.model.generate_content(
            user,
            generation_config=genai.GenerationConfig(temperature=0),
        )
        return response.text or ""

    def complete_json(self, system: str, user: str, max_retries: int = 3) -> dict:
        """Use Gemini's JSON response mime type with system_instruction."""
        if system != self._system_cache:
            self.model = genai.GenerativeModel(self.model_name, system_instruction=system)
            self._system_cache = system
        for attempt in range(max_retries):
            try:
                response = self.model.generate_content(
                    user,
                    generation_config=genai.GenerationConfig(
                        response_mime_type="application/json",
                        temperature=0,
                    ),
                )
                text = response.text or "{}"
                return json.loads(text)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("Gemini JSON attempt %d failed: %s", attempt + 1, e)
                if attempt == max_retries - 1:
                    raise
        return {}
