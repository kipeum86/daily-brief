"""Generate a weekly recap insight from weekly market data and recent news clusters."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pipeline.ai.briefing import _get_provider

logger = logging.getLogger("daily-brief.ai.weekly")

_WEEKLY_REQUIRED_FIELDS = ("core_theme", "market_review", "top_stories", "watch_next_week")

_WEEKLY_SYSTEM_PROMPT_KO = """\
당신은 이코노미스트(The Economist)와 파이낸셜타임스(FT) 수준의 금융 편집장입니다.
주간 시장 리뷰를 작성합니다.

작성 원칙:
- 한국어로 작성하되 반드시 존댓말을 사용하세요.
- 단순 나열이 아니라, 이번 주를 관통하는 핵심 테마를 먼저 제시하세요.
- 시장, 환율, 원자재, 변동성, 주요 뉴스를 서로 연결해 해석하세요.
- 과장하지 말고 팩트에 기반해 서술하세요.
- 불확실한 전망은 "~로 보입니다", "~가능성이 있습니다"처럼 신중하게 표현하세요.

형식:
- 요청한 JSON schema만 반환하세요.
- Markdown, HTML, 설명 문장을 추가하지 마세요.
"""

_WEEKLY_SYSTEM_PROMPT_EN = """\
You are a financial editor writing a weekly recap in the style of The Economist and the Financial Times.

Writing principles:
- Lead with the one theme that best explains the week.
- Connect markets, FX, commodities, volatility, and headlines into a coherent narrative.
- Stay factual and concise. Avoid hype.
- Use measured language for forecasts or uncertainty.

Format:
- Return only the requested JSON schema.
- Do not add Markdown, HTML, or explanatory prose.
"""


def _build_market_section(weekly_data: dict) -> str:
    lines = []
    for card in weekly_data.get("markets", {}).get("cards", []):
        lines.append(
            f"- {card['name']}: {card['start_price']:,.2f} -> {card['end_price']:,.2f} "
            f"({card['weekly_change_pct']:+.2f}%)"
        )
    leaders = weekly_data.get("markets", {}).get("leaders", [])
    laggards = weekly_data.get("markets", {}).get("laggards", [])
    if leaders:
        lines.append("")
        lines.append("Leaders:")
        lines.extend(
            f"- {item['name']} ({item['weekly_change_pct']:+.2f}%)"
            for item in leaders[:3]
        )
    if laggards:
        lines.append("")
        lines.append("Laggards:")
        lines.extend(
            f"- {item['name']} ({item['weekly_change_pct']:+.2f}%)"
            for item in laggards[:3]
        )
    return "\n".join(lines)


def _build_news_section(items: list[dict]) -> str:
    lines = []
    for item in items:
        date_label = item.get("latest_date", "")
        mentions = int(item.get("appearances", 0) or 0)
        sources = int(item.get("source_count", 0) or 0)
        lines.append(
            f"- [{item.get('source', '')}] {item.get('title', '')} "
            f"({date_label}; {mentions} mentions; {sources} sources)"
        )
    return "\n".join(lines)


def _build_weekly_prompt(weekly_data: dict, lang: str) -> str:
    range_line = f"{weekly_data.get('start_date', '')} -> {weekly_data.get('end_date', '')}"
    market_section = _build_market_section(weekly_data)
    world_news = _build_news_section(
        weekly_data.get("world_news_en" if lang == "en" else "world_news_ko", [])
    )
    korea_news = _build_news_section(
        weekly_data.get("korea_news_en" if lang == "en" else "korea_news_ko", [])
    )
    snapshot_count = weekly_data.get("snapshot_count", 0)
    news_pool_count = weekly_data.get("news_pool_count", 0)
    news_source_count = weekly_data.get("news_source_count", 0)
    unique_story_count = weekly_data.get("unique_story_count", 0)

    if lang == "en":
        return f"""Week range: {range_line}
Snapshots available: {snapshot_count}
Recent news pool: {news_pool_count} articles from {news_source_count} sources
Issue clusters: {unique_story_count}

## Market Moves
{market_section}

## World Stories
{world_news}

## Korea Stories
{korea_news}

Write a weekly market recap based on the data above.
Every claim must be grounded in the supplied market moves or story list.

Return ONLY valid JSON with this exact schema:
{{
  "core_theme": [
    "1-2 strings explaining the one theme that best explains the week"
  ],
  "market_review": [
    "2-4 strings connecting market moves, FX, commodities, volatility, and risk appetite"
  ],
  "top_stories": [
    {{
      "story": "short story label, no Markdown",
      "meaning": "one sentence explaining why it mattered this week"
    }}
  ],
  "watch_next_week": [
    "2-3 strings naming concrete data, policy, earnings, or market risks to watch"
  ]
}}

Use 2-4 top_stories. Values must be plain text only."""

    return f"""주간 범위: {range_line}
사용 가능한 일일 스냅샷: {snapshot_count}
최근 7일 기사 풀: {news_pool_count}건 / {news_source_count}개 출처
이슈 클러스터 수: {unique_story_count}

## 시장 움직임
{market_section}

## 글로벌 주요 기사
{world_news}

## 한국 주요 기사
{korea_news}

위 데이터를 바탕으로 주간 시장 리캡을 작성하세요.
각 판단은 위 시장 움직임 또는 주요 기사 목록에 근거해야 합니다.

아래 schema의 유효한 JSON만 반환하세요.
{{
  "core_theme": [
    "이번 주를 가장 잘 설명하는 핵심 테마 1~2문장"
  ],
  "market_review": [
    "시장, 환율, 원자재, 변동성, 위험선호를 연결한 문장 2~4개"
  ],
  "top_stories": [
    {{
      "story": "짧은 이슈명, Markdown 금지",
      "meaning": "이번 주 이 이슈가 중요했던 이유를 설명하는 한 문장"
    }}
  ],
  "watch_next_week": [
    "다음 주 확인할 데이터, 정책, 실적, 시장 리스크 2~3개"
  ]
}}

top_stories는 2~4개를 작성하세요. 모든 값은 일반 텍스트여야 합니다."""


def _strip_json_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return text


def _parse_weekly_json(text: str) -> dict[str, Any]:
    text = _strip_json_fences(text)
    if not text:
        raise RuntimeError("LLM returned empty weekly recap")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("weekly recap output must be a JSON object")
    return payload


def _clean_plain_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"^[-*]\s*", "", text)
    return text.strip()


def _text_list(payload: dict[str, Any], field: str, *, min_items: int = 1, max_items: int = 4) -> list[str]:
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError(f"weekly recap field '{field}' must be a list")
    items = [_clean_plain_text(item) for item in value]
    items = [item for item in items if item]
    if len(items) < min_items:
        raise ValueError(f"weekly recap field '{field}' needs at least {min_items} non-empty item(s)")
    return items[:max_items]


def validate_weekly_recap_payload(payload: Any) -> dict[str, Any]:
    """Validate and normalize the structured weekly recap payload."""
    if not isinstance(payload, dict):
        raise ValueError("weekly recap output must be a JSON object")
    missing = [field for field in _WEEKLY_REQUIRED_FIELDS if field not in payload]
    if missing:
        raise ValueError(f"weekly recap output missing required field(s): {', '.join(missing)}")

    stories = payload.get("top_stories")
    if not isinstance(stories, list):
        raise ValueError("weekly recap field 'top_stories' must be a list")

    normalized_stories: list[dict[str, str]] = []
    for item in stories:
        if not isinstance(item, dict):
            raise ValueError("each weekly top_stories item must be an object")
        story = _clean_plain_text(item.get("story"))
        meaning = _clean_plain_text(item.get("meaning"))
        if story and meaning:
            normalized_stories.append({"story": story, "meaning": meaning})

    if not normalized_stories:
        raise ValueError("weekly recap field 'top_stories' needs at least one complete item")

    return {
        "core_theme": _text_list(payload, "core_theme", min_items=1, max_items=2),
        "market_review": _text_list(payload, "market_review", min_items=1, max_items=4),
        "top_stories": normalized_stories[:4],
        "watch_next_week": _text_list(payload, "watch_next_week", min_items=1, max_items=3),
    }


def render_weekly_recap_markdown(payload: dict[str, Any]) -> str:
    """Render a validated weekly recap payload into stable Markdown sections."""
    parts = [
        "## This Week's Core Theme",
        "",
        " ".join(payload["core_theme"]),
        "",
        "## Market Review",
        "",
        " ".join(payload["market_review"]),
        "",
        "## Top Stories",
        "",
    ]
    for item in payload["top_stories"]:
        parts.append(f"- **{item['story']}**: {item['meaning']}")
    parts.extend([
        "",
        "## What to Watch Next Week",
        "",
    ])
    for item in payload["watch_next_week"]:
        parts.append(f"- {item}")
    return "\n".join(parts).strip()


def _call_weekly_recap_json(provider: Any, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    if hasattr(provider, "complete_json"):
        payload = provider.complete_json(system_prompt, user_prompt)
    else:
        payload = _parse_weekly_json(provider.complete(system_prompt, user_prompt))
    return validate_weekly_recap_payload(payload)


def generate_weekly_recap(
    config: dict,
    weekly_data: dict,
    lang: str = "ko",
) -> str:
    """Generate a weekly recap insight in Korean or English."""
    if (
        not weekly_data.get("markets", {}).get("cards")
        and not weekly_data.get("world_news_raw")
        and not weekly_data.get("korea_news_raw")
    ):
        return ""

    provider = _get_provider(config, task="weekly")
    system_prompt = _WEEKLY_SYSTEM_PROMPT_EN if lang == "en" else _WEEKLY_SYSTEM_PROMPT_KO
    user_prompt = _build_weekly_prompt(weekly_data, lang)
    logger.info("Generating weekly recap (lang=%s)", lang)
    payload = _call_weekly_recap_json(provider, system_prompt, user_prompt)
    result = render_weekly_recap_markdown(payload)
    if not result:
        raise RuntimeError("LLM returned empty weekly recap")
    return result
