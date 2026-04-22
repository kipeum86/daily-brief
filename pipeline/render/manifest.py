"""Generate /manifest.json for the briefing-hub aggregator.

Schema follows DESIGN.md §4 of the briefing-hub repo:
    { name, category, accent, description, url, updated_at, latest, items[] }

Reads from output/data/daily/*.json (one file per daily brief). Emits one
manifest item per recent brief, with the URL pointing at that day's archive
page so users land on the full brief, not just the index.

Run standalone for testing:
    python -m pipeline.render.manifest [output_dir]
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("daily-brief.manifest")

SITE_URL = "https://kipeum86.github.io/daily-brief/"
NAME = "Daily Brief"
CATEGORY = "Daily · Macro"
ACCENT = "#1a3a6b"
DESCRIPTION = "글로벌 매크로 + 국내 뉴스 · 매일 06:30 KST"
SOURCE_LABEL = "Daily Brief"
MAX_ITEMS = 10


def _published_at_for_brief(date_str: str, brief: dict[str, Any]) -> str:
    """Return ISO 8601 UTC timestamp for a daily brief.

    Prefer the explicit `generated_at` field; fall back to the brief date at
    21:30 UTC (the cron schedule that publishes morning briefs). Always
    normalises to a Z-suffixed UTC string so the briefing-hub's parser
    accepts it.
    """
    raw = brief.get("generated_at")
    if isinstance(raw, str) and raw:
        # generated_at may be naive ISO ("2026-04-04T23:18:14") — treat as UTC
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    # Fallback: date at 21:30 UTC (matches cron)
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=21, minute=30, tzinfo=timezone.utc
        )
        return d.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SECTION_HEADER_PATTERNS = {
    "key insight",
    "market overview",
    "macro overview",
    "주요 인사이트",
    "시장 개요",
    "오늘의 인사이트",
    "summary",
    "오늘의 요약",
}


def _first_meaningful_line(text: str) -> str | None:
    """First line that looks like a real lead sentence, not a section header.

    Skips:
      - empty lines
      - markdown headers ('#', '##', etc.)
      - markdown horizontal rules ('---', '***', '___')
      - lines shorter than 30 chars (typically section labels like
        "Key Insight" or "Market Overview" — too short to be a real lead)
      - exact known header phrases (case-insensitive)
    """
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith(("---", "***", "___")):
            continue
        # Strip leading bullet markers
        if line.startswith(("- ", "* ", "+ ")):
            line = line[2:].strip()
        if line.lower() in _SECTION_HEADER_PATTERNS:
            continue
        if len(line) < 30:
            continue
        return line[:200]
    return None


def _title_for_brief(date_str: str, brief: dict[str, Any]) -> str:
    """Pick a human-readable headline for the brief.

    Order of preference: first meaningful line of insight.ko (skipping
    markdown headers), first ko-bucket article title, then a fallback like
    "🟢 위험 선호 · 2026-04-22".
    """
    insight = brief.get("insight")
    if isinstance(insight, dict):
        ko = insight.get("ko")
        if isinstance(ko, str):
            line = _first_meaningful_line(ko)
            if line:
                return line

    articles = brief.get("articles")
    if isinstance(articles, dict):
        ko_arts = articles.get("ko") or articles.get("raw")
        if isinstance(ko_arts, list) and ko_arts:
            first = ko_arts[0]
            if isinstance(first, dict):
                t = first.get("title")
                if isinstance(t, str) and t.strip():
                    return t.strip()[:200]

    pulse = brief.get("market_pulse") or {}
    label = pulse.get("label_ko") or pulse.get("label_en") or "Daily Brief"
    return f"{label} · {date_str}"


def _archive_url(date_str: str) -> str:
    """Per-date archive URL on the live site."""
    return f"{SITE_URL}archive/{date_str}.html"


def build_manifest(output_dir: str | Path) -> dict[str, Any]:
    """Walk output/data/daily/, build the manifest dict."""
    out = Path(output_dir)
    daily_dir = out / "data" / "daily"
    if not daily_dir.exists():
        logger.warning("No daily data directory at %s — manifest will be empty", daily_dir)
        return {
            "name": NAME,
            "category": CATEGORY,
            "accent": ACCENT,
            "description": DESCRIPTION,
            "url": SITE_URL,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    candidates = sorted(
        (p for p in daily_dir.glob("*.json") if p.name != "latest.json"),
        key=lambda p: p.stem,
        reverse=True,
    )

    items: list[dict[str, Any]] = []
    for path in candidates[:MAX_ITEMS]:
        try:
            brief = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Skipping unreadable brief %s: %s", path, e)
            continue
        date_str = brief.get("date") or path.stem
        items.append({
            "title": _title_for_brief(date_str, brief),
            "source": SOURCE_LABEL,
            "url": _archive_url(date_str),
            "published_at": _published_at_for_brief(date_str, brief),
        })

    latest = items[0] if items else None
    updated_at = (
        latest["published_at"]
        if latest
        else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    return {
        "name": NAME,
        "category": CATEGORY,
        "accent": ACCENT,
        "description": DESCRIPTION,
        "url": SITE_URL,
        "updated_at": updated_at,
        "latest": latest,
        "items": items,
    }


def write_manifest(output_dir: str | Path) -> Path:
    """Build and persist manifest.json. Returns the written path."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(out)
    target = out / "manifest.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Wrote %s (%d items)", target, len(manifest.get("items") or []))
    return target


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "output"
    path = write_manifest(output_dir)
    print(f"Wrote {path}")
