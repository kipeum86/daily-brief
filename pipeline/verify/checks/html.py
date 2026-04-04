"""Check 5: HTML rendering integrity — size, DOM elements, nav chain, lang toggle."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MIN_HTML_BYTES = 10_000
_MIN_INSIGHT_TEXT = 100


def check_html_integrity(
    html_path: str,
    en_html_path: str,
    no_llm: bool,
    run_date: str = "",
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for label, path in [("KO", html_path), ("EN", en_html_path)]:
        if not path or not Path(path).exists():
            if path:
                errors.append(f"{label} HTML file not found: {path}")
            continue

        content = Path(path).read_text(encoding="utf-8")
        size = len(content.encode("utf-8"))

        if size < _MIN_HTML_BYTES:
            errors.append(f"{label} HTML too small ({size} bytes, min {_MIN_HTML_BYTES})")
            continue

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")

        if not no_llm:
            insight_body = soup.select_one(".insight-body")
            if not insight_body:
                errors.append(f"{label} HTML: .insight-body element missing")
            elif len(insight_body.get_text(strip=True)) < _MIN_INSIGHT_TEXT:
                errors.append(f"{label} HTML: .insight-body text too short")

            for el in soup.find_all(string=lambda t: t and ("사용할 수 없습니다" in t or "not available" in t.lower())):
                errors.append(f"{label} HTML: fallback message detected ('{el.strip()[:60]}')")
                break

        section_titles = [el.get_text(strip=True) for el in soup.select(".section-title")]
        if not any("World" in t or "WORLD" in t for t in section_titles):
            errors.append(f"{label} HTML: World news section missing")
        if not any("Korea" in t or "KOREA" in t for t in section_titles):
            errors.append(f"{label} HTML: Korea news section missing")

        nav = soup.select_one(".header-nav")
        if not nav:
            warnings.append(f"{label} HTML: .header-nav missing")
        else:
            time_el = nav.select_one("time")
            if not time_el:
                warnings.append(f"{label} HTML: <time> element missing in nav")
            elif run_date and time_el.get("datetime", "") != run_date:
                warnings.append(f"{label} HTML: nav date '{time_el.get('datetime')}' != run_date '{run_date}'")

        toggle = soup.select_one(".header-title a")
        if toggle:
            href = toggle.get("href", "")
            if label == "KO" and "/en/" not in href:
                errors.append(f"KO HTML: language toggle doesn't point to EN version (href='{href[:60]}')")
            elif label == "EN" and "/en/" in href:
                errors.append(f"EN HTML: language toggle points to EN instead of KO (href='{href[:60]}')")

    return errors, warnings
