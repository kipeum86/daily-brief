"""Weekly recap runner built from saved daily snapshots."""

from __future__ import annotations

import logging
from typing import Any

from pipeline.ai.briefing import _get_provider
from pipeline.ai.weekly import generate_weekly_recap
from pipeline.recap import (
    build_weekly_market_summary,
    build_weekly_news_digest,
    get_week_window,
    load_daily_snapshots,
)
from pipeline.render.weekly import render_weekly_recap

logger = logging.getLogger("daily-brief.weekly")


def _load_provider(config: dict, no_llm: bool = False) -> Any | None:
    if no_llm:
        return None
    try:
        return _get_provider(config)
    except Exception as exc:
        logger.warning("Weekly recap LLM unavailable: %s", exc)
        return None


def build_weekly_recap_data(
    config: dict,
    run_date: str,
    output_dir: str,
    no_llm: bool = False,
) -> dict[str, Any]:
    """Build week-level recap data from stored daily snapshots."""
    week_window = get_week_window(run_date)
    snapshots = load_daily_snapshots(
        output_dir,
        week_window["start_date"],
        week_window["end_date"],
    )
    provider = _load_provider(config, no_llm=no_llm)

    weekly_data = {
        **week_window,
        "snapshot_count": len(snapshots),
    }
    weekly_data["markets"] = build_weekly_market_summary(snapshots)

    news_top_n = max(3, int(config.get("news", {}).get("top_n", 5)))
    news_digest = build_weekly_news_digest(
        config,
        snapshots,
        provider=provider,
        top_n=news_top_n,
    )
    weekly_data.update({
        "unique_story_count": news_digest["unique_story_count"],
        "world_news_raw": news_digest["world_raw"],
        "korea_news_raw": news_digest["korea_raw"],
        "world_news_ko": news_digest["world_ko"],
        "world_news_en": news_digest["world_en"],
        "korea_news_ko": news_digest["korea_ko"],
        "korea_news_en": news_digest["korea_en"],
    })

    if provider:
        weekly_data["insight_ko"] = generate_weekly_recap(config, weekly_data, lang="ko")
        weekly_data["insight_en"] = generate_weekly_recap(config, weekly_data, lang="en")
    else:
        weekly_data["insight_ko"] = ""
        weekly_data["insight_en"] = ""

    if not snapshots:
        logger.warning(
            "No daily snapshots found for %s → %s. Rendering an empty weekly shell.",
            week_window["start_date"],
            week_window["end_date"],
        )

    return weekly_data


def run_weekly_recap(
    config: dict,
    run_date: str,
    output_dir: str,
    no_llm: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Generate a weekly recap site from stored daily snapshots."""
    weekly_data = build_weekly_recap_data(
        config,
        run_date,
        output_dir,
        no_llm=no_llm,
    )
    html_path = render_weekly_recap(config, weekly_data, output_dir)
    return html_path, weekly_data
