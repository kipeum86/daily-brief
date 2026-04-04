"""Pre-deploy verification gate — orchestrates all checks."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0


def run_pre_deploy_checks(
    markets: dict[str, list[dict]],
    holidays: dict[str, Any],
    articles_ko: list,
    articles_en: list,
    insight_ko: str,
    insight_en: str,
    html_path: str,
    en_html_path: str,
    run_date: str,
    config: dict,
    no_llm: bool = False,
) -> GateResult:
    """Run all daily pre-deploy checks and return a GateResult."""
    all_errors: list[str] = []
    all_warnings: list[str] = []
    checks_run = 0
    checks_passed = 0

    check_fns = [
        ("market_data", lambda: _run_market_data(markets, holidays, run_date)),
        ("insight", lambda: _run_insight(insight_ko, insight_en, markets, holidays, no_llm)),
        ("translation", lambda: _run_translation(articles_ko, articles_en, config)),
        ("content", lambda: _run_content(articles_ko, articles_en, insight_ko, insight_en, run_date, no_llm)),
        ("html", lambda: _run_html(html_path, en_html_path, no_llm, run_date)),
    ]

    for name, fn in check_fns:
        checks_run += 1
        try:
            errors, warnings = fn()
            all_errors.extend(errors)
            all_warnings.extend(warnings)
            if not errors:
                checks_passed += 1
            else:
                logger.warning("Check '%s' FAILED: %s", name, errors)
        except Exception as exc:
            logger.error("Check '%s' raised exception: %s — skipping", name, exc)
            all_warnings.append(f"Check '{name}' skipped due to error: {exc}")
            checks_passed += 1  # don't block on broken check

    passed = len(all_errors) == 0

    result = GateResult(
        passed=passed,
        errors=all_errors,
        warnings=all_warnings,
        checks_run=checks_run,
        checks_passed=checks_passed,
    )

    _save_log(result, run_date, config)
    return result


def run_weekly_checks(
    weekly_data: dict[str, Any],
    html_path: str,
    no_llm: bool = False,
) -> GateResult:
    """Run weekly recap pre-deploy checks."""
    all_errors: list[str] = []
    all_warnings: list[str] = []
    checks_run = 0
    checks_passed = 0

    check_fns = [
        ("weekly", lambda: _run_weekly(weekly_data, html_path, no_llm)),
    ]

    for name, fn in check_fns:
        checks_run += 1
        try:
            errors, warnings = fn()
            all_errors.extend(errors)
            all_warnings.extend(warnings)
            if not errors:
                checks_passed += 1
            else:
                logger.warning("Weekly check '%s' FAILED: %s", name, errors)
        except Exception as exc:
            logger.error("Weekly check '%s' raised exception: %s — skipping", name, exc)
            all_warnings.append(f"Check '{name}' skipped: {exc}")
            checks_passed += 1

    passed = len(all_errors) == 0
    result = GateResult(
        passed=passed, errors=all_errors, warnings=all_warnings,
        checks_run=checks_run, checks_passed=checks_passed,
    )
    return result


def _save_log(result: GateResult, run_date: str, config: dict) -> None:
    try:
        output_dir = config.get("output", {}).get("dir", "output")
        log_path = Path(output_dir) / "data" / "verification-log.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "date": run_date,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **asdict(result),
        }
        log_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Verification log saved → %s", log_path)
    except Exception as exc:
        logger.warning("Failed to save verification log: %s", exc)


# --- Delegators (import checks lazily to avoid circular imports) ---

def _run_market_data(markets, holidays, run_date):
    from pipeline.verify.checks.market_data import check_market_data
    return check_market_data(markets, holidays, run_date)

def _run_insight(insight_ko, insight_en, markets, holidays, no_llm):
    from pipeline.verify.checks.insight import check_insight_accuracy
    return check_insight_accuracy(insight_ko, insight_en, markets, holidays, no_llm)

def _run_translation(articles_ko, articles_en, config):
    from pipeline.verify.checks.translation import check_translations
    return check_translations(articles_ko, articles_en, config)

def _run_content(articles_ko, articles_en, insight_ko, insight_en, run_date, no_llm):
    from pipeline.verify.checks.content import check_content_completeness
    return check_content_completeness(articles_ko, articles_en, insight_ko, insight_en, run_date, no_llm)

def _run_html(html_path, en_html_path, no_llm, run_date):
    from pipeline.verify.checks.html import check_html_integrity
    return check_html_integrity(html_path, en_html_path, no_llm, run_date)

def _run_weekly(weekly_data, html_path, no_llm):
    from pipeline.verify.checks.weekly import check_weekly_recap
    return check_weekly_recap(weekly_data, html_path, no_llm)
