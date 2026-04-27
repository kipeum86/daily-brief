"""Config loading, validation, and defaults for daily-brief."""

import logging
import os
from copy import deepcopy
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = str(_PROJECT_ROOT / "config.yaml")

DEFAULTS = {
    "briefing": {
        "name": "Daily Brief",
        "language": "ko",
        "timezone": "Asia/Seoul",
    },
    "llm": {
        "provider": "gemini",
        "model": "",
        "analysis_model": "",
        "selection_model": "",
        "translation_model": "",
        "max_input_chars": 8000,
    },
    "markets": {
        "kr": {"indices": [], "names": []},
        "us": {"indices": [], "names": []},
        "fx": {"pairs": [], "names": []},
        "commodities": {"tickers": [], "names": []},
        "crypto": {"tickers": [], "names": []},
        "risk": {"tickers": [], "names": [], "fred_series": []},
    },
    "news": {
        "world": [],
        "korea": [],
        "finance": [],
        "top_n": 5,
        "top_n_weekend": 8,
        "days_back": 2,
    },
    "keywords": {"include": [], "exclude": []},
    "dedup": {
        "source_similarity_threshold": 0.75,
        "cross_similarity_threshold": 0.60,
        "min_overlap_tokens": 3,
        "event_key_enabled": True,
    },
    "email": {
        "enabled": True,
        "sender_name": "Daily Brief",
        "sender_email": "",
        "subject_prefix": "Daily Brief",
        "subscribers": [],
    },
    "alerts": {
        "failure_email_enabled": True,
    },
    "sheets": {
        "enabled": False,
        "spreadsheet_id": "",
    },
    "output": {
        "dir": "output",
        "archive_dir": "output/archive",
    },
    "schedule": {
        "morning_cron": "0 20 * * 0-4",
        "weekly_cron": "0 0 * * 6",
    },
}

_REQUIRED_FIELDS = [
    ("llm", "provider"),
    ("news",),
]


def load_config(path: str | None = None) -> dict:
    """Load config.yaml from disk."""
    path = path or DEFAULT_CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config.yaml not found: %s", path)
        return {}
    except yaml.YAMLError as e:
        logger.error("config.yaml parse error: %s", e)
        return {}


def validate_config(config: dict) -> bool:
    """Validate required fields are present in config."""
    errors: list[str] = []

    # LLM provider must be set
    llm = config.get("llm", {})
    if not llm.get("provider"):
        errors.append("llm.provider is required")

    # At least one news source category must have entries
    news = config.get("news", {})
    has_sources = any(
        isinstance(news.get(cat), list) and len(news.get(cat, [])) > 0
        for cat in ("world", "korea", "finance")
    )
    if not has_sources:
        errors.append("At least one news source category (world/korea/finance) is required")

    # Markets: warn if empty but don't fail
    markets = config.get("markets", {})
    if not markets:
        logger.warning("No market data configured — briefing will skip markets")

    # Email: validate if enabled
    email = config.get("email", {})
    if email.get("enabled"):
        if not email.get("subscribers"):
            logger.warning("Email enabled but no subscribers configured")

    # Sheets: validate if enabled
    sheets = config.get("sheets", {})
    if sheets.get("enabled"):
        if not sheets.get("spreadsheet_id") or sheets.get("spreadsheet_id") == "YOUR_SPREADSHEET_ID":
            logger.warning("Sheets enabled but spreadsheet_id not configured")

    for err in errors:
        logger.error("Config validation error: %s", err)

    return len(errors) == 0


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, preserving base defaults."""
    result = deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def get_config_with_defaults(config: dict) -> dict:
    """Return config merged with DEFAULTS (user values override defaults)."""
    return _deep_merge(DEFAULTS, config)


def setup_logging(config: dict) -> None:
    """Configure logging from environment or config."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="[%(levelname)s] %(name)s: %(message)s",
    )
