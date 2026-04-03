"""
한국/미국 증시 휴장일 캘린더

고정 공휴일 + 연도별 변동 공휴일(설날, 추석, 부활절 등)을 관리한다.
매년 초에 해당 연도 데이터를 추가해야 한다.
"""

from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# 한국 증시 휴장일 (KRX)
# 고정: 신정, 삼일절, 어린이날, 현충일, 광복절, 개천절, 한글날, 크리스마스
# 변동: 설날(음력 1/1 ±1), 부처님오신날(음력 4/8), 추석(음력 8/15 ±1)
# 대체공휴일, 선거일 등은 확정 시 수동 추가
# ---------------------------------------------------------------------------

_KR_HOLIDAYS: dict[str, str] = {
    # 2026
    "2026-01-01": "신정",
    "2026-01-28": "설날 연휴",
    "2026-01-29": "설날",
    "2026-01-30": "설날 연휴",
    "2026-03-01": "삼일절",
    "2026-03-02": "삼일절 대체공휴일",
    "2026-05-05": "어린이날",
    "2026-05-24": "부처님 오신 날",
    "2026-05-25": "부처님 오신 날 대체공휴일",
    "2026-06-06": "현충일",
    "2026-08-15": "광복절",
    "2026-08-17": "광복절 대체공휴일",
    "2026-09-24": "추석 연휴",
    "2026-09-25": "추석",
    "2026-09-26": "추석 연휴",
    "2026-10-03": "개천절",
    "2026-10-05": "개천절 대체공휴일",
    "2026-10-09": "한글날",
    "2026-12-25": "크리스마스",
    # 2027 — 연초에 추가
}

# ---------------------------------------------------------------------------
# 미국 증시 휴장일 (NYSE/NASDAQ)
# 고정: New Year's, Independence Day, Christmas
# 변동: MLK Day, Presidents' Day, Good Friday, Memorial Day,
#       Juneteenth, Labor Day, Thanksgiving
# ---------------------------------------------------------------------------

_US_HOLIDAYS: dict[str, str] = {
    # 2026
    "2026-01-01": "New Year's Day",
    "2026-01-19": "Martin Luther King Jr. Day",
    "2026-02-16": "Presidents' Day",
    "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth",
    "2026-07-03": "Independence Day (Observed)",
    "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day",
    # 2027 — 연초에 추가
}


def get_kr_holiday(date_iso: str) -> str | None:
    """한국 증시 휴장일이면 사유를 반환, 아니면 None."""
    d = date.fromisoformat(date_iso)
    if d.weekday() >= 5:
        return "주말"
    return _KR_HOLIDAYS.get(date_iso)


def get_us_holiday(date_iso: str) -> str | None:
    """미국 증시 휴장일이면 사유를 반환, 아니면 None."""
    d = date.fromisoformat(date_iso)
    if d.weekday() >= 5:
        return "Weekend"
    return _US_HOLIDAYS.get(date_iso)


def is_kr_holiday(date_iso: str) -> bool:
    return get_kr_holiday(date_iso) is not None


def is_us_holiday(date_iso: str) -> bool:
    return get_us_holiday(date_iso) is not None


def _prev_weekday(d: date) -> date:
    """직전 평일(월~금)을 반환한다."""
    from datetime import timedelta
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def get_brief_target_date(run_date: str) -> str:
    """브리핑 대상일(직전 평일)을 계산한다.

    토요일 실행 → 금요일, 월요일 실행 → 금요일.
    """
    d = date.fromisoformat(run_date)
    return _prev_weekday(d).isoformat()


def get_market_holiday_status(
    run_date: str,
) -> dict[str, dict[str, str | None]]:
    """각 시장의 브리핑 대상일 휴장 상태를 반환한다.

    run_date(파이프라인 실행일) 기준 직전 평일을 구하고,
    그 날이 각 시장에서 공휴일인지 판단한다.

    Returns:
        {
            "kr": {"target_date": "2026-04-03", "holiday": None},
            "us": {"target_date": "2026-04-03", "holiday": "Good Friday"},
        }
    """
    target = get_brief_target_date(run_date)

    return {
        "kr": {
            "target_date": target,
            "holiday": _KR_HOLIDAYS.get(target),  # 주말은 이미 제외됨
        },
        "us": {
            "target_date": target,
            "holiday": _US_HOLIDAYS.get(target),
        },
    }
