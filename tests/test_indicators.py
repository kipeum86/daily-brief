from __future__ import annotations

from pipeline.markets.indicators import calculate_market_pulse


def _raw_data(vix: float, usdkrw: float, sp500: float):
    return {
        "risk": [{"name": "VIX", "change_pct": vix}],
        "fx": [{"name": "USD/KRW", "change_pct": usdkrw}],
        "us": [{"name": "S&P 500", "change_pct": sp500}],
    }


def test_market_pulse_has_label_en():
    pulse = calculate_market_pulse(_raw_data(vix=-6.0, usdkrw=-0.8, sp500=1.5))
    assert pulse["label_en"]
    assert pulse["level"] == "risk_on"


def test_market_pulse_all_states_have_both_labels():
    scenarios = [
        _raw_data(vix=6.0, usdkrw=0.7, sp500=-1.5),
        _raw_data(vix=1.0, usdkrw=0.8, sp500=0.0),
        _raw_data(vix=0.0, usdkrw=0.0, sp500=0.0),
        _raw_data(vix=-1.0, usdkrw=0.0, sp500=0.0),
        _raw_data(vix=-6.0, usdkrw=-0.8, sp500=1.5),
    ]
    for scenario in scenarios:
        pulse = calculate_market_pulse(scenario)
        assert pulse["label_ko"]
        assert pulse["label_en"]
