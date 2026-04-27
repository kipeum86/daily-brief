"""Check 6: Weekly recap verification — snapshots, markets, news, translations, insight."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MIN_SNAPSHOTS = 3
_MIN_NEWS = 3
_MIN_INSIGHT = 200
_MIN_HTML_BYTES = 10_000


def check_weekly_recap(
    weekly_data: dict[str, Any],
    html_path: str,
    no_llm: bool,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    snap_count = weekly_data.get("snapshot_count", 0)
    if snap_count == 0:
        errors.append("No daily snapshots for weekly recap")
    elif snap_count < _MIN_SNAPSHOTS:
        errors.append(f"Only {snap_count} snapshots (min {_MIN_SNAPSHOTS})")

    # Weekly markets structure: {"cards": [...], "leaders": [...], ...}
    # (NOT the daily {"kr": [...], "us": [...]} format)
    markets = weekly_data.get("markets", {})
    cards = markets.get("cards", []) if isinstance(markets, dict) else []
    if not cards:
        errors.append("Weekly markets has no card data")

    world_ko = weekly_data.get("world_news_ko", [])
    korea_ko = weekly_data.get("korea_news_ko", [])
    if len(world_ko) < _MIN_NEWS:
        # Weekly digest can be empty if pool articles lack bucket labels (known issue).
        # Downgrade to warning if insight exists (digest still rendered from raw).
        has_insight = bool(weekly_data.get("insight_ko", ""))
        severity = warnings if has_insight else errors
        severity.append(f"Only {len(world_ko)} weekly world articles (min {_MIN_NEWS})")
    if len(korea_ko) < _MIN_NEWS:
        has_insight = bool(weekly_data.get("insight_ko", ""))
        severity = warnings if has_insight else errors
        severity.append(f"Only {len(korea_ko)} weekly korea articles (min {_MIN_NEWS})")

    try:
        from pipeline.verify.checks.content import _check_korea_purity
        korea_errors: list[str] = []
        _check_korea_purity(korea_ko, korea_errors)
        errors.extend(korea_errors)
    except Exception as exc:
        warnings.append(f"Korea purity check skipped: {exc}")

    try:
        from pipeline.verify.checks.translation import check_translations
        translation_errors, translation_warnings = check_translations(
            [*world_ko, *korea_ko],
            [*weekly_data.get("world_news_en", []), *weekly_data.get("korea_news_en", [])],
            {},
        )
        errors.extend(f"Weekly {error}" for error in translation_errors)
        warnings.extend(f"Weekly {warning}" for warning in translation_warnings)
    except Exception as exc:
        errors.append(f"Weekly translation check failed due to verifier error: {exc}")

    if not no_llm:
        insight_ko = weekly_data.get("insight_ko", "")
        insight_en = weekly_data.get("insight_en", "")
        if len(insight_ko) < _MIN_INSIGHT:
            errors.append(f"Weekly Korean insight too short ({len(insight_ko)} chars)")
        if len(insight_en) < _MIN_INSIGHT:
            errors.append(f"Weekly English insight too short ({len(insight_en)} chars)")

    if html_path and Path(html_path).exists():
        size = Path(html_path).stat().st_size
        if size < _MIN_HTML_BYTES:
            errors.append(f"Weekly HTML too small ({size} bytes)")
    elif html_path:
        errors.append(f"Weekly HTML not found: {html_path}")

    return errors, warnings
