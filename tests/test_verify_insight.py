"""Tests for AI insight accuracy checks."""
from __future__ import annotations

from pipeline.verify.checks.insight import check_insight_accuracy


def _markets(kr_change=8.44, us_change=0.72):
    return {
        "kr": [{"name": "KOSPI", "change_pct": kr_change}],
        "us": [{"name": "S&P 500", "change_pct": us_change}],
    }


def _holidays(kr=False, us=False):
    names = {}
    if kr: names["kr"] = "추석"
    if us: names["us"] = "Good Friday"
    return {"kospi_holiday": kr, "nyse_holiday": us, "holiday_names": names}


def test_skip_when_no_llm():
    errors, warnings = check_insight_accuracy("", "", _markets(), _holidays(), no_llm=True)
    assert errors == []


def test_empty_insight_is_error():
    errors, _ = check_insight_accuracy("", "short", _markets(), _holidays(), no_llm=False)
    assert any("Korean insight" in e for e in errors)


def test_direction_mismatch_kospi():
    insight = "한국 증시는 코스피가 폭등하며 강한 상승세를 보였습니다." * 3
    errors, _ = check_insight_accuracy(insight, insight, _markets(kr_change=-3.0), _holidays(), no_llm=False)
    assert any("KOSPI" in e and "상승" in e for e in errors)


def test_direction_match_ok():
    insight = "한국 증시는 코스피가 폭등하며 강한 상승세를 보였습니다." * 3
    errors, _ = check_insight_accuracy(insight, insight, _markets(kr_change=8.44), _holidays(), no_llm=False)
    direction_errors = [e for e in errors if "방향" in e or "direction" in e.lower() or "상승" in e]
    assert direction_errors == []


def test_holiday_narration_error():
    insight = "미국 증시는 오늘 강한 랠리를 펼쳤습니다. " * 5
    errors, _ = check_insight_accuracy(insight, insight, _markets(), _holidays(us=True), no_llm=False)
    assert any("휴장" in e or "holiday" in e.lower() for e in errors)


def test_holiday_mention_ok():
    insight = "미국 증시는 오늘 Good Friday로 휴장이었습니다. " * 5
    errors, _ = check_insight_accuracy(insight, insight, _markets(), _holidays(us=True), no_llm=False)
    holiday_narration_errors = [e for e in errors if "휴장 시장 서술" in e]
    assert holiday_narration_errors == []
