"""
네이버 증권 API를 통한 한국 시장 데이터 수집

yfinance가 한국 시장 데이터 지연/누락이 잦아,
네이버 증권을 한국 지수(KOSPI, KOSDAQ) primary 소스로 사용한다.

히스토리(price) API를 primary로 사용한다.
basic API는 장 시작 전(PREOPEN)에 전일 등락률이 0으로 리셋되므로,
모닝브리프(06:30 KST)에는 부적합하다.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_BASE_URL = "https://m.stock.naver.com/api/index"
_USER_AGENT = "Mozilla/5.0 (compatible; DailyBrief/1.0)"
_TIMEOUT = 15

# 네이버 증권 코드 ↔ yfinance 티커 매핑
_NAVER_CODE_MAP: dict[str, str] = {
    "^KS11": "KOSPI",
    "^KQ11": "KOSDAQ",
}


def _parse_price(s: str) -> float:
    """'5,478.70' → 5478.70"""
    return float(s.replace(",", ""))


def _fetch_naver_index(naver_code: str, name: str, history_count: int = 5) -> dict[str, Any] | None:
    """네이버 증권 히스토리 API에서 단일 지수 데이터를 가져온다.

    히스토리 API는 시장 상태(PREOPEN/CLOSE/TRADING)와 무관하게
    확정된 일별 종가·등락률을 정확하게 제공한다.

    Returns:
        성공 시 dict, 실패 시 None
    """
    try:
        hist_url = f"{_BASE_URL}/{naver_code}/price?pageSize={history_count}&page=1"
        req = urllib.request.Request(hist_url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            hist_data = json.loads(resp.read())

        if not hist_data:
            logger.warning("[Naver] %s: 히스토리 데이터 없음", naver_code)
            return None

        # 최신 거래일 데이터 (리스트 첫 번째 = 가장 최근)
        latest = hist_data[0]
        close_price = _parse_price(latest["closePrice"])
        change_pct = float(latest["fluctuationsRatio"])
        change_val = float(latest["compareToPreviousClosePrice"])
        prev_close = close_price - change_val
        data_date = latest["localTradedAt"]  # "2026-04-01"

        # 스파크라인: 오래된 → 최신 순서
        sparkline = [_parse_price(item["closePrice"]) for item in reversed(hist_data)]

        return {
            "ticker": next(
                (t for t, c in _NAVER_CODE_MAP.items() if c == naver_code),
                naver_code,
            ),
            "name": name,
            "price": round(close_price, 2),
            "change_pct": round(change_pct, 2),
            "prev_close": round(prev_close, 2),
            "sparkline": sparkline,
            "volume": 0,
            "data_date": data_date,
            "source": "naver",
        }
    except Exception as e:
        logger.warning("[Naver] %s (%s) 수집 실패: %s", name, naver_code, e)
        return None


def fetch_korean_indices(
    tickers: list[str],
    names: list[str],
) -> list[dict[str, Any]]:
    """네이버 증권에서 한국 지수 데이터를 수집한다.

    Args:
        tickers: yfinance 티커 목록 (예: ["^KS11", "^KQ11"])
        names: 표시 이름 목록 (예: ["KOSPI", "KOSDAQ"])

    Returns:
        수집 성공한 데이터 리스트. 네이버에 없는 티커는 건너뛴다.
    """
    results: list[dict[str, Any]] = []

    for ticker, name in zip(tickers, names):
        naver_code = _NAVER_CODE_MAP.get(ticker)
        if not naver_code:
            continue

        result = _fetch_naver_index(naver_code, name)
        if result:
            results.append(result)
            logger.info(
                "[Naver] %s: %s (%+.2f%%) — 기준일 %s",
                name, result["price"], result["change_pct"], result["data_date"],
            )

    return results
