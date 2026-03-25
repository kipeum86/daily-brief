"""Anthropic Claude LLM provider."""

import json
import logging
import os

import anthropic

from pipeline.llm.base import LLMProvider, extract_json

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class ClaudeProvider(LLMProvider):
    def __init__(self, model: str = ""):
        self.model = model or DEFAULT_MODEL
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, user: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text
