"""
시장 데이터 수집 모듈

yfinance를 사용해 주요 시장 데이터를 병렬로 수집한다.
리스크 지표는 yfinance 실패 시 FRED API로 자동 폴백한다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any

import yfinance as yf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _fetch_one_ticker(ticker: str, name: str) -> dict[str, Any] | None:
    """yfinance로 단일 티커 데이터를 가져온다.

    Returns:
        성공 시 {"ticker", "name", "price", "change_pct", "prev_close",
                 "data_date", "source"} dict,
        실패 시 None
    """
    try:
        tk = yf.Ticker(ticker)
        # 최근 5일치를 가져와서 마지막 2개 종가를 비교한다
        hist = tk.history(period="5d")
        if hist.empty or len(hist) < 1:
            logger.warning("[yfinance] %s (%s): 데이터 없음", name, ticker)
            return None

        last_close = float(hist["Close"].iloc[-1])

        if len(hist) >= 2:
            prev_close = float(hist["Close"].iloc[-2])
            change_pct = ((last_close - prev_close) / prev_close) * 100 if prev_close else 0.0
        else:
            prev_close = last_close
            change_pct = 0.0

        # 5일 종가 히스토리 (스파크라인용)
        sparkline = [round(float(c), 2) for c in hist["Close"].tolist()]

        # 거래량 (가장 최근)
        volume = int(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0

        # 데이터 기준일
        data_date = hist.index[-1].date().isoformat()

        return {
            "ticker": ticker,
            "name": name,
            "price": round(last_close, 2),
            "change_pct": round(change_pct, 2),
            "prev_close": round(prev_close, 2),
            "sparkline": sparkline,
            "volume": volume,
            "data_date": data_date,
            "source": "yfinance",
        }
    except Exception as e:
        logger.warning("[yfinance] %s (%s) 수집 실패: %s", name, ticker, e)
        return None


def _fetch_fred_series(series_id: str, name: str) -> dict[str, Any] | None:
    """FRED API로 단일 시리즈를 가져온다 (fredapi 필요).

    Returns:
        성공 시 dict, 실패 시 None
    """
    try:
        import os
        from fredapi import Fred

        api_key = os.environ.get("FRED_API_KEY", "")
        if not api_key:
            logger.warning("[FRED] FRED_API_KEY 환경변수 미설정 — %s 건너뜀", series_id)
            return None

        fred = Fred(api_key=api_key)
        # 최근 10일치 가져오기 (주말·공휴일 건너뛰기 위해 넉넉히)
        end = datetime.now()
        start = end - timedelta(days=10)
        data = fred.get_series(series_id, observation_start=start, observation_end=end)

        if data is None or data.empty:
            logger.warning("[FRED] %s (%s): 데이터 없음", name, series_id)
            return None

        data = data.dropna()
        if len(data) < 1:
            return None

        last_val = float(data.iloc[-1])
        if len(data) >= 2:
            prev_val = float(data.iloc[-2])
            change_pct = ((last_val - prev_val) / prev_val) * 100 if prev_val else 0.0
        else:
            prev_val = last_val
            change_pct = 0.0

        return {
            "ticker": series_id,
            "name": name,
            "price": round(last_val, 4),
            "change_pct": round(change_pct, 2),
            "prev_close": round(prev_val, 4),
        }
    except ImportError:
        logger.warning("[FRED] fredapi 패키지 미설치 — %s 건너뜀", series_id)
        return None
    except Exception as e:
        logger.warning("[FRED] %s (%s) 수집 실패: %s", name, series_id, e)
        return None


def _fetch_one_ticker_window(
    ticker: str,
    name: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any] | None:
    """Fetch weekly history using the prior close before start_date as baseline."""
    try:
        tk = yf.Ticker(ticker)
        week_start = date.fromisoformat(start_date)
        week_end = date.fromisoformat(end_date)
        fetch_start = (week_start - timedelta(days=10)).isoformat()
        end_exclusive = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
        hist = tk.history(start=fetch_start, end=end_exclusive)
        if hist.empty or "Close" not in hist.columns:
            logger.warning("[yfinance] %s (%s): 주간 데이터 없음", name, ticker)
            return None

        closes = hist["Close"].dropna()
        if closes.empty:
            logger.warning("[yfinance] %s (%s): 유효한 종가 없음", name, ticker)
            return None

        before_week = closes[closes.index.date < week_start]
        within_week = closes[
            (closes.index.date >= week_start) & (closes.index.date <= week_end)
        ]
        if within_week.empty:
            logger.warning("[yfinance] %s (%s): 주간 거래일 종가 없음", name, ticker)
            return None

        points: list[dict[str, Any]] = []
        prev_close = 0.0

        if not before_week.empty:
            baseline_idx = before_week.index[-1]
            baseline_close = float(before_week.iloc[-1])
            points.append({
                "date": baseline_idx.date().isoformat(),
                "price": round(baseline_close, 4),
                "change_pct": 0.0,
            })
            prev_close = baseline_close

        for idx, close in within_week.items():
            close_val = float(close)
            change_pct = ((close_val - prev_close) / prev_close * 100) if prev_close else 0.0
            points.append({
                "date": idx.date().isoformat(),
                "price": round(close_val, 4),
                "change_pct": round(change_pct, 2),
            })
            prev_close = close_val

        return {
            "ticker": ticker,
            "name": name,
            "points": points,
        }
    except Exception as e:
        logger.warning("[yfinance] %s (%s) 주간 데이터 수집 실패: %s", name, ticker, e)
        return None


# ---------------------------------------------------------------------------
# 섹션별 수집 함수
# ---------------------------------------------------------------------------

# FRED 시리즈 ID → yfinance 티커 매핑 (리스크 지표 폴백용)
_FRED_TICKER_MAP: dict[str, str] = {
    "^TNX": "DGS10",       # US 10Y Treasury
    "DX-Y.NYB": "DTWEXBGS",  # Dollar Index → 무역가중 달러
}


def _collect_section(
    tickers: list[str],
    names: list[str],
    fred_series: list[str] | None = None,
) -> list[dict[str, Any]]:
    """티커 목록을 병렬로 수집한다.

    yfinance 실패 시, fred_series가 제공되었으면 FRED로 폴백 시도.
    """
    results: list[dict[str, Any]] = []

    # FRED 폴백 매핑 구성
    fred_map: dict[str, str] = {}
    if fred_series:
        # config의 fred_series를 티커와 매핑
        for ticker in tickers:
            if ticker in _FRED_TICKER_MAP:
                sid = _FRED_TICKER_MAP[ticker]
                if sid in fred_series:
                    fred_map[ticker] = sid

    with ThreadPoolExecutor(max_workers=len(tickers) or 1) as pool:
        future_to_meta = {
            pool.submit(_fetch_one_ticker, t, n): (t, n)
            for t, n in zip(tickers, names)
        }
        for future in as_completed(future_to_meta):
            ticker, name = future_to_meta[future]
            result = future.result()

            # yfinance 실패 → FRED 폴백
            if result is None and ticker in fred_map:
                logger.info("[폴백] %s → FRED %s 시도", ticker, fred_map[ticker])
                result = _fetch_fred_series(fred_map[ticker], name)

            if result is not None:
                results.append(result)
            else:
                logger.warning("%s (%s): 모든 소스 실패, 건너뜀", name, ticker)

    # 원래 티커 순서 유지
    order = {t: i for i, t in enumerate(tickers)}
    # FRED 폴백 결과는 원래 yfinance 티커의 순서를 따름
    def sort_key(item: dict) -> int:
        return order.get(item["ticker"], order.get(
            next((k for k, v in _FRED_TICKER_MAP.items() if v == item["ticker"]), ""),
            999,
        ))
    results.sort(key=sort_key)

    return results


# ---------------------------------------------------------------------------
# 메인 수집 함수
# ---------------------------------------------------------------------------

def collect_market_data(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """config.yaml의 markets 섹션을 기반으로 전체 시장 데이터를 수집한다.

    한국 지수(kr)는 네이버 증권을 primary로 사용하고,
    네이버에서 수집 실패한 티커만 yfinance로 폴백한다.

    Args:
        config: 전체 config dict (config["markets"] 사용)

    Returns:
        {
            "kr": [...],
            "us": [...],
            "fx": [...],
            "commodities": [...],
            "crypto": [...],
            "risk": [...],
        }
        각 항목: {"ticker", "name", "price", "change_pct", "prev_close",
                  "data_date", "source"}
    """
    mkts = config.get("markets", {})
    output: dict[str, list[dict[str, Any]]] = {}

    # 섹션 정의: (key, tickers_key, names_key)
    sections = [
        ("kr", "indices", "names"),
        ("us", "indices", "names"),
        ("fx", "pairs", "names"),
        ("commodities", "tickers", "names"),
        ("crypto", "tickers", "names"),
        ("risk", "tickers", "names"),
        ("sectors", "tickers", "names"),
    ]

    for section_key, tickers_field, names_field in sections:
        section_cfg = mkts.get(section_key, {})
        tickers = section_cfg.get(tickers_field, [])
        names = section_cfg.get(names_field, [])

        if not tickers:
            logger.info("섹션 '%s': 티커 없음, 건너뜀", section_key)
            output[section_key] = []
            continue

        # 이름 목록이 부족하면 티커로 대체
        if len(names) < len(tickers):
            names = names + tickers[len(names):]

        try:
            if section_key == "kr":
                output[section_key] = _collect_kr_section(tickers, names)
            else:
                fred_series = section_cfg.get("fred_series") if section_key == "risk" else None
                output[section_key] = _collect_section(tickers, names, fred_series)
            logger.info(
                "섹션 '%s': %d/%d 티커 수집 완료",
                section_key, len(output[section_key]), len(tickers),
            )
        except Exception as e:
            logger.error("섹션 '%s' 수집 중 오류: %s", section_key, e)
            output[section_key] = []

    return output


def _collect_kr_section(
    tickers: list[str],
    names: list[str],
) -> list[dict[str, Any]]:
    """한국 시장: 네이버 증권 primary → yfinance fallback.

    네이버에서 수집된 티커는 제외하고, 나머지만 yfinance로 폴백.
    """
    from pipeline.markets.naver import fetch_korean_indices

    # 1) 네이버 증권 시도
    naver_results = []
    try:
        naver_results = fetch_korean_indices(tickers, names)
        logger.info("[KR] 네이버 증권: %d/%d 수집 성공", len(naver_results), len(tickers))
    except Exception as e:
        logger.warning("[KR] 네이버 증권 전체 실패: %s — yfinance 폴백", e)

    # 2) 네이버에서 수집 못한 티커만 yfinance로 폴백
    naver_tickers = {r["ticker"] for r in naver_results}
    remaining = [
        (t, n) for t, n in zip(tickers, names) if t not in naver_tickers
    ]

    yf_results: list[dict[str, Any]] = []
    if remaining:
        rem_tickers, rem_names = zip(*remaining)
        logger.info("[KR] yfinance 폴백: %s", list(rem_tickers))
        yf_results = _collect_section(list(rem_tickers), list(rem_names))

    # 3) 합치고 원래 순서 유지
    all_results = naver_results + yf_results
    order = {t: i for i, t in enumerate(tickers)}
    all_results.sort(key=lambda item: order.get(item["ticker"], 999))

    return all_results


def collect_market_window_data(
    config: dict[str, Any],
    start_date: str,
    end_date: str,
) -> dict[str, list[dict[str, Any]]]:
    """Collect historical market points for the requested weekly window."""
    mkts = config.get("markets", {})
    output: dict[str, list[dict[str, Any]]] = {}

    sections = [
        ("kr", "indices", "names"),
        ("us", "indices", "names"),
        ("fx", "pairs", "names"),
        ("commodities", "tickers", "names"),
        ("crypto", "tickers", "names"),
        ("risk", "tickers", "names"),
        ("sectors", "tickers", "names"),
    ]

    for section_key, tickers_field, names_field in sections:
        section_cfg = mkts.get(section_key, {})
        tickers = list(section_cfg.get(tickers_field, []))
        names = list(section_cfg.get(names_field, []))

        if not tickers:
            output[section_key] = []
            continue

        if len(names) < len(tickers):
            names = names + tickers[len(names):]

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=len(tickers) or 1) as pool:
            future_to_meta = {
                pool.submit(_fetch_one_ticker_window, ticker, name, start_date, end_date): (ticker, name)
                for ticker, name in zip(tickers, names)
            }
            for future in as_completed(future_to_meta):
                result = future.result()
                if result is not None:
                    results.append(result)

        order = {ticker: i for i, ticker in enumerate(tickers)}
        results.sort(key=lambda item: order.get(item.get("ticker", ""), 999))
        output[section_key] = results

    return output
