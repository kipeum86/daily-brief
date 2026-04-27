"""Check 3: Translation completeness — KO world titles Korean, EN korea titles English, no empty fields."""
from __future__ import annotations

import logging
from typing import Any

from pipeline.ai.translate import looks_like_language

logger = logging.getLogger(__name__)


def check_translations(
    articles_ko: list,
    articles_en: list,
    config: dict,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for art in _get_by_bucket(articles_ko, "world"):
        title = _get_title(art)
        summary = _get_summary(art)
        if not title:
            errors.append("KO world article has empty title")
        elif not looks_like_language(title, "ko"):
            errors.append(f"KO world article not translated to Korean: '{title[:60]}'")
        if summary and not looks_like_language(summary, "ko"):
            errors.append(f"KO world article summary not translated to Korean: '{title[:60]}'")

    for art in _get_by_bucket(articles_en, "korea"):
        title = _get_title(art)
        summary = _get_summary(art)
        if not title:
            errors.append("EN korea article has empty title")
        elif not looks_like_language(title, "en"):
            errors.append(f"EN korea article not translated to English: '{title[:60]}'")
        if summary and not looks_like_language(summary, "en"):
            errors.append(f"EN korea article summary not translated to English: '{title[:60]}'")

    for label, articles in [("KO", articles_ko), ("EN", articles_en)]:
        for art in articles:
            title = _get_title(art)
            if not title:
                errors.append(f"{label} article has empty title")

    for label, articles in [("KO", articles_ko), ("EN", articles_en)]:
        for art in articles:
            summary = _get_summary(art)
            if not summary:
                warnings.append(f"{label} article missing summary: '{_get_title(art)[:40]}'")

    return errors, warnings


def _get_by_bucket(articles: list, bucket: str) -> list:
    results = []
    for art in articles:
        b = art.get("bucket", "") if isinstance(art, dict) else getattr(art, "bucket", "")
        if b == bucket:
            results.append(art)
    return results


def _get_title(art) -> str:
    if isinstance(art, dict):
        return art.get("title", "")
    return getattr(art, "title", "")


def _get_summary(art) -> str:
    if isinstance(art, dict):
        return art.get("summary", "") or art.get("description", "")
    return getattr(art, "description", "") or getattr(art, "summary", "")
