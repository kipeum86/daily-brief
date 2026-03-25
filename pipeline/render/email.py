"""Render Jinja2 email template to an HTML string for sending.

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
from pipeline.render.dashboard import (
    _format_date_korean,
    _md_to_html,
    _normalize_market_items,
    _split_news,
)

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

    # Build web URL for the CTA button
    site_url = config.get("site_url", "").rstrip("/")
    web_url = f"{site_url}/archive/{run_date}.html" if site_url else ""

    return {
        "date_str": _format_date_korean(run_date),
        "date_iso": run_date,
        "insight_text": _md_to_html(insight),
        "markets": normalized_markets,
        "world_news": world_news,
        "korea_news": korea_news,
        "holidays": holidays or {},
        "web_url": web_url,
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
