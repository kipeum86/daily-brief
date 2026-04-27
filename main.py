"""Daily Brief orchestrator — collects market data, news, generates AI briefing."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

# .env 파일 로드 (python-dotenv 없이 직접 파싱)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

logger = logging.getLogger("daily-brief")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily Brief pipeline")
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip email + sheets delivery; print summary instead",
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip AI insight generation (data-only briefing)",
    )
    parser.add_argument(
        "--date",
        help="Override run date as YYYY-MM-DD (useful for testing)",
    )
    parser.add_argument(
        "--brief-type",
        choices=["daily", "weekly"],
        default="daily",
        help="Briefing mode to run (default: daily)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Optional delivery stubs
# ---------------------------------------------------------------------------


def _send_email_stub(config: dict, html_path: str, run_date: str) -> None:
    logger.warning("pipeline.deliver.mailer not implemented yet")


def _send_failure_email_stub(config: dict, subject: str, body: str) -> None:
    logger.warning("pipeline.deliver.mailer.send_failure_email not implemented yet")


def _save_sheets_stub(
    config: dict,
    markets: dict[str, list[dict[str, Any]]],
    news: list,
    insight: str,
    run_date: str,
) -> None:
    logger.warning("pipeline.deliver.sheets not implemented yet")


def _config_with_email_overrides(config: dict, **overrides: Any) -> dict:
    """Return a shallow config copy with email-specific overrides applied."""
    copied = dict(config)
    email_config = dict(config.get("email", {}))
    email_config.update({key: value for key, value in overrides.items() if value is not None})
    copied["email"] = email_config
    return copied


# ---------------------------------------------------------------------------
# Safe imports — fall back to stubs when a module is missing
# ---------------------------------------------------------------------------

def _import_or_stub(module_path: str, func_name: str, stub):
    """Try to import module_path.func_name; return stub on ImportError."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name)
    except (ImportError, AttributeError) as e:
        logger.debug("Could not import %s.%s: %s — using stub", module_path, func_name, e)
        return stub


def _apply_brief_type_overrides(config: dict, brief_type: str) -> None:
    """Apply lightweight config overrides for non-daily briefing modes."""
    if brief_type == "daily":
        return

    news_config = config.setdefault("news", {})
    if brief_type == "weekly":
        news_config["days_back"] = max(int(news_config.get("days_back", 2)), 7)
        news_config["top_n"] = max(
            int(news_config.get("top_n", 5)),
            int(news_config.get("top_n_weekend", 8)),
        )
        logger.info(
            "Weekly mode enabled: using snapshot-based recap with an expanded weekly top_n."
        )
        return

    raise ValueError(f"Unsupported brief type: {brief_type}")


@dataclass
class RuntimeContext:
    config: dict
    run_date: str
    output_dir: str
    archive_dir: str


@dataclass
class MarketStageResult:
    markets: dict[str, list[dict[str, Any]]]
    holidays: dict[str, Any]
    market_pulse: dict[str, Any]


@dataclass
class NewsStageResult:
    articles: list
    all_articles: list


@dataclass
class AIStageResult:
    insight: str
    insight_en: str
    articles_ko: list
    articles_en: list


@dataclass
class VerificationStageResult:
    passed: bool
    errors: list[str]
    warnings: list[str]
    checks_run: int = 0
    checks_passed: int = 0


def _failure_alert_stage(
    config: dict,
    brief_type: str,
    run_label: str,
    verification: VerificationStageResult,
    dry_run: bool,
) -> None:
    if dry_run:
        logger.info("Failure alert skipped (--dry-run)")
        return
    if verification.passed:
        return

    try:
        send_failure_email = _import_or_stub(
            "pipeline.deliver.mailer", "send_failure_email",
            _send_failure_email_stub,
        )
        subject = f"[{brief_type}] verification failed - {run_label}"
        body = _format_failure_alert_body(brief_type, run_label, verification)
        sent = send_failure_email(config, subject, body)
        if not sent:
            logger.warning("Failure alert email was not sent")
    except Exception as exc:
        logger.error("Failure alert email failed: %s", exc)


def _format_failure_alert_body(
    brief_type: str,
    run_label: str,
    verification: VerificationStageResult,
) -> str:
    lines = [
        f"{brief_type} verification failed.",
        f"Run: {run_label}",
        f"Checks: {verification.checks_passed}/{verification.checks_run}",
    ]
    run_url = _github_run_url()
    if run_url:
        lines.append(f"GitHub Actions: {run_url}")

    lines.extend(["", "Errors:"])
    if verification.errors:
        lines.extend(f"- {error}" for error in verification.errors[:20])
    else:
        lines.append("- (none)")

    if verification.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in verification.warnings[:20])

    return "\n".join(lines)


def _github_run_url() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not repository or not run_id:
        return ""
    return f"https://github.com/{repository}/actions/runs/{run_id}"


def _load_runtime_context(args: argparse.Namespace) -> RuntimeContext | None:
    logger.info("Stage 1/10: Loading config from %s", args.config)
    try:
        from pipeline.config import (
            load_config,
            validate_config,
            get_config_with_defaults,
            setup_logging,
        )
        raw_config = load_config(args.config)
        if not raw_config:
            logger.critical("Config file is empty or not found: %s", args.config)
            return None
        config = get_config_with_defaults(raw_config)
        setup_logging(config)
        if not validate_config(config):
            logger.critical("Config validation failed")
            return None
        _apply_brief_type_overrides(config, args.brief_type)
    except Exception as exc:
        logger.critical("Failed to load config: %s", exc)
        return None

    tz_name = config.get("briefing", {}).get("timezone", "Asia/Seoul")
    run_date: str = args.date or datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    logger.info("Run date: %s", run_date)
    logger.info("Brief type: %s", args.brief_type)

    output_dir: str = config.get("output", {}).get("dir", "output")
    archive_dir: str = config.get("output", {}).get("archive_dir", "output/archive")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(archive_dir).mkdir(parents=True, exist_ok=True)

    return RuntimeContext(
        config=config,
        run_date=run_date,
        output_dir=output_dir,
        archive_dir=archive_dir,
    )


def _run_weekly_mode(args: argparse.Namespace, runtime: RuntimeContext) -> int:
    logger.info("Weekly recap mode: building from stored daily snapshots")
    try:
        from pipeline.weekly import run_weekly_recap
        html_path, weekly_data = run_weekly_recap(
            runtime.config,
            runtime.run_date,
            runtime.output_dir,
            no_llm=args.no_llm,
        )
        logger.info("Weekly recap output: %s", html_path or "(none)")

        weekly_verification = VerificationStageResult(
            passed=True,
            errors=[],
            warnings=[],
        )
        try:
            from pipeline.verify.gate import run_weekly_checks
            gate_result = run_weekly_checks(
                weekly_data=weekly_data,
                html_path=html_path,
                no_llm=args.no_llm,
                write_summary=True,
            )
            weekly_verification = VerificationStageResult(
                passed=gate_result.passed,
                errors=list(gate_result.errors),
                warnings=list(gate_result.warnings),
                checks_run=gate_result.checks_run,
                checks_passed=gate_result.checks_passed,
            )
            if gate_result.warnings:
                for warning in gate_result.warnings:
                    logger.warning("[WEEKLY VERIFY] %s", warning)
            if not weekly_verification.passed:
                for error in gate_result.errors:
                    logger.error("[WEEKLY VERIFY] %s", error)
                logger.critical("Weekly verification FAILED — blocking email")
        except Exception as exc:
            logger.error("Weekly verification error: %s", exc)
            weekly_verification = VerificationStageResult(
                passed=False,
                errors=[f"Weekly verification module error: {exc}"],
                warnings=[],
            )

        if not weekly_verification.passed:
            logger.warning("Weekly email skipped (verification failed)")
            week_label = runtime.run_date
            if isinstance(weekly_data, dict):
                week_label = str(weekly_data.get("week_id") or runtime.run_date)
            _failure_alert_stage(
                runtime.config,
                "Weekly Recap",
                week_label,
                weekly_verification,
                args.dry_run,
            )
            return 1
        if args.dry_run:
            logger.info("Weekly email skipped (--dry-run)")
            return 0

        logger.info("Sending weekly recap email")
        send_email = _import_or_stub(
            "pipeline.deliver.mailer", "send_email",
            _send_email_stub,
        )
        weekly_config = _config_with_email_overrides(
            runtime.config,
            sender_name="Weekly Recap",
            subject_prefix="Weekly Recap",
        )
        week_id = ""
        try:
            week_id = weekly_data.get("week_id", "")
        except Exception as window_exc:
            logger.warning("Could not resolve weekly window for email subject: %s", window_exc)
        email_date = week_id or runtime.run_date
        try:
            from pipeline.render.email import render_weekly_email
            email_html = render_weekly_email(weekly_config, weekly_data)
        except Exception as email_render_exc:
            logger.warning(
                "Weekly email template render failed: %s — using weekly page HTML",
                email_render_exc,
            )
            email_html = Path(html_path).read_text(encoding="utf-8")
        send_email(weekly_config, email_html, email_date)
        return 0
    except Exception as exc:
        logger.critical("Weekly recap failed: %s", exc, exc_info=True)
        return 1


def _run_market_stages(
    config: dict,
    run_date: str,
    sections: list[str],
    errors: list[str],
) -> MarketStageResult:
    logger.info("Stage 2/10: Collecting market data")
    markets: dict[str, list[dict[str, Any]]] = {}
    try:
        from pipeline.markets.collector import collect_market_data
        markets = collect_market_data(config)
        sections.append("markets")
        logger.info(
            "Market data collected: %d sections",
            sum(1 for values in markets.values() if values),
        )
    except Exception as exc:
        logger.error("Market data collection failed: %s", exc)
        errors.append(f"markets: {exc}")

    logger.info("Stage 3/10: Calculating indicators, holidays & market pulse")
    holidays: dict[str, Any] = {}
    market_pulse: dict[str, Any] = {}
    try:
        from pipeline.markets.indicators import (
            calculate_indicators, detect_holidays, calculate_market_pulse,
            generate_sparkline_svg,
        )
        if markets:
            market_pulse = calculate_market_pulse(markets)
            markets = calculate_indicators(markets)
            holidays = detect_holidays(markets, run_date=run_date)
            for section_items in markets.values():
                for item in section_items:
                    sparkline = item.get("sparkline", [])
                    if sparkline:
                        item["sparkline_svg"] = generate_sparkline_svg(sparkline)
            sections.append("indicators")
    except Exception as exc:
        logger.error("Indicator calculation failed: %s", exc)
        errors.append(f"indicators: {exc}")

    return MarketStageResult(
        markets=markets,
        holidays=holidays,
        market_pulse=market_pulse,
    )


def _collect_news_stage(config: dict, sections: list[str], errors: list[str]) -> list:
    logger.info("Stage 4/10: Collecting news (RSS + Naver)")
    from pipeline.models import Article
    articles: list[Article] = []
    try:
        from pipeline.news.collector import collect_articles
        articles, failed_sources = collect_articles(config)
        if failed_sources:
            logger.warning("Failed RSS sources: %s", ", ".join(failed_sources))

        korea_source = config.get("news", {}).get("korea", {}).get("source", "rss")
        if korea_source == "naver":
            try:
                from pipeline.news.naver import collect_naver_news
                naver_articles = collect_naver_news(config)
                for article in naver_articles:
                    articles.append(Article(
                        title=article["title"],
                        url=article["url"],
                        source=article.get("source", "네이버뉴스"),
                        description=article.get("summary", ""),
                        published_date=article.get("published", ""),
                    ))
                logger.info("네이버 뉴스 %d개 추가", len(naver_articles))
            except Exception as naver_exc:
                logger.warning("네이버 뉴스 수집 실패 (RSS fallback 없음): %s", naver_exc)

        sections.append("news")
    except Exception as exc:
        logger.error("News collection failed: %s", exc)
        errors.append(f"news: {exc}")
    return articles


def _dedupe_and_filter_news_stage(
    config: dict,
    output_dir: str,
    articles: list,
    errors: list[str],
) -> list:
    logger.info("Stage 5/10: Deduplicating news")
    try:
        from pipeline.news.dedup import deduplicate_articles, load_trend_snapshot
        trends_dir = str(Path(output_dir) / "trends")
        snapshot = load_trend_snapshot(trends_dir)
        dedup_config = config.get("dedup", {})
        articles = deduplicate_articles(articles, snapshot, dedup_config)
    except Exception as exc:
        logger.error("News deduplication failed: %s", exc)
        errors.append(f"dedup: {exc}")

    logger.info("Stage 6/10: Filtering news by keywords")
    try:
        from pipeline.news.filters import keyword_filter
        keywords_config = config.get("keywords", {})
        articles = keyword_filter(articles, keywords_config)
    except Exception as exc:
        logger.error("News filtering failed: %s", exc)
        errors.append(f"filter: {exc}")
    return articles


def _select_news_stage(
    config: dict,
    articles: list,
    no_llm: bool,
) -> NewsStageResult:
    all_articles = list(articles)
    if len(all_articles) < 3:
        logger.warning("Only %d articles collected — skipping email send", len(all_articles))
        config.setdefault("email", {})["enabled"] = False

    if not articles:
        return NewsStageResult(articles=articles, all_articles=all_articles)

    logger.info("Stage 6.5: Selecting and classifying top news")
    try:
        from pipeline.ai.briefing import _get_provider
        from pipeline.news.quality_gates import run_quality_gates
        from pipeline.news.selector import select_and_classify_news

        selector_provider = None if no_llm else _get_provider(config, task="selection")
        top_n = config.get("news", {}).get("top_n", 5)
        classified = select_and_classify_news(
            selector_provider,
            all_articles,
            top_n=top_n,
            config=config,
        )
        world_selected, korea_selected = run_quality_gates(
            classified.get("world", []),
            classified.get("korea", []),
            config,
        )
        articles = world_selected + korea_selected
        logger.info(
            "News selection complete: world %d개 + korea %d개",
            len(world_selected),
            len(korea_selected),
        )
    except Exception as exc:
        logger.warning("Unified news selection failed (using full filtered set): %s", exc)
        try:
            from pipeline.news.quality_gates import run_quality_gates
            from pipeline.news.selector import select_and_classify_news

            classified = select_and_classify_news(
                None,
                all_articles,
                top_n=config.get("news", {}).get("top_n", 5),
                config=config,
            )
            world_selected, korea_selected = run_quality_gates(
                classified.get("world", []),
                classified.get("korea", []),
                config,
            )
            articles = world_selected + korea_selected
        except Exception:
            logger.exception("Heuristic news selection fallback also failed")
            articles = []

    return NewsStageResult(articles=articles, all_articles=all_articles)


def _generate_ai_stage(
    config: dict,
    markets: dict[str, list[dict[str, Any]]],
    holidays: dict[str, Any],
    articles: list,
    run_date: str,
    no_llm: bool,
    sections: list[str],
    errors: list[str],
) -> AIStageResult | None:
    insight = ""
    insight_en = ""
    articles_ko: list = articles
    articles_en: list = articles

    try:
        from pipeline.news.collector import fill_missing_descriptions
        summary_fill_limit = max(int(config.get("news", {}).get("top_n", 5)) * 2, 10)
        fill_missing_descriptions(articles[:summary_fill_limit])
    except Exception as exc:
        logger.warning("Missing-summary fallback failed: %s", exc)

    if no_llm:
        logger.info("Stage 7/10: Skipped (--no-llm)")
        return AIStageResult(
            insight=insight,
            insight_en=insight_en,
            articles_ko=articles_ko,
            articles_en=articles_en,
        )

    logger.info("Stage 7/10: Generating AI briefing + translating news (ko + en)")
    try:
        from pipeline.ai.briefing import generate_briefing
    except Exception as exc:
        logger.error("AI briefing module unavailable: %s", exc)
        errors.append(f"ai_import: {exc}")
        return None

    try:
        insight = generate_briefing(config, markets, articles, lang="ko", run_date=run_date, holidays=holidays)
        if insight:
            sections.append("ai_insight_ko")
    except Exception as exc:
        logger.error("AI briefing (ko) failed: %s", exc)
        errors.append(f"ai_ko: {exc}")

    try:
        insight_en = generate_briefing(config, markets, articles, lang="en", run_date=run_date, holidays=holidays)
        if insight_en:
            sections.append("ai_insight_en")
    except Exception as exc:
        logger.error("AI briefing (en) failed: %s", exc)
        errors.append(f"ai_en: {exc}")

    if not insight.strip() or not insight_en.strip():
        logger.critical("AI briefing failed — blocking render/deploy")
        return None

    try:
        from pipeline.ai.translate import translate_news
        from pipeline.ai.briefing import _get_provider
        from pipeline.render.dashboard import _split_news
        provider = _get_provider(config, task="translation")

        world_news, korea_news = _split_news(articles, config)
        world_ko = translate_news(provider, world_news, target_lang="ko", strict=True)
        articles_ko = world_ko + korea_news

        korea_en = translate_news(provider, korea_news, target_lang="en", strict=True)
        articles_en = world_news + korea_en

        sections.append("news_translated")
        logger.info("번역 완료: world %d개(→한국어) + korea %d개(→영어)", len(world_news), len(korea_news))
    except Exception as exc:
        logger.critical("News translation failed — blocking render/deploy: %s", exc)
        return None

    return AIStageResult(
        insight=insight,
        insight_en=insight_en,
        articles_ko=articles_ko,
        articles_en=articles_en,
    )


def _render_stage(
    config: dict,
    markets: dict[str, list[dict[str, Any]]],
    holidays: dict[str, Any],
    articles: list,
    ai: AIStageResult,
    run_date: str,
    output_dir: str,
    market_pulse: dict[str, Any],
    sections: list[str],
    errors: list[str],
) -> str | None:
    logger.info("Stage 8/10: Rendering dashboard HTML (ko + en)")
    try:
        from pipeline.render.dashboard import render_dashboard
        html_path = render_dashboard(
            config, markets, holidays, articles, ai.insight, run_date, output_dir,
            insight_en=ai.insight_en,
            articles_ko=ai.articles_ko,
            articles_en=ai.articles_en,
            market_pulse=market_pulse,
        )
        if html_path:
            sections.append("dashboard")
        return html_path
    except Exception as exc:
        logger.critical("Dashboard render failed (critical): %s", exc)
        errors.append(f"render: {exc}")
        return None


def _snapshot_stage(
    config: dict,
    markets: dict[str, list[dict[str, Any]]],
    holidays: dict[str, Any],
    articles: list,
    ai: AIStageResult,
    run_date: str,
    output_dir: str,
    market_pulse: dict[str, Any],
    all_articles: list,
    sections: list[str],
) -> None:
    try:
        from pipeline.recap import save_daily_snapshot
        save_daily_snapshot(
            config,
            markets,
            holidays,
            articles,
            ai.articles_ko,
            ai.articles_en,
            ai.insight,
            ai.insight_en,
            run_date,
            output_dir,
            market_pulse=market_pulse,
            all_candidates=all_articles,
        )
        sections.append("snapshot")
    except Exception as exc:
        logger.warning("Daily snapshot save failed: %s", exc)


def _manifest_stage(output_dir: str, sections: list[str]) -> None:
    try:
        from pipeline.render.manifest import write_manifest
        write_manifest(output_dir)
        sections.append("manifest")
    except Exception as exc:
        logger.warning("Manifest write failed (non-critical): %s", exc)


def _verify_stage(
    config: dict,
    markets: dict[str, list[dict[str, Any]]],
    holidays: dict[str, Any],
    ai: AIStageResult,
    html_path: str,
    output_dir: str,
    run_date: str,
    no_llm: bool,
) -> VerificationStageResult:
    logger.info("Stage 8.5/10: Pre-deploy verification")
    try:
        from pipeline.verify.gate import run_pre_deploy_checks
        gate_result = run_pre_deploy_checks(
            markets=markets,
            holidays=holidays,
            articles_ko=ai.articles_ko,
            articles_en=ai.articles_en,
            insight_ko=ai.insight,
            insight_en=ai.insight_en,
            html_path=html_path,
            en_html_path=str(Path(output_dir) / "en" / "index.html"),
            run_date=run_date,
            config=config,
            no_llm=no_llm,
            write_summary=True,
        )
        if gate_result.warnings:
            for warning in gate_result.warnings:
                logger.warning("[VERIFY] %s", warning)
        if not gate_result.passed:
            for error in gate_result.errors:
                logger.error("[VERIFY] %s", error)
            logger.critical(
                "Pre-deploy verification FAILED: %d errors — blocking email/deploy",
                len(gate_result.errors),
            )
        return VerificationStageResult(
            passed=gate_result.passed,
            errors=list(gate_result.errors),
            warnings=list(gate_result.warnings),
            checks_run=gate_result.checks_run,
            checks_passed=gate_result.checks_passed,
        )
    except Exception as exc:
        logger.error("Verification module error: %s", exc)
        return VerificationStageResult(
            passed=False,
            errors=[f"Verification module error: {exc}"],
            warnings=[],
        )


def _email_stage(
    config: dict,
    markets: dict[str, list[dict[str, Any]]],
    holidays: dict[str, Any],
    articles: list,
    ai: AIStageResult,
    html_path: str,
    run_date: str,
    dry_run: bool,
    market_pulse: dict[str, Any],
    sections: list[str],
    errors: list[str],
) -> None:
    if dry_run:
        logger.info("Stage 9/10: Skipped (--dry-run)")
        return

    logger.info("Stage 9/10: Sending email")
    try:
        send_email = _import_or_stub(
            "pipeline.deliver.mailer", "send_email",
            _send_email_stub,
        )
        try:
            from pipeline.render.email import render_email
            email_html = render_email(
                config, markets, holidays,
                ai.articles_ko if ai.articles_ko else articles,
                ai.insight, run_date,
                market_pulse=market_pulse,
            )
        except Exception as email_render_exc:
            logger.warning("Email template render failed: %s — using dashboard HTML", email_render_exc)
            email_html = Path(html_path).read_text(encoding="utf-8")
        send_email(config, email_html, run_date, insight_text=ai.insight)
        sections.append("email")
    except Exception as exc:
        logger.error("Email delivery failed: %s", exc)
        errors.append(f"email: {exc}")


def _sheets_stage(
    config: dict,
    markets: dict[str, list[dict[str, Any]]],
    articles: list,
    insight: str,
    run_date: str,
    dry_run: bool,
    sections: list[str],
    errors: list[str],
) -> None:
    if dry_run:
        logger.info("Stage 10/10: Skipped (--dry-run)")
        return

    logger.info("Stage 10/10: Saving to Google Sheets")
    try:
        save_sheets = _import_or_stub(
            "pipeline.deliver.sheets", "save_to_sheets",
            _save_sheets_stub,
        )
        save_sheets(config, markets, articles, insight, run_date)
        sections.append("sheets")
    except Exception as exc:
        logger.error("Sheets save failed: %s", exc)
        errors.append(f"sheets: {exc}")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Execute the full daily-brief pipeline. Returns exit code."""

    start = time.monotonic()
    errors: list[str] = []
    sections: list[str] = []

    runtime = _load_runtime_context(args)
    if runtime is None:
        return 1

    if args.brief_type == "weekly":
        return _run_weekly_mode(args, runtime)

    market_result = _run_market_stages(
        runtime.config,
        runtime.run_date,
        sections,
        errors,
    )
    raw_articles = _collect_news_stage(runtime.config, sections, errors)
    filtered_articles = _dedupe_and_filter_news_stage(
        runtime.config,
        runtime.output_dir,
        raw_articles,
        errors,
    )
    news_result = _select_news_stage(
        runtime.config,
        filtered_articles,
        args.no_llm,
    )

    ai_result = _generate_ai_stage(
        runtime.config,
        market_result.markets,
        market_result.holidays,
        news_result.articles,
        runtime.run_date,
        args.no_llm,
        sections,
        errors,
    )
    if ai_result is None:
        return 1

    html_path = _render_stage(
        runtime.config,
        market_result.markets,
        market_result.holidays,
        news_result.articles,
        ai_result,
        runtime.run_date,
        runtime.output_dir,
        market_result.market_pulse,
        sections,
        errors,
    )
    if html_path is None:
        return 1

    _snapshot_stage(
        runtime.config,
        market_result.markets,
        market_result.holidays,
        news_result.articles,
        ai_result,
        runtime.run_date,
        runtime.output_dir,
        market_result.market_pulse,
        news_result.all_articles,
        sections,
    )
    _manifest_stage(runtime.output_dir, sections)

    verification = _verify_stage(
        runtime.config,
        market_result.markets,
        market_result.holidays,
        ai_result,
        html_path,
        runtime.output_dir,
        runtime.run_date,
        args.no_llm,
    )
    if not verification.passed:
        logger.warning("Stage 9/10: Skipped (verification failed)")
        _failure_alert_stage(
            runtime.config,
            "Daily Brief",
            runtime.run_date,
            verification,
            args.dry_run,
        )
        return 1

    _email_stage(
        runtime.config,
        market_result.markets,
        market_result.holidays,
        news_result.articles,
        ai_result,
        html_path,
        runtime.run_date,
        args.dry_run,
        market_result.market_pulse,
        sections,
        errors,
    )
    _sheets_stage(
        runtime.config,
        market_result.markets,
        news_result.articles,
        ai_result.insight,
        runtime.run_date,
        args.dry_run,
        sections,
        errors,
    )

    # ── Summary ───────────────────────────────────────────────────────────
    elapsed = time.monotonic() - start
    logger.info("=" * 60)
    logger.info("Daily Brief complete in %.1fs", elapsed)
    logger.info("  Date:      %s", runtime.run_date)
    logger.info("  Sections:  %s", ", ".join(sections) if sections else "(none)")
    logger.info("  Markets:   %d tickers", sum(len(v) for v in market_result.markets.values()))
    logger.info("  News:      %d articles", len(news_result.articles))
    logger.info("  AI:        %s", "yes" if ai_result.insight else "no")
    logger.info("  Output:    %s", html_path or "(none)")
    logger.info("  Errors:    %d", len(errors))
    if errors:
        for err in errors:
            logger.warning("  - %s", err)
    logger.info("=" * 60)

    if args.dry_run:
        print(f"\n[DRY RUN] {runtime.run_date} | "
              f"{len(sections)} sections | "
              f"{sum(len(v) for v in market_result.markets.values())} tickers | "
              f"{len(news_result.articles)} articles | "
              f"{'AI' if ai_result.insight else 'no AI'} | "
              f"{len(errors)} errors")

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
