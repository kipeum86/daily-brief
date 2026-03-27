"""Render Jinja2 email templates to HTML strings for sending.

Public API (called from main.py):
    render_email(config, markets, holidays, articles, insight, run_date)
        → returns rendered HTML string
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

# Reuse helpers from the dashboard renderer
import re

from pipeline.render.dashboard import (
    _format_date_korean,
    _md_to_html,
    _normalize_market_items,
    _split_news,
)


def _style_insight_for_email(html: str) -> str:
    """AI 인사이트 HTML에 이메일용 인라인 스타일을 적용한다."""
    if not html:
        return html

    # h2 → 섹션 라벨 (작고 빨간 uppercase, 웹과 동일)
    html = re.sub(
        r"<h2>(.*?)</h2>",
        r'<p style="font-family:Helvetica,Arial,sans-serif;font-size:11px;font-weight:700;'
        r'letter-spacing:1.5px;text-transform:uppercase;color:#B91C1C;margin:20px 0 6px 0;'
        r'padding:0;">\1</p>',
        html,
    )

    # h3 → 동일
    html = re.sub(
        r"<h3>(.*?)</h3>",
        r'<p style="font-family:Helvetica,Arial,sans-serif;font-size:11px;font-weight:700;'
        r'letter-spacing:1.5px;text-transform:uppercase;color:#B91C1C;margin:18px 0 6px 0;'
        r'padding:0;">\1</p>',
        html,
    )

    # p → 본문 (serif, 웹과 동일)
    html = re.sub(
        r"<p(?![^>]*style)>",
        r'<p style="font-family:Georgia,serif;font-size:15px;line-height:1.7;color:#1A1A1A;'
        r'margin:0 0 12px 0;padding:0;">',
        html,
    )

    # ul → 리스트 컨테이너
    html = re.sub(
        r"<ul>",
        r'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        r'style="margin:8px 0 16px 0;width:100%;">',
        html,
    )
    html = html.replace("</ul>", "</table>")

    # li → 테이블 행 (왼쪽 빨간 보더, 이메일 호환)
    html = re.sub(
        r"<li>(.*?)</li>",
        r'<tr><td style="font-family:Helvetica,Arial,sans-serif;font-size:14px;line-height:1.6;'
        r'color:#1A1A1A;padding:6px 0 6px 14px;border-left:2px solid #B91C1C;'
        r'margin-bottom:6px;">\1</td></tr>',
        html,
        flags=re.DOTALL,
    )

    return html

logger = logging.getLogger("daily-brief.render.email")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATES_DIR = _PROJECT_ROOT / "templates" / "email"
_MIN_HTML_LENGTH = 500  # reject obviously broken output


def _build_email_context(
    config: dict,
    markets: dict[str, list],
    holidays: dict[str, Any],
    articles: list,
    insight: str,
    run_date: str,
) -> dict[str, Any]:
    """Assemble Jinja2 template variables for the email template."""

    # Normalize market data
    normalized_markets: dict[str, list[dict]] = {}
    for key, items in markets.items():
        normalized_markets[key] = _normalize_market_items(items)

    # Split news
    world_news, korea_news = _split_news(articles, config)

    # Build web URLs for the CTA buttons
    site_url = config.get("site_url", "").rstrip("/")
    web_url = f"{site_url}/archive/{run_date}.html" if site_url else ""
    web_url_en = f"{site_url}/en/archive/{run_date}.html" if site_url else ""

    return {
        "date_str": _format_date_korean(run_date),
        "date_iso": run_date,
        "insight_text": _style_insight_for_email(_md_to_html(insight)),
        "markets": normalized_markets,
        "world_news": world_news,
        "korea_news": korea_news,
        "holidays": holidays or {},
        "web_url": web_url,
        "web_url_en": web_url_en,
        "site_url": site_url,
    }


def _truncate_text(text: str, max_len: int = 140) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _format_weekly_market_card(card: dict[str, Any]) -> dict[str, Any]:
    change_pct = float(card.get("weekly_change_pct", 0.0))
    direction = "flat"
    if change_pct > 0:
        direction = "up"
    elif change_pct < 0:
        direction = "down"
    return {
        **card,
        "direction": direction,
        "start_fmt": f"{float(card.get('start_price', 0.0)):,.2f}",
        "end_fmt": f"{float(card.get('end_price', 0.0)):,.2f}",
        "change_fmt": f"{change_pct:+.2f}%",
    }


def _format_weekly_story(article: dict[str, Any]) -> dict[str, Any]:
    appearances = int(article.get("appearances", 0) or 0)
    source_count = int(article.get("source_count", 0) or 0)
    return {
        **article,
        "summary_short": _truncate_text(article.get("summary", "") or article.get("description", ""), 150),
        "appearances_label": f"{appearances}회 언급",
        "source_count_label": f"{source_count}개 소스" if source_count else "",
    }


def _build_weekly_email_context(
    config: dict,
    weekly_data: dict[str, Any],
) -> dict[str, Any]:
    """Assemble Jinja2 variables for the weekly email template."""
    site_url = config.get("site_url", "").rstrip("/")
    week_id = weekly_data.get("week_id", "")
    web_url = f"{site_url}/weekly/archive/{week_id}.html" if site_url and week_id else ""
    web_url_en = f"{site_url}/en/weekly/archive/{week_id}.html" if site_url and week_id else ""
    archive_url = f"{site_url}/weekly/archive/" if site_url else ""

    markets = weekly_data.get("markets", {})
    world_news = weekly_data.get("world_news_ko", [])
    korea_news = weekly_data.get("korea_news_ko", [])

    return {
        "date_str": weekly_data.get("week_id", ""),
        "week_label": f"{weekly_data.get('start_date', '')} → {weekly_data.get('end_date', '')}",
        "week_id": week_id,
        "insight_text": _style_insight_for_email(_md_to_html(weekly_data.get("insight_ko", ""))),
        "snapshot_count": weekly_data.get("snapshot_count", 0),
        "news_pool_count": weekly_data.get("news_pool_count", 0),
        "news_source_count": weekly_data.get("news_source_count", 0),
        "unique_story_count": weekly_data.get("unique_story_count", 0),
        "market_cards": [_format_weekly_market_card(card) for card in markets.get("cards", [])],
        "leaders": [_format_weekly_market_card(card) for card in markets.get("leaders", [])[:3]],
        "laggards": [_format_weekly_market_card(card) for card in markets.get("laggards", [])[:3]],
        "sectors_best": [_format_weekly_market_card(card) for card in markets.get("sectors_best", [])],
        "sectors_worst": [_format_weekly_market_card(card) for card in markets.get("sectors_worst", [])],
        "world_news": [_format_weekly_story(article) for article in world_news],
        "korea_news": [_format_weekly_story(article) for article in korea_news],
        "web_url": web_url,
        "web_url_en": web_url_en,
        "archive_url": archive_url,
        "site_url": site_url,
    }


def render_email(
    config: dict,
    markets: dict[str, list],
    holidays: dict[str, Any],
    articles: list,
    insight: str,
    run_date: str,
    market_pulse: dict | None = None,
) -> str:
    """Render the email HTML template with briefing data.

    Uses the same data sources and patterns as dashboard.py but produces
    a self-contained HTML email (all CSS inlined, no JS, table-based layout).

    Args:
        config: Loaded config dict.
        markets: Dict of market category → list of MarketData or dicts.
        holidays: Holiday detection dict (kospi_holiday, nyse_holiday, etc.).
        articles: List of Article objects or dicts.
        insight: AI-generated insight HTML string.
        run_date: ISO date string (YYYY-MM-DD).

    Returns:
        Rendered HTML string suitable for email body.

    Raises:
        ValueError: If rendered HTML is suspiciously short.
    """
    logger.info("Rendering email template for %s", run_date)

    # Build context
    context = _build_email_context(
        config, markets, holidays, articles, insight, run_date,
    )
    context["market_pulse"] = market_pulse or {}

    # Load and render Jinja2 template
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("brief.html")

    # Mark insight_text as safe (may contain HTML from AI)
    if context.get("insight_text"):
        context["insight_text"] = Markup(context["insight_text"])

    html = template.render(**context)

    if len(html) < _MIN_HTML_LENGTH:
        raise ValueError(
            f"Rendered email HTML too short ({len(html)} chars < {_MIN_HTML_LENGTH}). "
            "Likely a broken template render."
        )

    logger.info("Email render complete (%d chars)", len(html))
    return html


def render_weekly_email(
    config: dict,
    weekly_data: dict[str, Any],
) -> str:
    """Render the weekly recap email template."""
    logger.info("Rendering weekly email template for %s", weekly_data.get("week_id", ""))

    context = _build_weekly_email_context(config, weekly_data)
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("weekly.html")

    if context.get("insight_text"):
        context["insight_text"] = Markup(context["insight_text"])

    html = template.render(**context)

    if len(html) < _MIN_HTML_LENGTH:
        raise ValueError(
            f"Rendered weekly email HTML too short ({len(html)} chars < {_MIN_HTML_LENGTH}). "
            "Likely a broken template render."
        )

    logger.info("Weekly email render complete (%d chars)", len(html))
    return html
