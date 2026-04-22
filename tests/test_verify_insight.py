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


def test_decimal_in_direction_sentence_not_split():
    # Regression: 2026-04-22 run failed because "2.7%" split the sentence,
    # dropping the 급등세/강세 words and leaving only 약세 → false 하락 alarm.
    insight = (
        "## Market Overview\n"
        "반면 한국 코스피는 반도체 수출이 182% 폭증하며 역대 최대 수출액을 경신했다는 소식에 힘입어, "
        "글로벌 약세장 속에서도 2.7%대 급등세를 보이며 독자적인 강세를 기록했습니다. "
    ) * 2
    errors, _ = check_insight_accuracy(insight, insight, _markets(kr_change=2.72), _holidays(), no_llm=False)
    assert not any("KOSPI" in e and "하락" in e for e in errors)


def test_news_stat_percentage_not_flagged_as_market_mismatch():
    # Regression: "182% 폭증" (export growth news stat) was flagged as
    # inconsistent with market change_pct values. Now skipped.
    insight = (
        "## Market Overview\n"
        "한국 코스피는 반도체 수출이 182% 폭증하며 역대 최대 수출액을 기록했다는 소식에 힘입어 "
        "2.72% 상승 마감했습니다. " * 3
    )
    _, warnings = check_insight_accuracy(insight, insight, _markets(kr_change=2.72), _holidays(), no_llm=False)
    assert not any("182" in w for w in warnings)
