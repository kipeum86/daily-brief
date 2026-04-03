"""
시장 지표 계산 및 휴장 감지 모듈

수집된 원시 데이터에서 포맷팅된 수치와 휴장 여부를 판단한다.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 숫자 포맷팅
# ---------------------------------------------------------------------------

def _format_korean_number(value: float, decimals: int = 2) -> str:
    """숫자를 한국식 천 단위 콤마 포맷으로 변환한다.

    Examples:
        2547.3  -> "2,547.30"
        1385123 -> "1,385,123.00"
        0.0531  -> "0.05"
    """
    formatted = f"{value:,.{decimals}f}"
    return formatted


def _format_change(change_pct: float) -> str:
    """변동률을 부호 포함 문자열로 변환한다.

    Examples:
        1.23  -> "+1.23%"
        -0.45 -> "-0.45%"
        0.0   -> "0.00%"
    """
    if change_pct > 0:
        return f"+{change_pct:.2f}%"
    return f"{change_pct:.2f}%"


def _choose_decimals(item: dict[str, Any]) -> int:
    """티커 종류에 따라 적절한 소수점 자릿수를 결정한다."""
    ticker = item.get("ticker", "")
    price = item.get("price", 0)

    # 환율, 금리 등 소수점이 중요한 항목
    if ticker in ("^TNX", "^VIX", "DGS10", "DTWEXBGS"):
        return 2
    # FX — 소수점이 이미 많을 수 있음
    if "KRW" in ticker or "=X" in ticker:
        return 2
    # 가격이 작으면 소수점 유지 (예: ETH-USD가 0.xx일 리 없지만 안전장치)
    if price < 1:
        return 4
    # 한국 지수는 소수점 2자리
    return 2


# ---------------------------------------------------------------------------
# 메인 함수
# ---------------------------------------------------------------------------

def calculate_indicators(raw_data: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """원시 시장 데이터에 포맷팅된 문자열을 추가한다.

    각 항목에 다음 필드가 추가된다:
        - price_fmt: 한국식 포맷 가격 문자열
        - change_fmt: 부호 포함 변동률 문자열 (예: "+1.23%")
        - prev_close_fmt: 한국식 포맷 전일 종가

    Args:
        raw_data: collect_market_data()의 반환값

    Returns:
        동일 구조에 포맷 필드가 추가된 dict
    """
    result: dict[str, list[dict[str, Any]]] = {}

    for section_key, items in raw_data.items():
        enriched: list[dict[str, Any]] = []
        for item in items:
            decimals = _choose_decimals(item)
            enriched_item = {
                **item,
                "price_fmt": _format_korean_number(item["price"], decimals),
                "change_fmt": _format_change(item["change_pct"]),
                "prev_close_fmt": _format_korean_number(item["prev_close"], decimals),
            }
            enriched.append(enriched_item)
        result[section_key] = enriched

    return result


def detect_holidays(
    raw_data: dict[str, list[dict[str, Any]]],
    run_date: str = "",
) -> dict[str, Any]:
    """시장 휴장 여부를 감지한다.

    판단 우선순위:
      1. 공휴일 캘린더 (run_date 기준)
      2. data_date가 run_date보다 오래됨 (데이터 미갱신 → 휴장 가능성)
      3. 전 티커 변동률 0 (fallback)

    Args:
        raw_data: collect_market_data()의 반환값
        run_date: 브리핑 대상 날짜 (YYYY-MM-DD)

    Returns:
        {
            "kospi_holiday": bool,
            "nyse_holiday": bool,
            "holiday_names": {"kr": "설날", "us": "Good Friday"},
        }
    """
    from pipeline.markets.holidays import get_kr_holiday, get_us_holiday

    def _all_zero(items: list[dict[str, Any]]) -> bool:
        if not items:
            return False
        return all(item.get("change_pct", 0) == 0.0 for item in items)

    def _data_stale(items: list[dict[str, Any]], target_date: str) -> bool:
        """데이터 기준일이 target_date보다 과거인지 확인."""
        if not items or not target_date:
            return False
        return all(
            item.get("data_date", target_date) < target_date
            for item in items
        )

    kr_items = raw_data.get("kr", [])
    us_items = raw_data.get("us", [])

    # 1) 캘린더 기반 감지
    kr_reason = get_kr_holiday(run_date) if run_date else None
    us_reason = get_us_holiday(run_date) if run_date else None

    # 2) data_date 기반 보조 감지 (캘린더에 없는 임시 휴장)
    if not kr_reason and _data_stale(kr_items, run_date):
        kr_reason = "시장 휴장"
    if not us_reason and _data_stale(us_items, run_date):
        us_reason = "Market Closed"

    # 3) 변동률 0 fallback (캘린더+data_date 둘 다 놓쳤을 때)
    if not kr_reason and _all_zero(kr_items):
        kr_reason = "시장 휴장"
    if not us_reason and _all_zero(us_items):
        us_reason = "Market Closed"

    kospi_holiday = kr_reason is not None
    nyse_holiday = us_reason is not None

    holiday_names: dict[str, str] = {}
    if kospi_holiday:
        holiday_names["kr"] = kr_reason  # type: ignore[assignment]
        logger.info("한국 시장 휴장: %s", kr_reason)
    if nyse_holiday:
        holiday_names["us"] = us_reason  # type: ignore[assignment]
        logger.info("미국 시장 휴장: %s", us_reason)

    return {
        "kospi_holiday": kospi_holiday,
        "nyse_holiday": nyse_holiday,
        "holiday_names": holiday_names,
    }


def calculate_market_pulse(raw_data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """VIX + 환율 + 금리 방향을 조합하여 시장 온도를 계산한다.

    Returns:
        {
            "level": "risk_off" | "cautious" | "neutral" | "risk_on",
            "label_ko": "🔴 위험 회피" | "🟡 경계" | "⚪ 중립" | "🟢 위험 선호",
            "label_en": "🔴 Risk-Off" | "🟡 Cautious" | "⚪ Neutral" | "🟢 Risk-On",
            "score": int (-3 ~ +3),
            "signals": list[str],
        }
    """
    score = 0
    signals = []

    # VIX 방향
    risk_items = {item["name"]: item for item in raw_data.get("risk", [])}
    vix = risk_items.get("VIX", {})
    if vix:
        vix_chg = vix.get("change_pct", 0)
        if vix_chg > 5:
            score -= 2
            signals.append(f"VIX {vix_chg:+.1f}% ↑↑")
        elif vix_chg > 0:
            score -= 1
            signals.append(f"VIX {vix_chg:+.1f}% ↑")
        elif vix_chg < -5:
            score += 2
            signals.append(f"VIX {vix_chg:+.1f}% ↓↓")
        elif vix_chg < 0:
            score += 1
            signals.append(f"VIX {vix_chg:+.1f}% ↓")

    # 달러 방향 (달러 강세 = risk-off 경향)
    fx_items = {item["name"]: item for item in raw_data.get("fx", [])}
    usdkrw = fx_items.get("USD/KRW", {})
    if usdkrw:
        fx_chg = usdkrw.get("change_pct", 0)
        if fx_chg > 0.5:
            score -= 1
            signals.append(f"USD/KRW {fx_chg:+.1f}% (달러 강세)")
        elif fx_chg < -0.5:
            score += 1
            signals.append(f"USD/KRW {fx_chg:+.1f}% (달러 약세)")

    # S&P 방향
    us_items = {item["name"]: item for item in raw_data.get("us", [])}
    sp500 = us_items.get("S&P 500", {})
    if sp500:
        sp_chg = sp500.get("change_pct", 0)
        if sp_chg > 1:
            score += 1
            signals.append(f"S&P 500 {sp_chg:+.1f}%")
        elif sp_chg < -1:
            score -= 1
            signals.append(f"S&P 500 {sp_chg:+.1f}%")

    # 판정
    if score <= -2:
        level, label_ko, label_en = "risk_off", "🔴 위험 회피", "🔴 Risk-Off"
    elif score == -1:
        level, label_ko, label_en = "cautious", "🟡 경계", "🟡 Cautious"
    elif score >= 2:
        level, label_ko, label_en = "risk_on", "🟢 위험 선호", "🟢 Risk-On"
    elif score == 1:
        level, label_ko, label_en = "mild_risk_on", "🟢 완만한 낙관", "🟢 Mildly Bullish"
    else:
        level, label_ko, label_en = "neutral", "⚪ 중립", "⚪ Neutral"

    return {
        "level": level,
        "label_ko": label_ko,
        "label_en": label_en,
        "score": score,
        "signals": signals,
    }


def generate_sparkline_svg(values: list[float], width: int = 50, height: int = 16) -> str:
    """5일 종가 리스트로 프리미엄 인라인 SVG 스파크라인을 생성한다.

    부드러운 곡선(cubic bezier) + 그라디언트 채움 + 끝점 도트.

    Returns:
        SVG 문자열 (인라인 삽입용)
    """
    if not values or len(values) < 2:
        return ""

    min_v = min(values)
    max_v = max(values)
    spread = max_v - min_v if max_v != min_v else 1
    pad = 2  # 상하 패딩

    # 좌표 계산
    coords = []
    step = width / (len(values) - 1)
    for i, v in enumerate(values):
        x = round(i * step, 2)
        y = round(height - pad - ((v - min_v) / spread) * (height - pad * 2), 2)
        coords.append((x, y))

    # 색상
    up = values[-1] >= values[0]
    color = "#16A34A" if up else "#DC2626"
    color_light = "#16A34A20" if up else "#DC262620"

    # 유니크 ID (같은 페이지에 여러 스파크라인)
    uid = f"sp{abs(hash(tuple(values))) % 100000}"

    # Smooth cubic bezier path
    def _smooth_path(pts: list[tuple[float, float]]) -> str:
        if len(pts) < 2:
            return ""
        d = [f"M{pts[0][0]},{pts[0][1]}"]
        for i in range(1, len(pts)):
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            # Control points at 1/3 of segment length (horizontal smoothing)
            cx = (x1 - x0) * 0.4
            d.append(f"C{x0 + cx},{y0} {x1 - cx},{y1} {x1},{y1}")
        return " ".join(d)

    line_path = _smooth_path(coords)
    # Area path (close to bottom)
    area_path = line_path + f" L{coords[-1][0]},{height} L{coords[0][0]},{height} Z"

    last_x, last_y = coords[-1]

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="vertical-align:middle;margin-left:6px;flex-shrink:0;">'
        f'<defs>'
        f'<linearGradient id="{uid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.15"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0.02"/>'
        f'</linearGradient>'
        f'</defs>'
        f'<path d="{area_path}" fill="url(#{uid})"/>'
        f'<path d="{line_path}" fill="none" stroke="{color}" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="1.5" fill="{color}"/>'
        f'</svg>'
    )
