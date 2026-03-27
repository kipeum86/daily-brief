"""Render weekly recap templates to static HTML."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from pipeline.render.dashboard import _build_page_url, _md_to_html, _write_html

import logging

logger = logging.getLogger("daily-brief.render.weekly")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATES_DIR = _PROJECT_ROOT / "templates" / "dashboard"

_KO_WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
_EN_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_EN_MONTHS = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _page_path(lang: str, page_kind: str, week_id: str = "") -> PurePosixPath:
    parts: list[str] = ["en"] if lang == "en" else []
    parts.append("weekly")
    if page_kind == "index":
        parts.append("index.html")
    elif page_kind == "archive_index":
        parts.extend(["archive", "index.html"])
    elif page_kind == "archive":
        parts.extend(["archive", f"{week_id}.html"])
    else:
        raise ValueError(f"Unsupported page kind: {page_kind}")
    return PurePosixPath(*parts)


def _find_adjacent_weeks(week_id: str, archive_dir: Path) -> tuple[str, str]:
    weeks = sorted(p.stem for p in archive_dir.glob("*.html") if p.stem != "index")
    prev_week = ""
    next_week = ""
    for existing in reversed(weeks):
        if existing < week_id:
            prev_week = existing
            break
    for existing in weeks:
        if existing > week_id:
            next_week = existing
            break
    return prev_week, next_week


def _format_week_label(start_date: str, end_date: str, lang: str = "ko") -> str:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if lang == "en":
        if start.year == end.year and start.month == end.month:
            return f"{_EN_MONTHS[start.month]} {start.day}-{end.day}, {end.year}"
        if start.year == end.year:
            return f"{_EN_MONTHS[start.month]} {start.day} - {_EN_MONTHS[end.month]} {end.day}, {end.year}"
        return f"{_EN_MONTHS[start.month]} {start.day}, {start.year} - {_EN_MONTHS[end.month]} {end.day}, {end.year}"
    start_weekday = _KO_WEEKDAYS[start.weekday()]
    end_weekday = _KO_WEEKDAYS[end.weekday()]
    return (
        f"{start.year}년 {start.month}월 {start.day}일 {start_weekday} - "
        f"{end.month}월 {end.day}일 {end_weekday}"
    )


def _format_market_card(card: dict[str, Any]) -> dict[str, Any]:
    entry = dict(card)
    change_pct = float(entry.get("weekly_change_pct", 0.0))
    entry["start_fmt"] = f"{float(entry.get('start_price', 0.0)):,.2f}"
    entry["end_fmt"] = f"{float(entry.get('end_price', 0.0)):,.2f}"
    entry["change_fmt"] = f"{change_pct:+.2f}%"
    if change_pct > 0:
        entry["direction"] = "up"
    elif change_pct < 0:
        entry["direction"] = "down"
    else:
        entry["direction"] = "flat"
    return entry


def _label_from_week_id(week_id: str, lang: str = "ko") -> str:
    try:
        year_str, week_str = week_id.split("-W", 1)
        start = date.fromisocalendar(int(year_str), int(week_str), 1)
        end = date.fromisocalendar(int(year_str), int(week_str), 5)
        return _format_week_label(start.isoformat(), end.isoformat(), lang=lang)
    except Exception:
        return week_id


def _build_context(
    config: dict,
    weekly_data: dict[str, Any],
    output_dir: str,
    lang: str = "ko",
    page_kind: str = "index",
) -> dict[str, Any]:
    archive_dir = Path(output_dir) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    week_id = weekly_data.get("week_id", "")
    prev_week, next_week = _find_adjacent_weeks(week_id, archive_dir)
    current_path = _page_path(lang, page_kind, week_id)
    site_url = config.get("site_url", "").rstrip("/")
    other_lang = "ko" if lang == "en" else "en"

    context = {
        "date_str": _format_week_label(
            weekly_data.get("start_date", ""),
            weekly_data.get("end_date", ""),
            lang=lang,
        ),
        "start_date": weekly_data.get("start_date", ""),
        "end_date": weekly_data.get("end_date", ""),
        "week_id": week_id,
        "snapshot_count": weekly_data.get("snapshot_count", 0),
        "unique_story_count": weekly_data.get("unique_story_count", 0),
        "prev_url": _build_page_url(site_url, current_path, _page_path(lang, "archive", prev_week)) if prev_week else "",
        "next_url": _build_page_url(site_url, current_path, _page_path(lang, "archive", next_week)) if next_week else "",
        "archive_index_url": _build_page_url(site_url, current_path, _page_path(lang, "archive_index")),
        "lang": lang,
        "lang_current": "EN" if lang == "en" else "KR",
        "lang_toggle_label": "한국어" if lang == "en" else "English",
        "lang_toggle_url": _build_page_url(
            site_url,
            current_path,
            _page_path(other_lang, "archive" if page_kind == "archive" else "index", week_id),
        ),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_cards": [_format_market_card(card) for card in weekly_data.get("markets", {}).get("cards", [])],
        "leaders": [_format_market_card(card) for card in weekly_data.get("markets", {}).get("leaders", [])],
        "laggards": [_format_market_card(card) for card in weekly_data.get("markets", {}).get("laggards", [])],
        "world_news": weekly_data.get("world_news_en" if lang == "en" else "world_news_ko", []),
        "korea_news": weekly_data.get("korea_news_en" if lang == "en" else "korea_news_ko", []),
        "insight_text": _md_to_html(weekly_data.get("insight_en" if lang == "en" else "insight_ko", "")),
    }
    return context


def render_weekly_html(context: dict[str, Any]) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("weekly.html")
    if context.get("insight_text"):
        context["insight_text"] = Markup(context["insight_text"])
    return template.render(**context)


def save_weekly_dashboard(index_html: str, archive_html: str, output_dir: str, week_id: str) -> str:
    root = Path(output_dir)
    archive_dir = root / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    index_path = root / "index.html"
    archive_path = archive_dir / f"{week_id}.html"
    _write_html(index_path, index_html)
    _write_html(archive_path, archive_html)
    logger.info("Saved weekly recap → %s", index_path)
    logger.info("Saved weekly archive → %s", archive_path)
    return str(index_path)


def render_weekly_archive_html(weeks: list[str], labels: dict[str, str], config: dict, lang: str = "ko") -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("weekly_archive.html")
    current_path = _page_path(lang, "archive_index")
    site_url = config.get("site_url", "").rstrip("/")
    other_lang = "ko" if lang == "en" else "en"
    items = [
        {
            "week_id": week_id,
            "label": labels.get(week_id, _label_from_week_id(week_id, lang=lang)),
            "url": _build_page_url(site_url, current_path, _page_path(lang, "archive", week_id)),
        }
        for week_id in weeks
    ]
    return template.render(
        weeks=items,
        lang=lang,
        home_url=_build_page_url(site_url, current_path, _page_path(lang, "index")),
        lang_current="EN" if lang == "en" else "KR",
        lang_toggle_label="한국어" if lang == "en" else "English",
        lang_toggle_url=_build_page_url(site_url, current_path, _page_path(other_lang, "archive_index")),
    )


def _refresh_archive_pages(config: dict, output_dir: str, lang: str = "ko") -> None:
    archive_dir = Path(output_dir) / "archive"
    if not archive_dir.exists():
        return
    weeks = sorted((p for p in archive_dir.glob("*.html") if p.stem != "index"), key=lambda p: p.stem)
    if not weeks:
        return

    site_url = config.get("site_url", "").rstrip("/")
    prev_text = "◀ Previous Week" if lang == "en" else "◀ 이전 주"
    next_text = "Next Week ▶" if lang == "en" else "다음 주 ▶"
    archive_label = "Browse Weekly Archive" if lang == "en" else "주간 아카이브 보기"
    toggle_text = "EN → 한국어" if lang == "en" else "KR → English"
    ids = [page.stem for page in weeks]

    for index, page in enumerate(weeks):
        week_id = page.stem
        prev_id = ids[index - 1] if index > 0 else ""
        next_id = ids[index + 1] if index < len(ids) - 1 else ""
        current_path = _page_path(lang, "archive", week_id)

        prev_url = _build_page_url(site_url, current_path, _page_path(lang, "archive", prev_id)) if prev_id else ""
        next_url = _build_page_url(site_url, current_path, _page_path(lang, "archive", next_id)) if next_id else ""
        archive_index_url = _build_page_url(site_url, current_path, _page_path(lang, "archive_index"))
        other_lang = "ko" if lang == "en" else "en"
        toggle_url = _build_page_url(site_url, current_path, _page_path(other_lang, "archive", week_id))

        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        toggle = soup.select_one(".lang-toggle")
        if toggle is not None:
            toggle["href"] = toggle_url
            toggle.string = toggle_text

        nav = soup.select_one(".hero-nav")
        if nav is not None:
            nav.clear()
            if prev_url:
                prev_el = soup.new_tag("a", href=prev_url)
                prev_el.string = prev_text
            else:
                prev_el = soup.new_tag("span", attrs={"class": "nav-disabled"})
                prev_el.string = prev_text
            nav.append(prev_el)
            nav.append("\n")
            mid = soup.new_tag("span")
            mid.string = week_id
            nav.append(mid)
            nav.append("\n")
            if next_url:
                next_el = soup.new_tag("a", href=next_url)
                next_el.string = next_text
            else:
                next_el = soup.new_tag("span", attrs={"class": "nav-disabled"})
                next_el.string = next_text
            nav.append(next_el)

        footer_link = soup.select_one(".footer-link")
        if footer_link is not None:
            footer_link["href"] = archive_index_url
            footer_link.string = archive_label

        page.write_text(str(soup), encoding="utf-8")


def save_weekly_archive_index(config: dict, output_dir: str, weekly_labels: dict[str, str], lang: str = "ko") -> str:
    archive_dir = Path(output_dir) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    weeks = sorted((p.stem for p in archive_dir.glob("*.html") if p.stem != "index"), reverse=True)
    html = render_weekly_archive_html(weeks, weekly_labels, config, lang=lang)
    archive_index = archive_dir / "index.html"
    _write_html(archive_index, html)
    logger.info("Saved weekly archive index → %s", archive_index)
    return str(archive_index)


def render_weekly_recap(config: dict, weekly_data: dict[str, Any], output_dir: str) -> str:
    """Render Korean and English weekly recap pages."""
    ko_labels = dict(weekly_data.get("archive_labels_ko", {}))
    en_labels = dict(weekly_data.get("archive_labels_en", {}))
    ko_labels[weekly_data.get("week_id", "")] = _format_week_label(
        weekly_data.get("start_date", ""),
        weekly_data.get("end_date", ""),
        lang="ko",
    )
    en_labels[weekly_data.get("week_id", "")] = _format_week_label(
        weekly_data.get("start_date", ""),
        weekly_data.get("end_date", ""),
        lang="en",
    )

    ko_root = Path(output_dir) / "weekly"
    ko_root.mkdir(parents=True, exist_ok=True)
    ko_index_context = _build_context(config, weekly_data, str(ko_root), lang="ko", page_kind="index")
    ko_archive_context = _build_context(config, weekly_data, str(ko_root), lang="ko", page_kind="archive")
    ko_index = render_weekly_html(ko_index_context)
    ko_archive = render_weekly_html(ko_archive_context)
    index_path = save_weekly_dashboard(ko_index, ko_archive, str(ko_root), weekly_data.get("week_id", ""))

    en_root = Path(output_dir) / "en" / "weekly"
    en_root.mkdir(parents=True, exist_ok=True)
    en_index_context = _build_context(config, weekly_data, str(en_root), lang="en", page_kind="index")
    en_archive_context = _build_context(config, weekly_data, str(en_root), lang="en", page_kind="archive")
    en_index = render_weekly_html(en_index_context)
    en_archive = render_weekly_html(en_archive_context)
    save_weekly_dashboard(en_index, en_archive, str(en_root), weekly_data.get("week_id", ""))

    _refresh_archive_pages(config, str(ko_root), lang="ko")
    _refresh_archive_pages(config, str(en_root), lang="en")
    save_weekly_archive_index(config, str(ko_root), ko_labels, lang="ko")
    save_weekly_archive_index(config, str(en_root), en_labels, lang="en")
    return index_path
