"""Check 4: Content completeness — insight, article count, Korea purity, cross-section overlap."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from pipeline.news.quality_gates import (
        DOMESTIC_KEYWORDS,
        INTERNATIONAL_KEYWORDS,
        LOW_VALUE_KEYWORDS,
    )
except ImportError:
    INTERNATIONAL_KEYWORDS = {"Trump", "Iran", "Russia", "China", "EU", "트럼프", "이란", "러시아"}
    DOMESTIC_KEYWORDS = {"한국", "국내", "정부", "코스피", "코스닥"}
    LOW_VALUE_KEYWORDS = {"인사발령", "부고", "운세", "로또", "날씨", "부임", "전보", "승진인사"}

_MIN_INSIGHT_LENGTH = 200


def check_content_completeness(
    articles_ko: list,
    articles_en: list,
    insight_ko: str,
    insight_en: str,
    run_date: str,
    no_llm: bool,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not no_llm:
        if not insight_ko or len(insight_ko) < _MIN_INSIGHT_LENGTH:
            errors.append(f"Korean insight missing or too short ({len(insight_ko or '')} chars)")
        if not insight_en or len(insight_en) < _MIN_INSIGHT_LENGTH:
            errors.append(f"English insight missing or too short ({len(insight_en or '')} chars)")

    world_ko, korea_ko = _split_by_bucket(articles_ko)
    if len(world_ko) < 3:
        errors.append(f"Only {len(world_ko)} world articles (min 3)")
    if len(korea_ko) < 3:
        errors.append(f"Only {len(korea_ko)} korea articles (min 3)")

    _check_korea_purity(korea_ko, errors)
    _check_cross_overlap(world_ko, korea_ko, errors, warnings)
    _check_date(run_date, errors)

    return errors, warnings


def _split_by_bucket(articles: list) -> tuple[list[dict], list[dict]]:
    world, korea = [], []
    for art in articles:
        bucket = art.get("bucket", "") if isinstance(art, dict) else getattr(art, "bucket", "")
        if bucket == "world":
            world.append(art if isinstance(art, dict) else {"title": getattr(art, "title", "")})
        elif bucket == "korea":
            korea.append(art if isinstance(art, dict) else {"title": getattr(art, "title", "")})
    return world, korea


def _check_korea_purity(korea_articles: list[dict], errors: list[str]) -> None:
    for art in korea_articles:
        title = art.get("title", "")
        text = f"{title} {art.get('description', '') or art.get('summary', '')}".lower()

        intl_hits = sum(1 for kw in INTERNATIONAL_KEYWORDS if kw.lower() in text)
        domestic_hits = sum(1 for kw in DOMESTIC_KEYWORDS if kw.lower() in text)
        if intl_hits > 0 and domestic_hits == 0:
            errors.append(f"Korea article is international: '{title[:60]}'")

        if any(kw.lower() in text for kw in LOW_VALUE_KEYWORDS):
            errors.append(f"Low-value article in Korea section: '{title[:60]}'")


def _check_cross_overlap(
    world: list[dict],
    korea: list[dict],
    errors: list[str],
    warnings: list[str],
) -> None:
    world_urls = {_url(a) for a in world if _url(a)}
    for art in korea:
        url = _url(art)
        if url and url in world_urls:
            errors.append(f"Duplicate URL across world/korea: '{art.get('title', '')[:50]}'")

    world_tokens = [_tokens(a) for a in world]
    for art in korea:
        k_tokens = _tokens(art)
        if not k_tokens:
            continue
        for w_tokens in world_tokens:
            if not w_tokens:
                continue
            overlap = len(k_tokens & w_tokens)
            smaller = min(len(k_tokens), len(w_tokens))
            if smaller > 0 and overlap / smaller > 0.6:
                warnings.append(f"Topic overlap between world/korea: '{art.get('title', '')[:40]}'")
                break


def _url(art: dict) -> str:
    return art.get("url", "")


def _tokens(art: dict) -> set[str]:
    text = f"{art.get('title', '')} {art.get('summary', '') or art.get('description', '')}".lower()
    return {w for w in text.split() if len(w) >= 2}


def _check_date(run_date: str, errors: list[str]) -> None:
    if not run_date:
        errors.append("run_date is empty")
        return
    try:
        from datetime import date
        date.fromisoformat(run_date)
    except ValueError:
        errors.append(f"run_date '{run_date}' is not a valid ISO date")
