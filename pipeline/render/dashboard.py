"""Render Jinja2 dashboard templates to static HTML files.

Public API (called from main.py):
    render_dashboard(config, markets, holidays, articles, insight, run_date, output_dir)
        → returns path to the generated index.html
"""

from __future__ import annotations

import json
import logging
import posixpath
from datetime import datetime
from pathlib import Path, PurePosixPath
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


def _page_path(lang: str, page_kind: str, date_iso: str = "") -> PurePosixPath:
    """Return the public site path for a rendered page."""
    parts: list[str] = ["en"] if lang == "en" else []
    if page_kind == "index":
        parts.append("index.html")
    elif page_kind == "archive_index":
        parts.extend(["archive", "index.html"])
    elif page_kind == "archive":
        parts.extend(["archive", f"{date_iso}.html"])
    else:
        raise ValueError(f"Unsupported page kind: {page_kind}")
    return PurePosixPath(*parts)


def _join_site_url(site_url: str, target_path: PurePosixPath) -> str:
    """Convert a site-relative path to a full public URL."""
    path_str = target_path.as_posix()
    if path_str == "index.html":
        suffix = "/"
    elif path_str.endswith("/index.html"):
        suffix = f"/{path_str[:-10]}/"
    else:
        suffix = f"/{path_str}"
    return f"{site_url.rstrip('/')}{suffix}"


def _build_page_url(
    site_url: str,
    current_path: PurePosixPath,
    target_path: PurePosixPath,
) -> str:
    """Build a public URL, falling back to a relative path for local previews."""
    if site_url:
        return _join_site_url(site_url, target_path)
    start = current_path.parent.as_posix()
    return posixpath.relpath(target_path.as_posix(), start=start)


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


def _split_news(articles: list, config: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Split articles by AI-assigned bucket field."""
    world_news: list[dict] = []
    korea_news: list[dict] = []

    for art in articles:
        if isinstance(art, dict):
            entry = {
                "title": art.get("title", ""),
                "summary": art.get("description", "") or art.get("summary", ""),
                "source": art.get("source", ""),
                "url": art.get("url", ""),
            }
            bucket = art.get("bucket", "")
        else:
            entry = {
                "title": getattr(art, "title", ""),
                "summary": getattr(art, "description", "") or getattr(art, "body", ""),
                "source": getattr(art, "source", ""),
                "url": getattr(art, "url", ""),
            }
            bucket = getattr(art, "bucket", "")

        if bucket == "korea":
            korea_news.append(entry)
        elif bucket == "world":
            world_news.append(entry)

    return world_news, korea_news


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
    page_kind: str = "index",
) -> dict[str, Any]:
    """Assemble all Jinja2 template variables into a single context dict."""

    archive_dir = Path(output_dir) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    prev_date, next_date = _find_adjacent_dates(run_date, archive_dir)
    current_path = _page_path(lang, page_kind, run_date)

    # Normalize market data for template consumption
    normalized_markets: dict[str, list[dict]] = {}
    for key, items in markets.items():
        normalized_markets[key] = _normalize_market_items(items)

    # Split news
    world_news, korea_news = _split_news(articles, config)

    # Site URL (strip trailing slash)
    site_url = config.get("site_url", "").rstrip("/")

    prev_url = (
        _build_page_url(site_url, current_path, _page_path(lang, "archive", prev_date))
        if prev_date else ""
    )
    next_url = (
        _build_page_url(site_url, current_path, _page_path(lang, "archive", next_date))
        if next_date else ""
    )
    archive_index_url = _build_page_url(
        site_url, current_path, _page_path(lang, "archive_index")
    )

    # Language toggle URLs
    other_lang = "ko" if lang == "en" else "en"
    toggle_target_kind = "archive" if page_kind == "archive" else "index"
    lang_toggle_url = _build_page_url(
        site_url,
        current_path,
        _page_path(other_lang, toggle_target_kind, run_date),
    )
    if lang == "en":
        lang_toggle_label = "한국어"
        lang_current = "EN"
    else:
        lang_toggle_label = "English"
        lang_current = "KR"

    return {
        "date_str": _format_date(run_date, lang),
        "date_iso": run_date,
        "prev_date": prev_date,
        "next_date": next_date,
        "prev_url": prev_url,
        "next_url": next_url,
        "insight_text": _md_to_html(insight),
        "markets": normalized_markets,
        "world_news": world_news,
        "korea_news": korea_news,
        "holidays": holidays or {},
        "site_url": site_url,
        "archive_index_url": archive_index_url,
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


def _write_html(path: Path, html: str) -> None:
    """Write an HTML document after validating its size."""
    if len(html) < _MIN_HTML_LENGTH:
        raise ValueError(
            f"Rendered HTML too short ({len(html)} chars < {_MIN_HTML_LENGTH}). "
            "Refusing to save — likely a broken template render."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def save_dashboard(index_html: str, archive_html: str, output_dir: str, date_iso: str) -> str:
    """Save the latest page and archive copy for a single language output.

    Args:
        index_html: Rendered HTML for the latest landing page.
        archive_html: Rendered HTML for the archive detail page.
        output_dir: Base output directory (e.g. "output").
        date_iso: Date string like "2026-03-24".

    Returns:
        Path to the saved index.html.

    Raises:
        ValueError: If HTML is too short (likely broken render).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    archive = out / "archive"
    archive.mkdir(parents=True, exist_ok=True)

    # Save as latest
    index_path = out / "index.html"
    _write_html(index_path, index_html)
    logger.info("Saved dashboard → %s (%d chars)", index_path, len(index_html))

    # Save archive copy
    archive_path = archive / f"{date_iso}.html"
    _write_html(archive_path, archive_html)
    logger.info("Saved archive  → %s", archive_path)

    return str(index_path)


def render_archive_html(dates: list[str], config: dict, lang: str = "ko") -> str:
    """Render the archive listing page for the given language."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("archive.html")
    site_url = config.get("site_url", "").rstrip("/")
    current_path = _page_path(lang, "archive_index")
    date_links = [
        {
            "date": date,
            "url": _build_page_url(site_url, current_path, _page_path(lang, "archive", date)),
        }
        for date in dates
    ]
    other_lang = "ko" if lang == "en" else "en"
    return template.render(
        dates=date_links,
        home_url=_build_page_url(site_url, current_path, _page_path(lang, "index")),
        lang=lang,
        lang_current="EN" if lang == "en" else "KR",
        lang_toggle_label="한국어" if lang == "en" else "English",
        lang_toggle_url=_build_page_url(
            site_url,
            current_path,
            _page_path(other_lang, "archive_index"),
        ),
    )


def save_archive_index(config: dict, output_dir: str, lang: str = "ko") -> str:
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

    html = render_archive_html(dates, config, lang=lang)
    archive_index = archive / "index.html"
    _write_html(archive_index, html)
    logger.info("Saved archive index → %s (%d dates)", archive_index, len(dates))
    return str(archive_index)


def _refresh_archive_pages(config: dict, output_dir: str, lang: str = "ko") -> None:
    """Rewrite archive detail page links so older pages stay navigable."""
    from bs4 import BeautifulSoup

    archive = Path(output_dir) / "archive"
    if not archive.exists():
        return

    pages = sorted(
        (p for p in archive.glob("*.html") if p.stem != "index"),
        key=lambda p: p.stem,
    )
    if not pages:
        return

    site_url = config.get("site_url", "").rstrip("/")
    prev_text = "◀ Previous" if lang == "en" else "◀ 이전"
    next_text = "Next ▶" if lang == "en" else "다음 ▶"
    archive_label = "Browse Archive" if lang == "en" else "과거 브리핑 보기"
    toggle_text = "EN → 한국어" if lang == "en" else "KR → English"

    dates = [p.stem for p in pages]
    for idx, page in enumerate(pages):
        date_iso = page.stem
        prev_date = dates[idx - 1] if idx > 0 else ""
        next_date = dates[idx + 1] if idx < len(dates) - 1 else ""
        current_path = _page_path(lang, "archive", date_iso)

        prev_url = (
            _build_page_url(site_url, current_path, _page_path(lang, "archive", prev_date))
            if prev_date else ""
        )
        next_url = (
            _build_page_url(site_url, current_path, _page_path(lang, "archive", next_date))
            if next_date else ""
        )
        archive_index_url = _build_page_url(
            site_url, current_path, _page_path(lang, "archive_index")
        )
        other_lang = "ko" if lang == "en" else "en"
        lang_toggle_url = _build_page_url(
            site_url,
            current_path,
            _page_path(other_lang, "archive", date_iso),
        )

        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")

        toggle_anchor = soup.select_one(".header-title a")
        if toggle_anchor is not None:
            toggle_anchor["href"] = lang_toggle_url
            toggle_anchor.string = toggle_text

        nav = soup.select_one(".header-nav")
        if nav is not None:
            nav.clear()

            if prev_url:
                prev_el = soup.new_tag("a", href=prev_url)
                prev_label = "Previous brief" if lang == "en" else "이전 브리핑"
                prev_el["aria-label"] = f"{prev_label}: {prev_date}"
                prev_el.string = prev_text
            else:
                prev_el = soup.new_tag("span", attrs={"class": "nav-disabled", "aria-hidden": "true"})
                prev_el.string = prev_text
            nav.append(prev_el)
            nav.append("\n")

            time_el = soup.new_tag("time", datetime=date_iso)
            time_el.string = date_iso
            nav.append(time_el)
            nav.append("\n")

            if next_url:
                next_el = soup.new_tag("a", href=next_url)
                next_label = "Next brief" if lang == "en" else "다음 브리핑"
                next_el["aria-label"] = f"{next_label}: {next_date}"
                next_el.string = next_text
            else:
                next_el = soup.new_tag("span", attrs={"class": "nav-disabled", "aria-hidden": "true"})
                next_el.string = next_text
            nav.append(next_el)

        footer_link = soup.select_one(".footer-actions a")
        if footer_link is not None:
            footer_link["href"] = archive_index_url
            footer_link.string = archive_label

        page.write_text(str(soup), encoding="utf-8")


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
    context_ko_index = _build_template_context(
        config, markets, holidays, ko_articles, insight, run_date, output_dir, lang="ko", page_kind="index",
    )
    context_ko_archive = _build_template_context(
        config, markets, holidays, ko_articles, insight, run_date, output_dir, lang="ko", page_kind="archive",
    )
    context_ko_index["market_pulse"] = pulse
    context_ko_archive["market_pulse"] = pulse
    html_ko_index = render_html(context_ko_index)
    html_ko_archive = render_html(context_ko_archive)
    index_path = save_dashboard(html_ko_index, html_ko_archive, output_dir, run_date)

    # --- English version ---
    en_dir = Path(output_dir) / "en"
    en_dir.mkdir(parents=True, exist_ok=True)
    en_insight = insight_en if insight_en else insight
    context_en_index = _build_template_context(
        config, markets, holidays, en_articles, en_insight, run_date, str(en_dir), lang="en", page_kind="index",
    )
    context_en_archive = _build_template_context(
        config, markets, holidays, en_articles, en_insight, run_date, str(en_dir), lang="en", page_kind="archive",
    )
    context_en_index["market_pulse"] = pulse
    context_en_archive["market_pulse"] = pulse
    html_en_index = render_html(context_en_index)
    html_en_archive = render_html(context_en_archive)
    save_dashboard(html_en_index, html_en_archive, str(en_dir), run_date)

    # Refresh archive page links for all existing dates in both languages
    _refresh_archive_pages(config, output_dir, lang="ko")
    _refresh_archive_pages(config, str(en_dir), lang="en")

    # Regenerate archive index pages (Korean + English)
    save_archive_index(config, output_dir, lang="ko")
    save_archive_index(config, str(en_dir), lang="en")

    logger.info("Dashboard render complete: %s (ko + en)", index_path)
    return index_path
