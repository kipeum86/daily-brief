"""Pre-deploy verification gate — orchestrates all checks."""
from __future__ import annotations

import json
import logging
import os
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
    write_summary: bool = False,
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
            logger.exception("Check '%s' raised exception", name)
            all_errors.append(f"Check '{name}' failed due to verifier error: {exc}")

    passed = len(all_errors) == 0 and checks_passed == checks_run

    result = GateResult(
        passed=passed,
        errors=all_errors,
        warnings=all_warnings,
        checks_run=checks_run,
        checks_passed=checks_passed,
    )

    _save_log(result, run_date, config)
    if write_summary:
        _write_github_summary(result, "Daily Brief Verification", run_date)
    return result


def run_weekly_checks(
    weekly_data: dict[str, Any],
    html_path: str,
    no_llm: bool = False,
    write_summary: bool = False,
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
            logger.exception("Weekly check '%s' raised exception", name)
            all_errors.append(f"Weekly check '{name}' failed due to verifier error: {exc}")

    passed = len(all_errors) == 0 and checks_passed == checks_run
    result = GateResult(
        passed=passed, errors=all_errors, warnings=all_warnings,
        checks_run=checks_run, checks_passed=checks_passed,
    )
    if write_summary:
        _write_github_summary(result, "Weekly Recap Verification", _weekly_label(weekly_data))
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


def _write_github_summary(result: GateResult, title: str, label: str = "") -> None:
    """Append a compact verification summary to GitHub Actions step summary."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    try:
        lines = [
            f"## {title}",
            "",
        ]
        if label:
            lines.append(f"- run: {_one_line(label)}")
        lines.extend([
            f"- passed: {str(result.passed).lower()}",
            f"- checks: {result.checks_passed}/{result.checks_run}",
        ])

        _append_issue_section(lines, "Errors", result.errors)
        _append_issue_section(lines, "Warnings", result.warnings)

        path = Path(summary_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n\n")
    except Exception as exc:
        logger.warning("Failed to write GitHub Actions summary: %s", exc)


def _append_issue_section(lines: list[str], heading: str, issues: list[str]) -> None:
    if not issues:
        return

    max_items = 20
    lines.extend(["", f"### {heading}"])
    for issue in issues[:max_items]:
        lines.append(f"- {_one_line(issue)}")
    remaining = len(issues) - max_items
    if remaining > 0:
        lines.append(f"- ... and {remaining} more")


def _one_line(value: Any, max_len: int = 500) -> str:
    text = " ".join(str(value).split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _weekly_label(weekly_data: dict[str, Any]) -> str:
    week_id = weekly_data.get("week_id")
    if week_id:
        return str(week_id)
    start_date = weekly_data.get("start_date")
    end_date = weekly_data.get("end_date")
    if start_date and end_date:
        return f"{start_date} -> {end_date}"
    return ""


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
