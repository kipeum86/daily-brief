"""Check 2: AI insight accuracy — emptiness, direction, holiday narration, numbers."""
from __future__ import annotations

import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

_UP_KO = re.compile(r"상승|폭등|급등|랠리|강세|돌파|반등")
_DOWN_KO = re.compile(r"하락|폭락|급락|약세|붕괴|추락|미끄러")

# Split on sentence boundaries but preserve decimal numbers like "2.7%".
# A period only ends a sentence when NOT surrounded by digits.
_SENTENCE_SPLIT = re.compile(r"(?<!\d)\.(?!\d)|[。\n]")

_MIN_INSIGHT_LENGTH = 200


def check_insight_accuracy(
    insight_ko: str,
    insight_en: str,
    markets: dict[str, list[dict[str, Any]]],
    holidays: dict[str, Any],
    no_llm: bool,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if no_llm:
        return errors, warnings

    # 1. Non-empty
    if len(insight_ko) < _MIN_INSIGHT_LENGTH:
        errors.append(f"Korean insight too short ({len(insight_ko)} chars, min {_MIN_INSIGHT_LENGTH})")
    if len(insight_en) < _MIN_INSIGHT_LENGTH:
        errors.append(f"English insight too short ({len(insight_en)} chars, min {_MIN_INSIGHT_LENGTH})")

    if not insight_ko:
        return errors, warnings

    # 2. Direction match
    kr_change = _get_change(markets, "kr", 0)
    us_change = _get_change(markets, "us", 0)
    kr_holiday = holidays.get("kospi_holiday", False)
    us_holiday = holidays.get("nyse_holiday", False)

    if not kr_holiday and kr_change is not None:
        _check_direction(insight_ko, "코스피", "KOSPI", kr_change, errors)
    if not us_holiday and us_change is not None:
        _check_direction(insight_ko, "나스닥", "Nasdaq", _get_change(markets, "us", 1) or us_change, errors)

    # 3. Holiday narration
    if us_holiday:
        _check_holiday_narration(insight_ko, ["미국 증시는 오늘", "미국 시장이 오늘", "미국 시장은 오늘"], "NYSE", errors)
    if kr_holiday:
        _check_holiday_narration(insight_ko, ["코스피가 오늘", "한국 증시는 오늘", "한국 시장이 오늘"], "KRX", errors)

    # 4. Number accuracy (WARNING only)
    _check_numbers(insight_ko, markets, warnings)

    return errors, warnings


def _get_change(markets: dict, section: str, index: int) -> float | None:
    items = markets.get(section, [])
    if index < len(items):
        return items[index].get("change_pct")
    return None


def _check_direction(text: str, ko_name: str, en_name: str, change: float, errors: list[str]) -> None:
    for sentence in _SENTENCE_SPLIT.split(text):
        if ko_name not in sentence and en_name not in sentence:
            continue
        has_up = bool(_UP_KO.search(sentence))
        has_down = bool(_DOWN_KO.search(sentence))
        if has_up and not has_down and change < -0.5:
            errors.append(f"Insight says {en_name} 상승 but actual change is {change:+.2f}%")
        elif has_down and not has_up and change > 0.5:
            errors.append(f"Insight says {en_name} 하락 but actual change is {change:+.2f}%")


def _check_holiday_narration(text: str, patterns: list[str], market: str, errors: list[str]) -> None:
    exempt = re.compile(r"오늘.*휴장|오늘은.*휴장")
    for pattern in patterns:
        if pattern in text:
            for sentence in _SENTENCE_SPLIT.split(text):
                if pattern in sentence and not exempt.search(sentence):
                    errors.append(f"휴장 시장 서술: {market} is closed but insight says '{pattern}...'")
                    return


def _check_numbers(text: str, markets: dict, warnings: list[str]) -> None:
    all_changes = []
    for items in markets.values():
        for item in items:
            c = item.get("change_pct")
            if c is not None:
                all_changes.append(abs(c))

    for match in re.finditer(r"(\d+\.?\d*)%", text):
        cited = float(match.group(1))
        # Skip values outside plausible daily market-change range — these are
        # almost always news statistics (e.g. YoY export growth) rather than
        # ticker moves, and comparing them to change_pct produces false alarms.
        if cited < 0.1 or cited > 20:
            continue
        closest = min((abs(cited - c) for c in all_changes), default=999)
        if closest > 1.0 and all_changes:
            warnings.append(f"Cited {cited}% not close to any market data (closest diff: {closest:.1f}%p)")
