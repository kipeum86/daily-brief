"""Tests for market data integrity checks."""
from __future__ import annotations

from pipeline.verify.checks.market_data import check_market_data


def _kr_item(price=5478.7, change_pct=8.44, data_date="2026-04-03", source="naver"):
    return {"ticker": "^KS11", "name": "KOSPI", "price": price,
            "change_pct": change_pct, "prev_close": 5052.46,
            "data_date": data_date, "source": source}


def _us_item(price=6575.32, change_pct=0.72, data_date="2026-04-03"):
    return {"ticker": "^GSPC", "name": "S&P 500", "price": price,
            "change_pct": change_pct, "prev_close": 6528.52,
            "data_date": data_date, "source": "yfinance"}


def _holidays(kr=False, us=False):
    names = {}
    if kr: names["kr"] = "추석"
    if us: names["us"] = "Good Friday"
    return {"kospi_holiday": kr, "nyse_holiday": us, "holiday_names": names, "target_date": "2026-04-03"}


def test_all_good():
    markets = {"kr": [_kr_item()], "us": [_us_item()], "fx": [], "commodities": [], "crypto": [], "risk": []}
    errors, warnings = check_market_data(markets, _holidays(), "2026-04-04")
    assert errors == []


def test_negative_price():
    markets = {"kr": [_kr_item(price=-100)], "us": [], "fx": [], "commodities": [], "crypto": [], "risk": []}
    errors, _ = check_market_data(markets, _holidays(), "2026-04-04")
    assert any("price" in e.lower() for e in errors)


def test_extreme_change():
    markets = {"kr": [_kr_item(change_pct=35.0)], "us": [], "fx": [], "commodities": [], "crypto": [], "risk": []}
    errors, _ = check_market_data(markets, _holidays(), "2026-04-04")
    assert any("35.0" in e for e in errors)


def test_holiday_with_nonzero_change():
    markets = {"kr": [_kr_item(change_pct=2.5)], "us": [], "fx": [], "commodities": [], "crypto": [], "risk": []}
    errors, _ = check_market_data(markets, _holidays(kr=True), "2026-04-04")
    assert any("holiday" in e.lower() or "휴장" in e for e in errors)


def test_stale_non_holiday_data_is_error():
    markets = {"kr": [], "us": [_us_item(data_date="2026-04-02")], "fx": [], "commodities": [], "crypto": [], "risk": []}
    errors, _ = check_market_data(markets, _holidays(), "2026-04-04")
    assert any("stale data_date 2026-04-02, target 2026-04-03" in e for e in errors)


def test_stale_holiday_market_data_is_warning():
    markets = {"kr": [], "us": [_us_item(change_pct=0.0, data_date="2026-04-02")], "fx": [], "commodities": [], "crypto": [], "risk": []}
    errors, warnings = check_market_data(markets, _holidays(us=True), "2026-04-04")
    assert not any("stale data_date" in e for e in errors)
    assert any("allowed because market is marked holiday" in w for w in warnings)


def test_missing_data_date_warns():
    item = _kr_item()
    item.pop("data_date")
    markets = {"kr": [item], "us": [], "fx": [], "commodities": [], "crypto": [], "risk": []}
    errors, warnings = check_market_data(markets, _holidays(), "2026-04-04")
    assert not any("data_date missing" in e for e in errors)
    assert any("data_date missing" in w for w in warnings)


def test_invalid_data_date_is_error():
    markets = {"kr": [_kr_item(data_date="not-a-date")], "us": [], "fx": [], "commodities": [], "crypto": [], "risk": []}
    errors, _ = check_market_data(markets, _holidays(), "2026-04-04")
    assert any("invalid data_date" in e for e in errors)
