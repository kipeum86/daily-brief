"""Render Jinja2 dashboard templates to static HTML files.

Public API (called from main.py):
    render_dashboard(config, markets, holidays, articles, insight, run_date, output_dir)
        → returns path to the generated index.html
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger("daily-brief.render")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATES_DIR = _PROJECT_ROOT / "templates" / "dashboard"
_MIN_HTML_LENGTH = 1000  # reject obviously broken output

# Day-of-week names
_KO_WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
_EN_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_EN_MONTHS = ["", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def _format_date_korean(iso_date: str) -> str:
    """Convert '2026-03-24' → '2026년 3월 24일 월요일'."""
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    weekday = _KO_WEEKDAYS[dt.weekday()]
    return f"{dt.year}년 {dt.month}월 {dt.day}일 {weekday}"


def _format_date_english(iso_date: str) -> str:
    """Convert '2026-03-24' → 'Monday, March 24, 2026'."""
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    weekday = _EN_WEEKDAYS[dt.weekday()]
    month = _EN_MONTHS[dt.month]
    return f"{weekday}, {month} {dt.day}, {dt.year}"


def _md_to_html(text: str) -> str:
    """Markdown 텍스트를 HTML로 변환. AI insight용."""
    if not text:
        return ""
    try:
        import markdown
        return markdown.markdown(text, extensions=["smarty"])
    except ImportError:
        # markdown 패키지 없으면 간이 변환
        import re
        html = text
        # ## 제목 → <h3>
        html = re.sub(r"^## (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
        # * 리스트 → <li>
        html = re.sub(r"^\*\s+(.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)
        # 빈 줄 → <p> 분리
        paragraphs = html.split("\n\n")
        result = []
        for p in paragraphs:
            p = p.strip()
            if p.startswith("<h3>") or p.startswith("<li>"):
                result.append(p)
            elif p:
                result.append(f"<p>{p}</p>")
        return "\n".join(result)


def _format_date(iso_date: str, lang: str = "ko") -> str:
    if lang == "en":
        return _format_date_english(iso_date)
    return _format_date_korean(iso_date)


def _find_adjacent_dates(date_iso: str, archive_dir: Path) -> tuple[str, str]:
    """Find previous/next briefing dates by scanning the archive directory.

    Returns (prev_date, next_date) as ISO strings. Empty string if none.
    """
    existing: list[str] = sorted(
        p.stem for p in archive_dir.glob("*.html") if p.stem != "index"
    )

    prev_date = ""
    next_date = ""

    if not existing:
        return prev_date, next_date

    for d in reversed(existing):
        if d < date_iso:
            prev_date = d
            break

    for d in existing:
        if d > date_iso:
            next_date = d
            break

    return prev_date, next_date


def _normalize_market_items(items: list) -> list[dict[str, Any]]:
    """Convert MarketData dataclasses or raw dicts into plain dicts for Jinja2."""
    result = []
    for item in items:
        if isinstance(item, dict):
            result.append(item)
        else:
            # Assume dataclass with .name, .price, .change_pct attributes
            result.append({
                "name": getattr(item, "name", ""),
                "price": float(getattr(item, "price", 0)),
                "change_pct": float(getattr(item, "change_pct", 0)),
                "volume": float(getattr(item, "volume", 0)),
                "ticker": getattr(item, "ticker", ""),
            })
    return result


def _split_news(articles: list, config: dict) -> tuple[list[dict], list[dict]]:
    """Split articles into world_news and korea_news lists of dicts.

    Uses the source name to classify: sources defined under news.korea go to
    korea_news, everything else goes to world_news.
    """
    korea_source_names: set[str] = set()
    korea_cfg = config.get("news", {}).get("korea", [])
    if isinstance(korea_cfg, list):
        # RSS mode: list of {"name": "연합뉴스", "url": "..."}
        for src in korea_cfg:
            if isinstance(src, dict):
                korea_source_names.add(src.get("name", ""))
    else:
        # Naver API mode: korea config is a dict, source name is "네이버뉴스"
        korea_source_names.add("네이버뉴스")

    world_news: list[dict] = []
    korea_news: list[dict] = []

    for art in articles:
        # Support both Article dataclass and plain dict
        if isinstance(art, dict):
            entry = {
                "title": art.get("title", ""),
                "summary": art.get("description", "") or art.get("summary", ""),
                "source": art.get("source", ""),
                "url": art.get("url", ""),
            }
            source_name = art.get("source", "")
        else:
            entry = {
                "title": getattr(art, "title", ""),
                "summary": getattr(art, "description", "") or getattr(art, "body", ""),
                "source": getattr(art, "source", ""),
                "url": getattr(art, "url", ""),
            }
            source_name = getattr(art, "source", "")

        if source_name in korea_source_names:
            korea_news.append(entry)
        else:
            world_news.append(entry)

    top_n = config.get("news", {}).get("top_n", 5)
    return world_news[:top_n], korea_news[:top_n]


# ---------------------------------------------------------------------------
# Core render function
# ---------------------------------------------------------------------------

def _build_template_context(
    config: dict,
    markets: dict[str, list],
    holidays: dict[str, Any],
    articles: list,
    insight: str,
    run_date: str,
    output_dir: str,
    lang: str = "ko",
) -> dict[str, Any]:
    """Assemble all Jinja2 template variables into a single context dict."""

    archive_dir = Path(output_dir) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    prev_date, next_date = _find_adjacent_dates(run_date, archive_dir)

    # Normalize market data for template consumption
    normalized_markets: dict[str, list[dict]] = {}
    for key, items in markets.items():
        normalized_markets[key] = _normalize_market_items(items)

    # Split news
    world_news, korea_news = _split_news(articles, config)

    # Site URL (strip trailing slash)
    site_url = config.get("site_url", "").rstrip("/")

    # Language toggle URLs (절대 URL로 — 아카이브에서도 작동)
    if lang == "en":
        lang_toggle_url = f"{site_url}/" if site_url else "../index.html"
        lang_toggle_label = "한국어"
        lang_current = "EN"
    else:
        lang_toggle_url = f"{site_url}/en/" if site_url else "en/index.html"
        lang_toggle_label = "English"
        lang_current = "KR"

    return {
        "date_str": _format_date(run_date, lang),
        "date_iso": run_date,
        "prev_date": prev_date,
        "next_date": next_date,
        "insight_text": _md_to_html(insight),
        "markets": normalized_markets,
        "world_news": world_news,
        "korea_news": korea_news,
        "holidays": holidays or {},
        "site_url": site_url,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chart_data": json.dumps({}, ensure_ascii=False),
        "lang": lang,
        "lang_toggle_url": lang_toggle_url,
        "lang_toggle_label": lang_toggle_label,
        "lang_current": lang_current,
    }


def render_html(data: dict[str, Any]) -> str:
    """Load the Jinja2 base template and render with the given context.

    Args:
        data: Template context dict (as produced by _build_template_context).

    Returns:
        Rendered HTML string.
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("base.html")

    # Mark insight_text as safe so it can contain <p> tags from the AI
    from markupsafe import Markup
    if data.get("insight_text"):
        data["insight_text"] = Markup(data["insight_text"])

    return template.render(**data)


def render_archive_html(dates: list[str], config: dict) -> str:
    """Render the archive listing page.

    Args:
        dates: Sorted list of date ISO strings (newest first).
        config: App config dict.

    Returns:
        Rendered HTML string.
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("archive.html")
    site_url = config.get("site_url", "").rstrip("/")
    return template.render(dates=dates, site_url=site_url)


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def save_dashboard(html: str, output_dir: str, date_iso: str) -> str:
    """Save rendered HTML to output/index.html and output/archive/{date}.html.

    Args:
        html: Rendered HTML string.
        output_dir: Base output directory (e.g. "output").
        date_iso: Date string like "2026-03-24".

    Returns:
        Path to the saved index.html.

    Raises:
        ValueError: If HTML is too short (likely broken render).
    """
    if len(html) < _MIN_HTML_LENGTH:
        raise ValueError(
            f"Rendered HTML too short ({len(html)} chars < {_MIN_HTML_LENGTH}). "
            "Refusing to save — likely a broken template render."
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    archive = out / "archive"
    archive.mkdir(parents=True, exist_ok=True)

    # Save as latest
    index_path = out / "index.html"
    index_path.write_text(html, encoding="utf-8")
    logger.info("Saved dashboard → %s (%d chars)", index_path, len(html))

    # Save archive copy
    archive_path = archive / f"{date_iso}.html"
    archive_path.write_text(html, encoding="utf-8")
    logger.info("Saved archive  → %s", archive_path)

    return str(index_path)


def save_archive_index(config: dict, output_dir: str) -> str:
    """Regenerate the archive/index.html listing page.

    Returns:
        Path to the saved archive index.
    """
    archive = Path(output_dir) / "archive"
    archive.mkdir(parents=True, exist_ok=True)

    dates = sorted(
        (p.stem for p in archive.glob("*.html") if p.stem != "index"),
        reverse=True,
    )

    html = render_archive_html(dates, config)
    archive_index = archive / "index.html"
    archive_index.write_text(html, encoding="utf-8")
    logger.info("Saved archive index → %s (%d dates)", archive_index, len(dates))
    return str(archive_index)


# ---------------------------------------------------------------------------
# Main entry point (matches the signature expected by main.py)
# ---------------------------------------------------------------------------

def render_dashboard(
    config: dict,
    markets: dict[str, list],
    holidays: dict[str, Any],
    articles: list,
    insight: str,
    run_date: str,
    output_dir: str,
    insight_en: str = "",
    articles_ko: list | None = None,
    articles_en: list | None = None,
    market_pulse: dict | None = None,
) -> str:
    """Full render pipeline: build context → render template → save files.

    Generates both Korean (default) and English versions.

    Args:
        config: Loaded config dict.
        markets: Dict of market category → list of MarketData or dicts.
        holidays: Holiday detection dict (kospi_holiday, nyse_holiday, etc.).
        articles: Original articles (used as fallback).
        insight: AI-generated insight (Korean).
        run_date: ISO date string (YYYY-MM-DD).
        output_dir: Base output directory.
        insight_en: AI-generated insight in English.
        articles_ko: Articles with all titles/summaries in Korean.
        articles_en: Articles with all titles/summaries in English.

    Returns:
        Path to the saved index.html file.
    """
    logger.info("Rendering dashboard for %s", run_date)

    # Use translated articles if available, otherwise fallback to originals
    ko_articles = articles_ko if articles_ko else articles
    en_articles = articles_en if articles_en else articles

    pulse = market_pulse or {}

    # --- Korean version (default) ---
    context_ko = _build_template_context(
        config, markets, holidays, ko_articles, insight, run_date, output_dir, lang="ko",
    )
    context_ko["market_pulse"] = pulse
    html_ko = render_html(context_ko)
    index_path = save_dashboard(html_ko, output_dir, run_date)

    # --- English version ---
    en_dir = Path(output_dir) / "en"
    en_dir.mkdir(parents=True, exist_ok=True)
    en_insight = insight_en if insight_en else insight
    context_en = _build_template_context(
        config, markets, holidays, en_articles, en_insight, run_date, output_dir, lang="en",
    )
    context_en["market_pulse"] = pulse
    _site = config.get("site_url", "").rstrip("/")
    context_en["lang_toggle_url"] = f"{_site}/" if _site else "../index.html"
    html_en = render_html(context_en)

    en_index = en_dir / "index.html"
    en_index.write_text(html_en, encoding="utf-8")
    logger.info("Saved English dashboard → %s (%d chars)", en_index, len(html_en))

    en_archive = en_dir / "archive"
    en_archive.mkdir(parents=True, exist_ok=True)
    (en_archive / f"{run_date}.html").write_text(html_en, encoding="utf-8")

    # Regenerate archive index page (Korean)
    save_archive_index(config, output_dir)

    logger.info("Dashboard render complete: %s (ko + en)", index_path)
    return index_path
