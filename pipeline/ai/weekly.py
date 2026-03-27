"""Generate a weekly recap insight from saved daily snapshots."""

from __future__ import annotations

import logging

from pipeline.ai.briefing import _get_provider

logger = logging.getLogger("daily-brief.ai.weekly")

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
- Markdown으로 작성하세요.
- 제목은 정확히 아래 네 개를 사용하세요.
  `## This Week's Core Theme`
  `## Market Review`
  `## Top Stories`
  `## What to Watch Next Week`
- 전체 분량은 350~650자 내외로 유지하세요.
"""

_WEEKLY_SYSTEM_PROMPT_EN = """\
You are a financial editor writing a weekly recap in the style of The Economist and the Financial Times.

Writing principles:
- Lead with the one theme that best explains the week.
- Connect markets, FX, commodities, volatility, and headlines into a coherent narrative.
- Stay factual and concise. Avoid hype.
- Use measured language for forecasts or uncertainty.

Format:
- Write in Markdown.
- Use exactly these headings:
  `## This Week's Core Theme`
  `## Market Review`
  `## Top Stories`
  `## What to Watch Next Week`
- Keep the recap around 220-380 words.
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
        lines.append(f"- [{item.get('source', '')}] {item.get('title', '')} ({date_label})")
    return "\n".join(lines)


def _build_weekly_prompt(weekly_data: dict, lang: str) -> str:
    range_line = f"{weekly_data.get('start_date', '')} -> {weekly_data.get('end_date', '')}"
    market_section = _build_market_section(weekly_data)
    world_news = _build_news_section(weekly_data.get("world_news_raw", []))
    korea_news = _build_news_section(weekly_data.get("korea_news_raw", []))
    snapshot_count = weekly_data.get("snapshot_count", 0)
    unique_story_count = weekly_data.get("unique_story_count", 0)

    if lang == "en":
        return f"""Week range: {range_line}
Snapshots available: {snapshot_count}
Unique story candidates: {unique_story_count}

## Market Moves
{market_section}

## World Stories
{world_news}

## Korea Stories
{korea_news}

Write a weekly market recap based on the data above."""

    return f"""주간 범위: {range_line}
사용 가능한 일일 스냅샷: {snapshot_count}
고유 기사 후보 수: {unique_story_count}

## 시장 움직임
{market_section}

## 글로벌 주요 기사
{world_news}

## 한국 주요 기사
{korea_news}

위 데이터를 바탕으로 주간 시장 리캡을 작성하세요."""


def generate_weekly_recap(
    config: dict,
    weekly_data: dict,
    lang: str = "ko",
) -> str:
    """Generate a weekly recap insight in Korean or English."""
    if not weekly_data.get("snapshot_count"):
        return ""

    try:
        provider = _get_provider(config)
        system_prompt = _WEEKLY_SYSTEM_PROMPT_EN if lang == "en" else _WEEKLY_SYSTEM_PROMPT_KO
        user_prompt = _build_weekly_prompt(weekly_data, lang)
        logger.info("Generating weekly recap (lang=%s)", lang)
        result = provider.complete(system_prompt, user_prompt)
        return (result or "").strip()
    except Exception:
        logger.exception("Failed to generate weekly recap")
        return ""
