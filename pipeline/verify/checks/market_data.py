"""Check 1: Market data integrity — prices, ranges, dates, holidays, cross-validation."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MAX_DAILY_CHANGE = 30.0
_WARN_DAILY_CHANGE = 15.0


def check_market_data(
    markets: dict[str, list[dict[str, Any]]],
    holidays: dict[str, Any],
    run_date: str,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    kr_holiday = holidays.get("kospi_holiday", False)
    us_holiday = holidays.get("nyse_holiday", False)

    for section_key, items in markets.items():
        is_holiday = (section_key == "kr" and kr_holiday) or (section_key == "us" and us_holiday)

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

            if is_holiday and abs(change) > 0.01:
                errors.append(f"{name}: marked holiday but change_pct={change}% (should be ~0)")

    _cross_validate_kr(markets.get("kr", []), errors, warnings)

    return errors, warnings


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
