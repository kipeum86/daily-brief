"""Archive daily briefing data to Google Sheets.

Public API:
    save_to_sheets(config, markets_data, news_articles, insight_text, run_date) → bool
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

logger = logging.getLogger("daily-brief.deliver.sheets")


def _extract_market_value(markets: dict[str, list], category: str, name: str) -> float:
    """Extract a specific market value by category and name.

    Returns the price as float, or 0.0 if not found.
    """
    items = markets.get(category, [])
    for item in items:
        if isinstance(item, dict):
            item_name = item.get("name", "")
            item_price = item.get("price", 0)
        else:
            item_name = getattr(item, "name", "")
            item_price = getattr(item, "price", 0)

        if item_name == name:
            return float(item_price)

    return 0.0


def _count_articles(articles: list) -> int:
    """Count total news articles."""
    return len(articles) if articles else 0


def _truncate_insight(text: str, max_len: int = 50) -> str:
    """Truncate insight text for the sheet summary column."""
    import re

    # Strip HTML
    clean = re.sub(r"<[^>]+>", "", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()

    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1] + "…"


def save_to_sheets(
    config: dict[str, Any],
    markets_data: dict[str, list],
    news_articles: list,
    insight_text: str,
    run_date: str,
) -> bool:
    """Append a summary row to Google Sheets for archival.

    Row format: [date, KOSPI, S&P500, USD/KRW, VIX, news_count, insight_summary]

    Args:
        config: Loaded config dict (needs sheets.enabled, sheets.spreadsheet_id).
        markets_data: Dict of market category → list of MarketData or dicts.
        news_articles: List of Article objects or dicts.
        insight_text: Raw AI insight text.
        run_date: ISO date string (YYYY-MM-DD).

    Returns:
        True if row was appended successfully, False otherwise.
    """
    # Check if sheets archival is enabled
    sheets_config = config.get("sheets", {})
    if not sheets_config.get("enabled", False):
        logger.debug("Google Sheets archival disabled in config — skipping")
        return False

    spreadsheet_id = sheets_config.get("spreadsheet_id", "")
    if not spreadsheet_id or spreadsheet_id == "YOUR_SPREADSHEET_ID":
        logger.warning(
            "Google Sheets spreadsheet_id not configured — skipping"
        )
        return False

    # Check for credentials
    creds_b64 = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
    if not creds_b64:
        logger.warning(
            "GOOGLE_SHEETS_CREDENTIALS not set — sheets archival skipped. "
            "Set the env var with base64-encoded service account JSON."
        )
        return False

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        # Decode base64 credentials
        creds_json = json.loads(base64.b64decode(creds_b64))

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials = Credentials.from_service_account_info(
            creds_json, scopes=scopes,
        )

        gc = gspread.authorize(credentials)
        spreadsheet = gc.open_by_key(spreadsheet_id)

        # Use the first worksheet
        worksheet = spreadsheet.sheet1

        # Build the row
        kospi = _extract_market_value(markets_data, "kr", "KOSPI")
        sp500 = _extract_market_value(markets_data, "us", "S&P 500")
        usdkrw = _extract_market_value(markets_data, "fx", "USD/KRW")
        vix = _extract_market_value(markets_data, "risk", "VIX")
        news_count = _count_articles(news_articles)
        insight_summary = _truncate_insight(insight_text)

        row = [
            run_date,
            kospi,
            sp500,
            usdkrw,
            vix,
            news_count,
            insight_summary,
        ]

        worksheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Appended row to Google Sheets: %s", run_date)
        return True

    except ImportError as exc:
        logger.error(
            "Required packages not installed: %s. "
            "Run: pip install gspread google-auth",
            exc,
        )
        return False

    except Exception as exc:
        logger.error(
            "Failed to save to Google Sheets: %s", exc, exc_info=True
        )
        return False
