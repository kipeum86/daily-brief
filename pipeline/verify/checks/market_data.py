"""Check 1: Market data integrity — prices, ranges, dates, holidays, cross-validation."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

_MAX_DAILY_CHANGE = 30.0
_WARN_DAILY_CHANGE = 15.0
_SECTION_HOLIDAY_FLAGS = {
    "kr": "kospi_holiday",
    "us": "nyse_holiday",
}


def check_market_data(
    markets: dict[str, list[dict[str, Any]]],
    holidays: dict[str, Any],
    run_date: str,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for section_key, items in markets.items():
        is_holiday = _is_section_holiday(section_key, holidays)

        for item in items:
            name = item.get("name", section_key)
            price = item.get("price", 0)
            change = item.get("change_pct", 0)

            if price <= 0:
                errors.append(f"{name}: price is {price} (must be > 0)")

            if not is_holiday:
                if abs(change) > _MAX_DAILY_CHANGE:
                    errors.append(f"{name}: change_pct {change}% exceeds ±{_MAX_DAILY_CHANGE}% (data error likely)")
                elif abs(change) > _WARN_DAILY_CHANGE:
                    warnings.append(f"{name}: change_pct {change}% exceeds ±{_WARN_DAILY_CHANGE}%")

            # 휴장일에도 yfinance가 소폭 등락 반환 가능 (장외/선물 반영)
            if is_holiday and abs(change) > 0.5:
                errors.append(f"{name}: marked holiday but change_pct={change}% (should be ~0)")

    _check_data_dates(markets, holidays, run_date, errors, warnings)
    _cross_validate_kr(markets.get("kr", []), errors, warnings)

    return errors, warnings


def _expected_target_date(holidays: dict[str, Any], run_date: str) -> str:
    target_date = holidays.get("target_date", "")
    if target_date:
        return str(target_date)
    if not run_date:
        return ""
    try:
        from pipeline.markets.holidays import get_brief_target_date
        return get_brief_target_date(run_date)
    except Exception as exc:
        logger.warning("Could not derive target date from run_date=%s: %s", run_date, exc)
        return ""


def _is_section_holiday(section_key: str, holidays: dict[str, Any]) -> bool:
    flag = _SECTION_HOLIDAY_FLAGS.get(section_key)
    return bool(flag and holidays.get(flag, False))


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _check_data_dates(
    markets: dict[str, list[dict[str, Any]]],
    holidays: dict[str, Any],
    run_date: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    target_date = _parse_iso_date(_expected_target_date(holidays, run_date))
    if target_date is None:
        warnings.append("Market data target_date unavailable; stale date check skipped")
        return

    for section_key, items in markets.items():
        section_holiday = _is_section_holiday(section_key, holidays)
        for item in items:
            name = item.get("name", section_key)
            raw_data_date = item.get("data_date", "")
            data_date = _parse_iso_date(raw_data_date)

            if raw_data_date and data_date is None:
                errors.append(f"{name}: invalid data_date '{raw_data_date}'")
                continue
            if data_date is None:
                warnings.append(f"{name}: data_date missing; cannot verify freshness")
                continue
            if data_date < target_date:
                message = f"{name}: stale data_date {data_date.isoformat()}, target {target_date.isoformat()}"
                if section_holiday:
                    warnings.append(f"{message} (allowed because market is marked holiday)")
                else:
                    errors.append(message)


def _cross_validate_kr(
    kr_items: list[dict[str, Any]],
    errors: list[str],
    warnings: list[str],
) -> None:
    if not kr_items:
        return
    try:
        from pipeline.markets.naver import fetch_korean_indices
        tickers = [item["ticker"] for item in kr_items]
        names = [item["name"] for item in kr_items]
        naver_data = fetch_korean_indices(tickers, names)
        naver_map = {item["ticker"]: item for item in naver_data}

        for item in kr_items:
            naver_item = naver_map.get(item["ticker"])
            if not naver_item:
                continue
            collected_price = item.get("price", 0)
            naver_price = naver_item.get("price", 0)
            if naver_price and collected_price:
                diff_pct = abs(collected_price - naver_price) / naver_price * 100
                if diff_pct > 2.0:
                    errors.append(
                        f"{item['name']}: collected price {collected_price} vs "
                        f"Naver {naver_price} (diff {diff_pct:.1f}%)"
                    )
    except Exception as exc:
        warnings.append(f"Naver cross-validation skipped: {exc}")
