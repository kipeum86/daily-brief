"""LLM provider interface and prompt builders."""

import json
import logging
import re
from abc import ABC, abstractmethod

from pipeline.models import Article

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return text completion."""

    def complete_json(self, system: str, user: str, max_retries: int = 3) -> dict:
        """Return parsed JSON, retrying on parse failure."""
        for attempt in range(max_retries):
            try:
                text = self.complete(system, user)
                return extract_json(text)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("JSON parse attempt %d failed: %s", attempt + 1, e)
                if attempt == max_retries - 1:
                    raise
        return {}


def extract_json(text: str) -> dict:
    """Extract JSON from text. Tries full parse first, then regex fallback."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in response")
    return json.loads(match.group())


def build_selection_system_prompt(
    domain_name: str, domain_description: str, top_n: int
) -> str:
    return f"""당신은 {domain_name} 분야의 전문 편집자입니다.
{domain_description}에 관한 뉴스 후보 목록을 받아, 가장 중요하고 시의성 있는 기사 {top_n}건을 선별합니다.

규칙:
- 동일 사건의 중복 기사는 제거하고 가장 상세한 1건만 선택
- 독립적이고 다양한 이벤트를 커버하도록 선별
- 실무적 관점에서 중요도가 높은 기사 우선

출력: 선별된 기사 URL만 JSON 배열로 반환하세요.
{{"selected_urls": ["url1", "url2", ...]}}"""


def build_selection_user_prompt(articles: list[Article]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        lines.append(f"[{i}] {a.title}")
        lines.append(f"    URL: {a.url}")
        lines.append(f"    Source: {a.source}")
        if a.description:
            lines.append(f"    Description: {a.description[:200]}")
        lines.append("")
    return "\n".join(lines)


def build_summarization_system_prompt(
    domain_name: str,
    categories: list[dict],
    language: str = "ko",
) -> str:
    cat_lines = "\n".join(
        f"- {c['name']}: {c.get('description', '')}" for c in categories
    )
    return f"""당신은 {domain_name} 분야 전문 브리핑 AI입니다.
기사를 분석하여 요약, 카테고리, 이벤트 정보를 추출합니다.
출력 언어: {language}. 객관적이고 전문적인 어조를 사용하세요.

카테고리 ({len(categories)}개 중 1개 선택):
{cat_lines}

출력 형식 (JSON만 반환):
{{
  "summary": ["핵심 포인트 1", "핵심 포인트 2", "핵심 포인트 3"],
  "category": "CATEGORY_NAME",
  "event": {{
    "jurisdiction": "관할권 (US, EU, KR 등)",
    "event_type": "enforcement|legislation|litigation|policy|security_incident|business|other",
    "actors": ["관련 주체"],
    "object": "대상",
    "action": "행위",
    "time_hint": "YYYY-MM-DD"
  }}
}}"""


def build_summarization_user_prompt(
    title: str,
    source: str,
    url: str,
    description: str,
    body: str,
    max_input_chars: int = 8000,
) -> str:
    truncated_body = body[:max_input_chars] if body else ""
    content = truncated_body or description
    return f"""제목: {title}
출처: {source}
URL: {url}

본문:
{content}"""
