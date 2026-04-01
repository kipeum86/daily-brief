"""Daily Brief orchestrator — collects market data, news, generates AI briefing."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
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
        choices=["daily", "weekly", "monthly"],
        default="daily",
        help="Briefing mode to run (default: daily)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Placeholder stubs for modules that don't exist yet
# ---------------------------------------------------------------------------

def _generate_briefing_stub(
    config: dict,
    markets: dict[str, list[dict[str, Any]]],
    news: list,
    run_date: str,
) -> str:
    logger.warning("pipeline.ai.briefing not implemented yet — returning empty insight")
    return ""


def _render_dashboard_stub(
    config: dict,
    markets: dict[str, list[dict[str, Any]]],
    holidays: dict[str, Any],
    news: list,
    insight: str,
    run_date: str,
    output_dir: str,
    **_: Any,
) -> str:
    logger.warning("pipeline.render.dashboard not implemented yet — returning empty path")
    return ""


def _send_email_stub(config: dict, html_path: str, run_date: str) -> None:
    logger.warning("pipeline.deliver.mailer not implemented yet")


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
    elif brief_type == "monthly":
        news_config["days_back"] = max(int(news_config.get("days_back", 2)), 30)
        news_config["top_n"] = max(
            int(news_config.get("top_n", 5)),
            int(news_config.get("top_n_weekend", 8)),
        )
        logger.warning(
            "Monthly mode currently reuses the daily pipeline with a 30-day lookback."
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Execute the full daily-brief pipeline. Returns exit code."""

    start = time.monotonic()
    errors: list[str] = []
    sections: list[str] = []

    # ── 1. Load + validate config ─────────────────────────────────────────
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
            return 1
        config = get_config_with_defaults(raw_config)
        setup_logging(config)
        if not validate_config(config):
            logger.critical("Config validation failed")
            return 1
        _apply_brief_type_overrides(config, args.brief_type)
    except Exception as exc:
        logger.critical("Failed to load config: %s", exc)
        return 1

    # Resolve run date (KST — target audience is in Korea)
    tz_name = config.get("briefing", {}).get("timezone", "Asia/Seoul")
    run_date: str = args.date or datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    logger.info("Run date: %s", run_date)
    logger.info("Brief type: %s", args.brief_type)

    # Output dirs
    output_dir: str = config.get("output", {}).get("dir", "output")
    archive_dir: str = config.get("output", {}).get("archive_dir", "output/archive")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(archive_dir).mkdir(parents=True, exist_ok=True)

    if args.brief_type == "weekly":
        logger.info("Weekly recap mode: building from stored daily snapshots")
        try:
            from pipeline.weekly import run_weekly_recap
            html_path, weekly_data = run_weekly_recap(
                config,
                run_date,
                output_dir,
                no_llm=args.no_llm,
            )
            logger.info("Weekly recap output: %s", html_path or "(none)")
            if args.dry_run:
                logger.info("Weekly email skipped (--dry-run)")
            else:
                logger.info("Sending weekly recap email")
                send_email = _import_or_stub(
                    "pipeline.deliver.mailer", "send_email",
                    _send_email_stub,
                )
                weekly_config = _config_with_email_overrides(
                    config,
                    sender_name="Weekly Recap",
                    subject_prefix="Weekly Recap",
                )
                week_id = ""
                try:
                    week_id = weekly_data.get("week_id", "")
                except Exception as window_exc:
                    logger.warning("Could not resolve weekly window for email subject: %s", window_exc)
                email_date = week_id or run_date
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

    # ── 2. Collect market data ────────────────────────────────────────────
    logger.info("Stage 2/10: Collecting market data")
    markets: dict[str, list[dict[str, Any]]] = {}
    try:
        from pipeline.markets.collector import collect_market_data
        markets = collect_market_data(config)
        sections.append("markets")
        logger.info("Market data collected: %d sections",
                     sum(1 for v in markets.values() if v))
    except Exception as exc:
        logger.error("Market data collection failed: %s", exc)
        errors.append(f"markets: {exc}")

    # ── 3. Calculate indicators + detect holidays ─────────────────────────
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
            holidays = detect_holidays(markets)
            # 스파크라인 SVG 생성
            for section_items in markets.values():
                for item in section_items:
                    sparkline = item.get("sparkline", [])
                    if sparkline:
                        item["sparkline_svg"] = generate_sparkline_svg(sparkline)
            sections.append("indicators")
    except Exception as exc:
        logger.error("Indicator calculation failed: %s", exc)
        errors.append(f"indicators: {exc}")

    # ── 4. Collect news (RSS + Naver API) ──────────────────────────────
    logger.info("Stage 4/10: Collecting news (RSS + Naver)")
    from pipeline.models import Article, DedupSnapshot
    articles: list[Article] = []
    try:
        # 글로벌 뉴스: RSS
        from pipeline.news.collector import collect_articles
        articles, failed_sources = collect_articles(config)
        if failed_sources:
            logger.warning("Failed RSS sources: %s", ", ".join(failed_sources))

        # 한국 뉴스: 네이버 API (config에서 source: "naver"이면)
        korea_source = config.get("news", {}).get("korea", {}).get("source", "rss")
        if korea_source == "naver":
            try:
                from pipeline.news.naver import collect_naver_news
                naver_articles = collect_naver_news(config)
                # Article 객체로 변환하여 합침
                for na in naver_articles:
                    articles.append(Article(
                        title=na["title"],
                        url=na["url"],
                        source=na.get("source", "네이버뉴스"),
                        description=na.get("summary", ""),
                        published_date=na.get("published", ""),
                    ))
                logger.info("네이버 뉴스 %d개 추가", len(naver_articles))
            except Exception as naver_exc:
                logger.warning("네이버 뉴스 수집 실패 (RSS fallback 없음): %s", naver_exc)

        sections.append("news")
    except Exception as exc:
        logger.error("News collection failed: %s", exc)
        errors.append(f"news: {exc}")

    # ── 5. Deduplicate news ───────────────────────────────────────────────
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

    # ── 6. Filter news by keywords ────────────────────────────────────────
    logger.info("Stage 6/10: Filtering news by keywords")
    try:
        from pipeline.news.filters import keyword_filter
        keywords_config = config.get("keywords", {})
        articles = keyword_filter(articles, keywords_config)
    except Exception as exc:
        logger.error("News filtering failed: %s", exc)
        errors.append(f"filter: {exc}")

    all_articles = list(articles)
    if len(all_articles) < 3:
        logger.warning("Only %d articles collected — skipping email send", len(all_articles))
        config.setdefault("email", {})["enabled"] = False

    # ── 6.5. AI 뉴스 중요도 선별 ──────────────────────────────────────────
    if articles:
        logger.info("Stage 6.5: Selecting and classifying top news")
        try:
            from pipeline.ai.briefing import _get_provider
            from pipeline.news.quality_gates import run_quality_gates
            from pipeline.news.selector import select_and_classify_news

            selector_provider = None if args.no_llm else _get_provider(config)
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

                classified = select_and_classify_news(None, all_articles, top_n=config.get("news", {}).get("top_n", 5), config=config)
                world_selected, korea_selected = run_quality_gates(
                    classified.get("world", []),
                    classified.get("korea", []),
                    config,
                )
                articles = world_selected + korea_selected
            except Exception:
                logger.warning("Heuristic news selection fallback also failed")
                articles = all_articles

    # ── 7. Generate AI briefing + translate news (Korean + English) ─────
    insight: str = ""
    insight_en: str = ""
    articles_ko: list = articles  # default: originals
    articles_en: list = articles
    try:
        from pipeline.news.collector import fill_missing_descriptions
        summary_fill_limit = max(int(config.get("news", {}).get("top_n", 5)) * 2, 10)
        fill_missing_descriptions(articles[:summary_fill_limit])
    except Exception as exc:
        logger.warning("Missing-summary fallback failed: %s", exc)

    if args.no_llm:
        logger.info("Stage 7/10: Skipped (--no-llm)")
    else:
        logger.info("Stage 7/10: Generating AI briefing + translating news (ko + en)")
        try:
            generate_briefing = _import_or_stub(
                "pipeline.ai.briefing", "generate_briefing",
                _generate_briefing_stub,
            )
            insight = generate_briefing(config, markets, articles, lang="ko", run_date=run_date)
            if insight:
                sections.append("ai_insight_ko")
        except Exception as exc:
            logger.error("AI briefing (ko) failed: %s", exc)
            errors.append(f"ai_ko: {exc}")
        try:
            if generate_briefing != _generate_briefing_stub:
                insight_en = generate_briefing(config, markets, articles, lang="en", run_date=run_date)
                if insight_en:
                    sections.append("ai_insight_en")
        except Exception as exc:
            logger.error("AI briefing (en) failed: %s", exc)
            errors.append(f"ai_en: {exc}")

        # Translate only what's needed per language version
        # KO ver: world(영→한) + korea(원본)
        # EN ver: world(원본) + korea(한→영)
        try:
            from pipeline.ai.translate import translate_news
            from pipeline.ai.briefing import _get_provider
            from pipeline.render.dashboard import _split_news
            provider = _get_provider(config)

            world_news, korea_news = _split_news(articles, config)

            # 한국어 버전: 영어 world 뉴스만 한국어로 번역
            world_ko = translate_news(provider, world_news, target_lang="ko")
            articles_ko = world_ko + korea_news  # world(번역) + korea(원본)

            # 영어 버전: 한국어 korea 뉴스만 영어로 번역
            korea_en = translate_news(provider, korea_news, target_lang="en")
            articles_en = world_news + korea_en  # world(원본) + korea(번역)

            sections.append("news_translated")
            logger.info("번역 완료: world %d개(→한국어) + korea %d개(→영어)", len(world_news), len(korea_news))
        except Exception as exc:
            logger.warning("News translation failed (using originals): %s", exc)
            articles_ko = articles
            articles_en = articles

    # ── 8. Render dashboard HTML (Korean + English) ──────────────────────
    logger.info("Stage 8/10: Rendering dashboard HTML (ko + en)")
    html_path: str = ""
    try:
        render_dashboard = _import_or_stub(
            "pipeline.render.dashboard", "render_dashboard",
            _render_dashboard_stub,
        )
        html_path = render_dashboard(
            config, markets, holidays, articles, insight, run_date, output_dir,
            insight_en=insight_en,
            articles_ko=articles_ko,
            articles_en=articles_en,
            market_pulse=market_pulse,
        )
        if html_path:
            sections.append("dashboard")
    except Exception as exc:
        logger.critical("Dashboard render failed (critical): %s", exc)
        errors.append(f"render: {exc}")
        return 1

    try:
        from pipeline.recap import save_daily_snapshot
        save_daily_snapshot(
            config,
            markets,
            holidays,
            articles,
            articles_ko,
            articles_en,
            insight,
            insight_en,
            run_date,
            output_dir,
            market_pulse=market_pulse,
            all_candidates=all_articles,
        )
        sections.append("snapshot")
    except Exception as exc:
        logger.warning("Daily snapshot save failed: %s", exc)

    # ── 9. Send email ─────────────────────────────────────────────────────
    if args.dry_run:
        logger.info("Stage 9/10: Skipped (--dry-run)")
    else:
        logger.info("Stage 9/10: Sending email")
        try:
            send_email = _import_or_stub(
                "pipeline.deliver.mailer", "send_email",
                _send_email_stub,
            )
            # 이메일 전용 템플릿 — 웹과 동일한 데이터 사용
            try:
                from pipeline.render.email import render_email
                email_html = render_email(
                    config, markets, holidays,
                    articles_ko if articles_ko else articles,
                    insight, run_date,
                    market_pulse=market_pulse,
                )
            except Exception as email_render_exc:
                logger.warning("Email template render failed: %s — using dashboard HTML", email_render_exc)
                email_html = Path(html_path).read_text(encoding="utf-8")
            send_email(config, email_html, run_date, insight_text=insight)
            sections.append("email")
        except Exception as exc:
            logger.error("Email delivery failed: %s", exc)
            errors.append(f"email: {exc}")

    # ── 10. Save to Google Sheets ─────────────────────────────────────────
    if args.dry_run:
        logger.info("Stage 10/10: Skipped (--dry-run)")
    else:
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

    # ── Summary ───────────────────────────────────────────────────────────
    elapsed = time.monotonic() - start
    logger.info("=" * 60)
    logger.info("Daily Brief complete in %.1fs", elapsed)
    logger.info("  Date:      %s", run_date)
    logger.info("  Sections:  %s", ", ".join(sections) if sections else "(none)")
    logger.info("  Markets:   %d tickers", sum(len(v) for v in markets.values()))
    logger.info("  News:      %d articles", len(articles))
    logger.info("  AI:        %s", "yes" if insight else "no")
    logger.info("  Output:    %s", html_path or "(none)")
    logger.info("  Errors:    %d", len(errors))
    if errors:
        for err in errors:
            logger.warning("  - %s", err)
    logger.info("=" * 60)

    if args.dry_run:
        print(f"\n[DRY RUN] {run_date} | "
              f"{len(sections)} sections | "
              f"{sum(len(v) for v in markets.values())} tickers | "
              f"{len(articles)} articles | "
              f"{'AI' if insight else 'no AI'} | "
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
