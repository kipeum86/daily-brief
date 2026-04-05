"""Snapshot persistence and weekly recap aggregation helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from pipeline.markets.indicators import generate_sparkline_svg
from pipeline.news.dedup import canonicalize_url
from pipeline.news.selector import select_top_news

logger = logging.getLogger("daily-brief.recap")

_SCHEMA_VERSION = 1
_CORE_MARKETS: list[tuple[str, str]] = [
    ("kr", "KOSPI"),
    ("kr", "KOSDAQ"),
    ("us", "S&P 500"),
    ("us", "Nasdaq"),
    ("fx", "USD/KRW"),
    ("commodities", "Gold"),
    ("commodities", "WTI Oil"),
    ("crypto", "Bitcoin"),
    ("risk", "VIX"),
]
_SECTION_LABELS = {
    "kr": "Korea",
    "us": "US",
    "fx": "FX",
    "commodities": "Commodities",
    "crypto": "Crypto",
    "risk": "Risk",
    "sectors": "Sectors",
}

_PRICE_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)%")


def _canonical_story_key(url: str, source: str, title: str) -> str:
    """Generate a stable story key."""
    if url:
        try:
            return canonicalize_url(url)
        except Exception:
            pass
    raw = f"{source}|{title}".encode("utf-8")
    return "story:" + hashlib.sha1(raw).hexdigest()


def _serialize_market_item(item: Any) -> dict[str, Any]:
    """Convert market data entry to a JSON-serializable dict."""
    if isinstance(item, dict):
        data = dict(item)
    else:
        data = {
            "ticker": getattr(item, "ticker", ""),
            "name": getattr(item, "name", ""),
            "price": getattr(item, "price", 0.0),
            "change_pct": getattr(item, "change_pct", 0.0),
            "prev_close": getattr(item, "prev_close", 0.0),
            "sparkline": getattr(item, "sparkline", []),
            "volume": getattr(item, "volume", 0),
        }

    # SVG is large and can be regenerated from week-level prices later.
    data.pop("sparkline_svg", None)
    return data


def _serialize_article(article: Any, config: dict) -> dict[str, Any]:
    """Convert an article object to a JSON-serializable dict."""
    if isinstance(article, dict):
        title = article.get("title", "")
        summary = article.get("summary", "") or article.get("description", "")
        source = article.get("source", "")
        url = article.get("url", "")
        published_date = article.get("published_date", "") or article.get("published", "")
    else:
        title = getattr(article, "title", "")
        summary = getattr(article, "description", "") or getattr(article, "body", "")
        source = getattr(article, "source", "")
        url = getattr(article, "url", "")
        published_date = getattr(article, "published_date", "")

    story_key = _canonical_story_key(url, source, title)
    return {
        "story_key": story_key,
        "canonical_url": story_key if story_key.startswith("https://") else "",
        "title": title,
        "summary": summary,
        "source": source,
        "url": url,
        "published_date": published_date,
        "bucket": (
            article.get("bucket", "")
            if isinstance(article, dict)
            else getattr(article, "bucket", "")
        ),
    }


def _serialize_pool_article(article: Any, config: dict) -> dict[str, Any]:
    """Serialize a pool article, inferring bucket if missing."""
    data = _serialize_article(article, config)
    if not data.get("bucket"):
        # Pool articles may lack bucket (saved before Stage 6.5 classification).
        # Infer using the same heuristic as the selector.
        try:
            from pipeline.news.selector import _guess_bucket
            data["bucket"] = _guess_bucket(data)
        except ImportError:
            # Fallback: simple heuristic
            text = f"{data.get('title', '')} {data.get('source', '')}".lower()
            kr_signals = any(kw in text for kw in ("한국", "국내", "코스피", "서울", "정부"))
            data["bucket"] = "korea" if kr_signals else "world"
    return data


def _safe_parse_float(text: str) -> float:
    """Extract the first numeric value from a text blob."""
    match = _PRICE_RE.search((text or "").replace("\xa0", " "))
    if not match:
        return 0.0
    return float(match.group(0).replace(",", ""))


def _safe_parse_pct(text: str) -> float:
    """Extract percentage text like ▲+2.34% or -0.45%."""
    clean = (text or "").replace("\xa0", " ").strip()
    match = _PCT_RE.search(clean)
    if match:
        return float(match.group(1))
    if "%" in clean:
        return _safe_parse_float(clean)
    return 0.0


def _extract_text_block(node: Any) -> str:
    """Normalize HTML content into a plain-text block."""
    if node is None:
        return ""
    parts = [" ".join(chunk.split()) for chunk in node.stripped_strings]
    return "\n\n".join(part for part in parts if part)


def _market_name_lookup(config: dict) -> dict[str, dict[str, str]]:
    """Build name → section/ticker lookup from config.markets."""
    lookup: dict[str, dict[str, str]] = {}
    for section, section_config in config.get("markets", {}).items():
        if not isinstance(section_config, dict):
            continue
        names = list(section_config.get("names", []))
        tickers = (
            list(section_config.get("indices", []))
            or list(section_config.get("pairs", []))
            or list(section_config.get("tickers", []))
            or list(section_config.get("fred_series", []))
        )
        for index, name in enumerate(names):
            lookup[name] = {
                "section": section,
                "ticker": tickers[index] if index < len(tickers) else "",
            }
    return lookup


def _empty_markets_payload(config: dict) -> dict[str, list[dict[str, Any]]]:
    """Prepare snapshot market sections in config order."""
    return {
        section: []
        for section in config.get("markets", {}).keys()
        if isinstance(config.get("markets", {}).get(section), dict)
    }


def _parse_market_items_from_archive(
    soup: BeautifulSoup,
    config: dict,
) -> dict[str, list[dict[str, Any]]]:
    """Recover market items from a rendered daily archive page."""
    section_lookup = _market_name_lookup(config)
    markets_payload = _empty_markets_payload(config)

    markets_section = soup.select_one("section.markets-section")
    if markets_section is None:
        return markets_payload

    for row in markets_section.select("div[style*='overflow-x:auto']"):
        for item_node in row.find_all("span", recursive=False):
            parts = item_node.find_all("span", recursive=False)
            if len(parts) < 3:
                continue
            name = parts[0].get_text(" ", strip=True)
            if not name:
                continue
            lookup = section_lookup.get(name, {"section": "other", "ticker": ""})
            entry = {
                "ticker": lookup.get("ticker", ""),
                "name": name,
                "price": _safe_parse_float(parts[1].get_text(" ", strip=True)),
                "change_pct": _safe_parse_pct(parts[2].get_text(" ", strip=True)),
                "prev_close": 0.0,
                "sparkline": [],
                "volume": 0,
            }
            markets_payload.setdefault(lookup.get("section", "other"), []).append(entry)

    return markets_payload


def _parse_news_sections_from_archive(soup: BeautifulSoup) -> dict[str, list[dict[str, Any]]]:
    """Recover world/korea news entries from a rendered daily archive page."""
    buckets = {"world": [], "korea": []}
    for section in soup.select("section"):
        title_node = section.select_one("h2.section-title")
        if title_node is None:
            continue
        title = title_node.get_text(" ", strip=True).lower()
        if title not in {"world", "korea"}:
            continue

        for item in section.select("li.news-item article"):
            headline_node = item.select_one("h3.news-headline")
            if headline_node is None:
                continue
            link = headline_node.find("a")
            summary_node = item.select_one("p.news-summary")
            source_node = item.select_one("p.news-source")
            buckets[title].append({
                "title": (link or headline_node).get_text(" ", strip=True),
                "url": link.get("href", "").strip() if link else "",
                "summary": summary_node.get_text(" ", strip=True) if summary_node else "",
                "source": source_node.get_text(" ", strip=True) if source_node else "",
                "published_date": "",
                "bucket": title,
            })

    return buckets


def _parse_archive_page(path: Path, config: dict) -> dict[str, Any]:
    """Parse one rendered daily archive page into snapshot-friendly pieces."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    date_node = soup.select_one("header.site-header time[datetime]")
    generated_node = soup.select_one("footer time[datetime]")
    insight_node = soup.select_one(".insight-body")
    pulse_label_nodes = soup.select("section.markets-section h2.section-title span")
    signal_node = soup.select_one("section.markets-section > div[style*='font-family:var(--font-mono)']")
    holiday_nodes = soup.select("p.market-holiday-notice")

    holidays = {
        "kospi_holiday": any("KOSPI" in node.get_text(" ", strip=True) for node in holiday_nodes),
        "nyse_holiday": any("NYSE" in node.get_text(" ", strip=True) for node in holiday_nodes),
    }
    market_pulse = {}
    if len(pulse_label_nodes) > 1:
        market_pulse["label_ko"] = pulse_label_nodes[-1].get_text(" ", strip=True)
    if signal_node is not None:
        signals_text = signal_node.get_text(" ", strip=True)
        if signals_text:
            market_pulse["signals"] = [part.strip() for part in signals_text.split("·") if part.strip()]

    return {
        "date": date_node.get("datetime", "").strip() if date_node else path.stem,
        "generated_at": generated_node.get("datetime", "").strip() if generated_node else "",
        "insight_text": _extract_text_block(insight_node),
        "markets": _parse_market_items_from_archive(soup, config),
        "news": _parse_news_sections_from_archive(soup),
        "holidays": holidays,
        "market_pulse": market_pulse,
    }


def _build_snapshot_payload_from_archives(
    config: dict,
    date_iso: str,
    ko_page: Path,
    en_page: Path | None = None,
) -> dict[str, Any]:
    """Reconstruct a daily snapshot from saved KO/EN archive pages."""
    ko_data = _parse_archive_page(ko_page, config)
    en_data = _parse_archive_page(en_page, config) if en_page and en_page.exists() else None

    raw_articles = ko_data["news"]["world"] + ko_data["news"]["korea"]
    ko_articles = list(raw_articles)
    en_articles = (
        en_data["news"]["world"] + en_data["news"]["korea"]
        if en_data is not None
        else list(raw_articles)
    )

    return {
        "schema_version": _SCHEMA_VERSION,
        "brief_type": "daily",
        "date": date_iso,
        "generated_at": ko_data.get("generated_at") or datetime.now().isoformat(timespec="seconds"),
        "market_pulse": ko_data.get("market_pulse", {}),
        "holidays": ko_data.get("holidays", {}),
        "markets": ko_data.get("markets", {}),
        "insight": {
            "ko": ko_data.get("insight_text", ""),
            "en": en_data.get("insight_text", "") if en_data is not None else "",
        },
        "articles": {
            "raw": [_serialize_article(article, config) for article in raw_articles],
            "ko": [_serialize_article(article, config) for article in ko_articles],
            "en": [_serialize_article(article, config) for article in en_articles],
        },
    }


def _refresh_latest_snapshot(snapshot_dir: Path) -> None:
    """Keep latest.json in sync with the newest dated snapshot file."""
    dated_paths = sorted(
        (
            path for path in snapshot_dir.glob("*.json")
            if path.name != "latest.json"
        ),
        key=lambda item: item.stem,
    )
    if not dated_paths:
        return
    latest_payload = dated_paths[-1].read_text(encoding="utf-8")
    (snapshot_dir / "latest.json").write_text(latest_payload, encoding="utf-8")


def backfill_daily_snapshots_from_archives(
    config: dict,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
    overwrite: bool = False,
) -> list[str]:
    """Backfill missing daily snapshot JSON files from saved archive HTML."""
    ko_archive_dir = Path(output_dir) / "archive"
    en_archive_dir = Path(output_dir) / "en" / "archive"
    snapshot_dir = Path(output_dir) / "data" / "daily"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    created: list[str] = []

    for ko_page in sorted(ko_archive_dir.glob("*.html")):
        if ko_page.stem == "index":
            continue
        try:
            page_date = date.fromisoformat(ko_page.stem)
        except ValueError:
            continue
        if start and page_date < start:
            continue
        if end and page_date > end:
            continue

        snapshot_path = snapshot_dir / f"{ko_page.stem}.json"
        if snapshot_path.exists() and not overwrite:
            continue

        payload = _build_snapshot_payload_from_archives(
            config,
            ko_page.stem,
            ko_page,
            en_archive_dir / f"{ko_page.stem}.html",
        )
        snapshot_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        created.append(str(snapshot_path))

    if created or not (snapshot_dir / "latest.json").exists():
        _refresh_latest_snapshot(snapshot_dir)

    if created:
        logger.info(
            "Backfilled %d daily snapshot(s) from archive HTML",
            len(created),
        )
    return created


def save_daily_snapshot(
    config: dict,
    markets: dict[str, list],
    holidays: dict[str, Any],
    articles: list,
    articles_ko: list,
    articles_en: list,
    insight_ko: str,
    insight_en: str,
    run_date: str,
    output_dir: str,
    market_pulse: dict | None = None,
    all_candidates: list | None = None,
) -> str:
    """Persist a daily briefing snapshot for future recap jobs."""
    snapshot_dir = Path(output_dir) / "data" / "daily"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    markets_payload = {
        key: [_serialize_market_item(item) for item in items]
        for key, items in markets.items()
    }
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "brief_type": "daily",
        "date": run_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "market_pulse": market_pulse or {},
        "holidays": holidays or {},
        "markets": markets_payload,
        "insight": {"ko": insight_ko or "", "en": insight_en or ""},
        "articles": {
            "raw": [_serialize_article(article, config) for article in articles],
            "ko": [_serialize_article(article, config) for article in articles_ko],
            "en": [_serialize_article(article, config) for article in articles_en],
            "pool": [_serialize_pool_article(article, config) for article in (all_candidates or [])],
        },
    }

    snapshot_path = snapshot_dir / f"{run_date}.json"
    snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    latest_path = snapshot_dir / "latest.json"
    latest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Saved daily snapshot → %s", snapshot_path)
    return str(snapshot_path)


def get_week_window(run_date: str) -> dict[str, str]:
    """Get the weekly recap window.

    Scheduled weekly runs happen on Saturday morning KST, so Saturday should
    summarize the market/news window ending on Friday. Manual weekday runs are
    treated as week-to-date previews and include the same calendar day.
    """
    run_day = date.fromisoformat(run_date)
    recap_end = run_day - timedelta(days=1) if run_day.weekday() == 5 else run_day
    recap_start = recap_end - timedelta(days=recap_end.weekday())
    iso_year, iso_week, _ = recap_end.isocalendar()
    return {
        "week_id": f"{iso_year}-W{iso_week:02d}",
        "start_date": recap_start.isoformat(),
        "end_date": recap_end.isoformat(),
    }


def load_daily_snapshots(output_dir: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Load daily snapshots for the given inclusive date range."""
    snapshot_dir = Path(output_dir) / "data" / "daily"
    if not snapshot_dir.exists():
        return []

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    snapshots: list[dict[str, Any]] = []
    for path in sorted(snapshot_dir.glob("*.json")):
        if path.name == "latest.json":
            continue
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if not (start <= day <= end):
            continue
        try:
            snapshots.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse snapshot %s: %s", path.name, exc)

    snapshots.sort(key=lambda item: item.get("date", ""))
    logger.info(
        "Loaded %d daily snapshot(s) for %s → %s",
        len(snapshots), start_date, end_date,
    )
    return snapshots


def _build_series_map_from_snapshots(snapshots: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Build a series map keyed by (section, name) from saved snapshots."""
    series_map: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in snapshots:
        date_iso = snapshot.get("date", "")
        for section, items in snapshot.get("markets", {}).items():
            for item in items:
                key = (section, item.get("name", ""))
                entry = series_map.setdefault(
                    key,
                    {
                        "section": section,
                        "name": item.get("name", ""),
                        "ticker": item.get("ticker", ""),
                        "points": [],
                    },
                )
                entry["points"].append({
                    "date": date_iso,
                    "price": float(item.get("price", 0.0)),
                    "change_pct": float(item.get("change_pct", 0.0)),
                })
    return series_map


def _build_series_map_from_market_window(
    config: dict[str, Any],
    start_date: str,
    end_date: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Build a weekly series map from API-fetched market history."""
    try:
        from pipeline.markets.collector import collect_market_window_data

        window_data = collect_market_window_data(config, start_date, end_date)
    except Exception as exc:
        logger.warning("Weekly market fallback fetch failed: %s", exc)
        return {}

    series_map: dict[tuple[str, str], dict[str, Any]] = {}
    for section, items in window_data.items():
        for item in items:
            fetched_points = list(item.get("points", []))
            if not fetched_points:
                continue
            key = (section, item.get("name", ""))
            series_map[key] = {
                "section": section,
                "name": item.get("name", ""),
                "ticker": item.get("ticker", ""),
                "points": fetched_points,
            }

    return series_map


def _build_weekly_market_cards(
    series_map: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert a series map into display-ready weekly market cards."""
    all_cards: list[dict[str, Any]] = []

    for entry in series_map.values():
        points = sorted(entry["points"], key=lambda item: item["date"])
        if not points:
            continue
        start_price = points[0]["price"]
        end_price = points[-1]["price"]
        weekly_change_pct = ((end_price - start_price) / start_price * 100) if start_price else 0.0
        sparkline = [point["price"] for point in points]
        all_cards.append({
            "section": entry["section"],
            "section_label": _SECTION_LABELS.get(entry["section"], entry["section"].title()),
            "name": entry["name"],
            "ticker": entry["ticker"],
            "points": points,
            "observations": len(points),
            "start_price": start_price,
            "end_price": end_price,
            "weekly_change_pct": round(weekly_change_pct, 2),
            "sparkline_svg": generate_sparkline_svg(sparkline, width=72, height=22),
        })
    return all_cards


def build_weekly_market_summary(
    snapshots: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Aggregate week-level market moves from daily snapshots."""
    snapshot_series_map = _build_series_map_from_snapshots(snapshots)
    series_map = dict(snapshot_series_map)
    if config and start_date and end_date:
        fetched_series_map = _build_series_map_from_market_window(config, start_date, end_date)
        if fetched_series_map:
            series_map.update(fetched_series_map)

    all_cards = _build_weekly_market_cards(series_map)

    core_lookup = {(item["section"], item["name"]): item for item in all_cards}
    core_cards = [core_lookup[key] for key in _CORE_MARKETS if key in core_lookup]

    non_sector_cards = [item for item in all_cards if item["section"] != "sectors"]
    leaders = sorted(non_sector_cards, key=lambda item: item["weekly_change_pct"], reverse=True)[:5]
    laggards = sorted(non_sector_cards, key=lambda item: item["weekly_change_pct"])[:5]
    sector_cards = [item for item in all_cards if item["section"] == "sectors"]
    sectors_best = sorted(sector_cards, key=lambda item: item["weekly_change_pct"], reverse=True)[:3]
    sectors_worst = sorted(sector_cards, key=lambda item: item["weekly_change_pct"])[:3]

    return {
        "cards": core_cards,
        "leaders": leaders,
        "laggards": laggards,
        "sectors_best": sectors_best,
        "sectors_worst": sectors_worst,
        "all_cards": all_cards,
    }


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, str]:
    return (candidate["score"], candidate["count"], candidate["latest_date"])


def _decorate_display(article: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    entry = dict(article)
    entry["appearances"] = candidate["count"]
    entry["latest_date"] = candidate["latest_date"]
    entry["dates"] = list(candidate["dates"])
    return entry


def build_weekly_news_digest(
    config: dict,
    snapshots: list[dict[str, Any]],
    provider: Any | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """Select weekly top stories from saved daily snapshots.

    Uses two article layers from each snapshot:
    - ``pool`` (full dedup'd candidates, 50-100/day): drives coverage count
    - ``raw`` (AI-selected top 10/day): provides display headlines & translations
    When ``pool`` is absent (older snapshots), falls back to ``raw`` for counting.
    """
    candidates: dict[str, dict[str, Any]] = {}
    for day_index, snapshot in enumerate(snapshots):
        date_iso = snapshot.get("date", "")
        articles_section = snapshot.get("articles", {})

        # Pool = full dedup'd candidates for coverage counting; fall back to raw
        pool_articles = articles_section.get("pool", []) or articles_section.get("raw", [])
        raw_articles = articles_section.get("raw", [])

        raw_by_key = {
            article.get("story_key") or _canonical_story_key(
                article.get("url", ""), article.get("source", ""), article.get("title", ""),
            ): article
            for article in raw_articles
        }
        ko_by_key = {
            article.get("story_key") or _canonical_story_key(
                article.get("url", ""), article.get("source", ""), article.get("title", ""),
            ): article
            for article in articles_section.get("ko", [])
        }
        en_by_key = {
            article.get("story_key") or _canonical_story_key(
                article.get("url", ""), article.get("source", ""), article.get("title", ""),
            ): article
            for article in articles_section.get("en", [])
        }

        # Count coverage from the full pool (many outlets covering same story)
        day_weight = 10 + day_index
        for pool_article in pool_articles:
            story_key = pool_article.get("story_key") or _canonical_story_key(
                pool_article.get("url", ""), pool_article.get("source", ""), pool_article.get("title", ""),
            )
            bucket = pool_article.get("bucket", "")
            if not bucket:
                # Legacy snapshots may lack bucket — infer at read time
                try:
                    from pipeline.news.selector import _guess_bucket
                    bucket = _guess_bucket(pool_article)
                except ImportError:
                    text = f"{pool_article.get('title', '')} {pool_article.get('source', '')}".lower()
                    bucket = "korea" if any(kw in text for kw in ("한국", "국내", "코스피", "서울", "정부")) else "world"
            # Use raw version for display if available, otherwise pool version
            display_article = raw_by_key.get(story_key, pool_article)
            entry = candidates.setdefault(
                story_key,
                {
                    "story_key": story_key,
                    "url": display_article.get("url", ""),
                    "source": display_article.get("source", ""),
                    "title": display_article.get("title", ""),
                    "description": display_article.get("summary", ""),
                    "bucket": bucket,
                    "dates": set(),
                    "count": 0,
                    "score": 0,
                    "latest_date": "",
                    "display_ko": display_article,
                    "display_en": display_article,
                },
            )
            entry["count"] += 1
            entry["score"] += day_weight
            entry["dates"].add(date_iso)
            if date_iso >= entry["latest_date"]:
                entry["latest_date"] = date_iso
                entry["title"] = display_article.get("title", "")
                entry["description"] = display_article.get("summary", "")
                entry["source"] = display_article.get("source", "")
                entry["url"] = display_article.get("url", "")
                entry["display_ko"] = ko_by_key.get(story_key, display_article)
                entry["display_en"] = en_by_key.get(story_key, display_article)

    ranked_world = sorted(
        (entry for entry in candidates.values() if entry["bucket"] == "world"),
        key=_candidate_sort_key,
        reverse=True,
    )
    ranked_korea = sorted(
        (entry for entry in candidates.values() if entry["bucket"] == "korea"),
        key=_candidate_sort_key,
        reverse=True,
    )

    if provider:
        selected_world = select_top_news(provider, ranked_world, top_n=top_n, category="world")
        selected_korea = select_top_news(provider, ranked_korea, top_n=top_n, category="korea")
    else:
        selected_world = ranked_world[:top_n]
        selected_korea = ranked_korea[:top_n]

    world_ko = [_decorate_display(item["display_ko"], item) for item in selected_world]
    world_en = [_decorate_display(item["display_en"], item) for item in selected_world]
    korea_ko = [_decorate_display(item["display_ko"], item) for item in selected_korea]
    korea_en = [_decorate_display(item["display_en"], item) for item in selected_korea]

    return {
        "world_raw": selected_world,
        "korea_raw": selected_korea,
        "world_ko": world_ko,
        "world_en": world_en,
        "korea_ko": korea_ko,
        "korea_en": korea_en,
        "unique_story_count": len(candidates),
    }
