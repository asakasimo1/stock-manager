"""
매수 후보 스크리닝 모듈
──────────────────────────────────────────────────────────────────
[스크리닝 로직]
1. 네이버 금융 거래량 상위 페이지에서 KOSPI/KOSDAQ 상위 티커 수집
   (pykrx 전체 시장 API가 불안정하여 네이버 스크래핑으로 대체)
2. 각 티커의 60일 OHLCV 다운로드 (pykrx 개별 종목 API — 안정적)
3. 전일 양봉 조건 (종가 > 시가) 필터
4. 거래량 급증 조건 (20일 평균 대비) 필터
5. 장대양봉 조건 (몸통비율) 필터
6. 기술적 분석 + 외국인/기관 수급 분석
7. 종합 스코어링 → TOP N 추천

[스코어 구성]  총합 기준 정렬
- 기술적 점수     : technical.py score (−100~+100)
- 장대양봉 보너스 : 몸통비율 × 25       (최대 +25)
- 거래량 급증     : vol_ratio × 4        (최대 +30, 7.5배 cap)
- 거래대금 급증   : amount_ratio × 3     (최대 +25, 8.3배 cap)
- 연속 양봉       : 연속 수 × 5          (최대 +15, 3일 cap)
- 갭업            : +10 (시가 > 전일 종가)
- 외국인 순매수   : +15
- 기관 순매수     : +10
- 미국 시황 반영  : +10 (상승) / −10 (하락)
"""
from __future__ import annotations

import sys
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import requests
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

try:
    from pykrx import stock as krx
except ImportError:
    krx = None

import config
from modules import technical


# ──────────────────────────────────────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────────────────────────────────────

def _last_trading_date(offset: int = 1) -> str:
    """offset=1 → 전 거래일, 0 → 오늘(또는 직전 거래일)"""
    d = datetime.today() - timedelta(days=offset)
    # 주말 건너뜀
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    # 추가: 월요일에 offset=1이면 금요일이 나올 수 있음 — 한번 더 체크
    # (공휴일은 pykrx에서 빈 데이터로 자동 처리)
    return d.strftime("%Y%m%d")


def _safe_download(ticker: str, fromdate: str, todate: str) -> pd.DataFrame:
    """pykrx OHLCV + 컬럼 표준화"""
    try:
        df = krx.get_market_ohlcv(fromdate, todate, ticker)
        if df is None or df.empty:
            return pd.DataFrame()
        rename = {"시가": "Open", "고가": "High", "저가": "Low",
                  "종가": "Close", "거래량": "Volume"}
        df = df.rename(columns=rename)
        df.index = pd.to_datetime(df.index)
        needed = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[needed].copy()
        df = df[df["Volume"] > 0].dropna()
        return df
    except Exception:
        return pd.DataFrame()


def _get_top_tickers_by_market_cap(market: str, top_n: int = 100) -> list:
    """
    pykrx로 시가총액 상위 N위 티커 반환.
    market: 'KOSPI' | 'KOSDAQ'
    """
    try:
        date = _last_trading_date(0)
        df = krx.get_market_cap_by_ticker(date, market=market)
        if df is None or df.empty:
            return []
        cap_col = next((c for c in df.columns if "시가총액" in str(c)), None)
        if cap_col:
            df = df.sort_values(cap_col, ascending=False)
        return list(df.index[:top_n])
    except Exception as e:
        print(f"[buy_signal] 시가총액 조회 실패 ({market}): {e}")
        return []


def _get_stock_name(ticker: str) -> str:
    try:
        name = krx.get_market_ticker_name(ticker)
        if isinstance(name, str) and name:
            return name
    except Exception:
        pass
    return ticker


def _body_ratio(row) -> float:
    """양봉 몸통 비율 = (종가-시가) / (고가-저가)"""
    high_low = row["High"] - row["Low"]
    if high_low <= 0:
        return 0.0
    body = row["Close"] - row["Open"]
    return max(0.0, body / high_low)


def _consecutive_bullish(df: pd.DataFrame) -> int:
    """최근 연속 양봉 수 (최대 5)"""
    count = 0
    for i in range(len(df) - 1, max(len(df) - 6, -1), -1):
        row = df.iloc[i]
        if row["Close"] > row["Open"]:
            count += 1
        else:
            break
    return count


# ──────────────────────────────────────────────────────────────────────────────
# 수급 분석
# ──────────────────────────────────────────────────────────────────────────────

def _get_supply_demand(ticker: str, fromdate: str, todate: str) -> dict:
    """
    외국인/기관 수급 분석
    반환: {
        'foreign_net': int,   # 외국인 순매수 (+ 순매수, - 순매도)
        'inst_net':    int,   # 기관 순매수
        'indiv_net':   int,   # 개인 순매수
        'foreign_trend': str, # '순매수' / '순매도' / '보합'
        'inst_trend':   str,
    }
    """
    empty = {"foreign_net": 0, "inst_net": 0, "indiv_net": 0,
             "foreign_trend": "보합", "inst_trend": "보합"}
    try:
        time.sleep(0.2)  # KRX API 레이트 리밋 방지
        df = krx.get_market_trading_value_by_date(fromdate, todate, ticker)
        if df is None or df.empty:
            return empty

        # 컬럼명은 버전마다 다를 수 있어 유연하게 처리
        col_map = {}
        for col in df.columns:
            if "외국인" in str(col) and ("순" in str(col) or "합계" in str(col)):
                col_map["foreign"] = col
            elif "기관" in str(col) and ("합계" in str(col) or "순" in str(col)):
                col_map["inst"] = col
            elif "개인" in str(col):
                col_map["indiv"] = col

        # 없으면 순서로 추정 (개인, 외국인, 기관 순서 일반적)
        cols = list(df.columns)
        if "foreign" not in col_map and len(cols) >= 2:
            col_map["foreign"] = cols[1]
        if "inst" not in col_map and len(cols) >= 3:
            col_map["inst"] = cols[2]
        if "indiv" not in col_map and len(cols) >= 1:
            col_map["indiv"] = cols[0]

        result = {}
        for key, col in col_map.items():
            try:
                result[f"{key}_net"] = int(df[col].sum())
            except Exception:
                result[f"{key}_net"] = 0

        f_net = result.get("foreign_net", 0)
        i_net = result.get("inst_net", 0)
        result["foreign_trend"] = "순매수" if f_net > 0 else ("순매도" if f_net < 0 else "보합")
        result["inst_trend"]    = "순매수" if i_net > 0 else ("순매도" if i_net < 0 else "보합")
        result.setdefault("indiv_net", 0)
        return result
    except Exception:
        return empty


# ──────────────────────────────────────────────────────────────────────────────
# 스코어링
# ──────────────────────────────────────────────────────────────────────────────

def _calc_score(
    df: pd.DataFrame,
    tech: dict,
    supply: dict,
    us_market: dict,
    vol_ratio: float,
    amount_ratio: float,
) -> int:
    """종합 매수 점수 계산"""
    score = tech.get("score", 0) if tech else 0

    last_row = df.iloc[-1]
    prev_row = df.iloc[-2] if len(df) > 1 else last_row

    # 장대양봉 보너스 (몸통비율 × 25)
    br = _body_ratio(last_row)
    score += int(br * 25)

    # 거래량 급증 보너스 (cap: 7.5배 → +30)
    vol_bonus = min(vol_ratio * 4, 30)
    score += int(vol_bonus)

    # 거래대금 급증 보너스 (cap: 8.3배 → +25)
    amt_bonus = min(amount_ratio * 3, 25)
    score += int(amt_bonus)

    # 연속 양봉 보너스 (cap: 3일 → +15)
    consec = _consecutive_bullish(df)
    score += min(consec * 5, 15)

    # 갭업 보너스
    if last_row["Open"] > prev_row["Close"]:
        score += 10

    # 외국인 수급
    if supply.get("foreign_trend") == "순매수":
        score += 15
    elif supply.get("foreign_trend") == "순매도":
        score -= 10

    # 기관 수급
    if supply.get("inst_trend") == "순매수":
        score += 10
    elif supply.get("inst_trend") == "순매도":
        score -= 5

    # 미국 시황 반영
    if us_market:
        summary = us_market.get("summary", "")
        if "전반 상승" in summary:
            score += 10
        elif "전반 하락" in summary:
            score -= 10

    return score


# ──────────────────────────────────────────────────────────────────────────────
# 메인 스크리닝 함수
# ──────────────────────────────────────────────────────────────────────────────

def _analyze_ticker(
    ticker: str,
    end_date: str,
    start_date: str,
    us_market: dict,
    include_supply: bool,
    min_vol_ratio: float,
    min_body_ratio: bool,  # False = 필터 안 함 (관심종목 모드)
) -> dict | None:
    """단일 종목 OHLCV 다운로드 → 필터 → 분석 → dict 반환 (실패 시 None)"""
    df = _safe_download(ticker, start_date, end_date)
    if df.empty or len(df) < 10:
        return None

    last_row = df.iloc[-1]
    prev_row = df.iloc[-2] if len(df) > 1 else last_row

    close   = float(last_row["Close"])
    prev_cl = float(prev_row["Close"])
    chg_pct = (close - prev_cl) / prev_cl * 100 if prev_cl else 0

    open_price = float(last_row["Open"])
    is_bullish = close > open_price
    body_pct   = (close - open_price) / open_price * 100 if open_price else 0  # 음수=음봉

    # 거래량 비율
    avg_vol  = float(df["Volume"].iloc[:-1].tail(20).mean()) if len(df) > 1 else 1
    last_vol = float(last_row["Volume"])
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0

    # 거래대금 비율
    df["Amount"] = df["Volume"] * df["Close"]
    avg_amt  = float(df["Amount"].iloc[:-1].tail(20).mean()) if len(df) > 1 else 1
    last_amt = float(last_vol * close)
    amount_ratio = last_amt / avg_amt if avg_amt > 0 else 1.0

    br = _body_ratio(last_row)

    # 필터 (full scan 모드일 때만 적용)
    if min_body_ratio:
        # 음봉 -1% 초과 제외 (양봉 + -1%까지 허용)
        if body_pct < -1.0:
            return None
        if vol_ratio < min_vol_ratio:
            return None
        # 장대양봉 비율: 양봉일 때만 체크 (음봉 허용 구간은 skip)
        if is_bullish and br < min_body_ratio:
            return None

    tech = technical.calculate(df)
    if not tech:
        return None

    supply = {}
    if include_supply:
        sup_start = (datetime.today() - timedelta(days=5)).strftime("%Y%m%d")
        supply = _get_supply_demand(ticker, sup_start, end_date)

    consec = _consecutive_bullish(df)
    gap_up = bool(last_row["Open"] > prev_row["Close"])
    score  = _calc_score(df, tech, supply, us_market or {}, vol_ratio, amount_ratio)

    signals    = tech.get("signals", {})
    rsi_series = tech.get("rsi")
    rsi_val    = float(rsi_series.iloc[-1]) if rsi_series is not None and not rsi_series.empty else None

    ma_dict    = tech.get("ma", {})
    ma_summary = []
    for p in [5, 20, 60]:
        if p in ma_dict and not ma_dict[p].empty:
            ma_v = float(ma_dict[p].iloc[-1])
            if not np.isnan(ma_v):
                arrow = "↑" if close > ma_v else "↓"
                ma_summary.append(f"MA{p}{arrow}")

    name = _get_stock_name(ticker)

    return {
        "ticker":         ticker,
        "name":           name,
        "close":          close,
        "chg_pct":        round(chg_pct, 2),
        "score":          score,
        "tech_score":     tech.get("score", 0),
        "vol_ratio":      round(vol_ratio, 1),
        "amount_ratio":   round(amount_ratio, 1),
        "body_ratio":     round(br, 2),
        "consec_bullish": consec,
        "gap_up":         gap_up,
        "is_bullish":     is_bullish,
        "supply":         supply,
        "rsi":            round(rsi_val, 1) if rsi_val else None,
        "macd_sig":       signals.get("MACD", ""),
        "bb_sig":         signals.get("볼린저밴드", ""),
        "ma_summary":     " ".join(ma_summary),
        "tech_signals":   signals,
    }


def scan(
    us_market: dict = None,
    markets: list = None,
    top_volume: int = 100,
    min_amount_billion: float = 30.0,
    min_vol_ratio: float = 1.5,
    min_body_ratio: float = 0.4,
    include_supply: bool = True,
    top_n: int = 10,
    progress_cb=None,
) -> list:
    """
    KOSPI/KOSDAQ 거래량 상위 종목 스크리닝 후 매수 추천 반환.
    네이버 금융 거래량 상위 페이지에서 티커를 수집하여 분석합니다.
    """
    if krx is None:
        return []

    markets = markets or ["KOSPI", "KOSDAQ"]

    def _log(msg):
        if progress_cb:
            progress_cb(msg)

    # ── 1. 시가총액 상위 티커 수집 (KOSPI 100위 이내 + KOSDAQ 100위 이내) ────
    _log(f"[1/4] 시가총액 상위 티커 수집 중...")

    seen = set()
    tickers = []
    for mkt in markets:
        mkt_tickers = _get_top_tickers_by_market_cap(mkt, top_n=top_volume)
        _log(f"  {mkt} 시가총액 상위 {len(mkt_tickers)}위 수집")
        for t in mkt_tickers:
            if t not in seen:
                seen.add(t)
                tickers.append(t)

    if not tickers:
        _log("  ⚠ 티커 수집 실패 (pykrx API 확인 필요)")
        return []

    _log(f"  총 {len(tickers)}개 후보 티커 확보")

    # ── 2. 각 종목 OHLCV 다운로드 + 필터 + 분석 ────────────────────────────
    end_date   = _last_trading_date(0)
    start_date = (datetime.today() - timedelta(days=60)).strftime("%Y%m%d")
    total      = len(tickers)

    _log(f"[2/4] {total}개 종목 개별 분석 중 (양봉+거래량 조건 필터)...")

    results = []
    for i, ticker in enumerate(tickers, 1):
        if i % 10 == 0 or i == total:
            _log(f"  진행: {i}/{total}")
        result = _analyze_ticker(
            ticker, end_date, start_date, us_market,
            include_supply, min_vol_ratio, min_body_ratio,
        )
        if result:
            results.append(result)
        time.sleep(0.3)  # KRX API 레이트 리밋 방지

    # ── 3. 스코어 정렬 → TOP N ──────────────────────────────────────────────
    _log(f"[3/4] 필터 통과: {len(results)}개 → TOP {top_n} 선정...")
    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:top_n]

    _log(f"✅ 스크리닝 완료 — 후보 {len(results)}종목 중 TOP {len(top)} 선정")
    return top


# ──────────────────────────────────────────────────────────────────────────────
# 관심종목 기반 스캔 (빠른 버전)
# ──────────────────────────────────────────────────────────────────────────────

def scan_watchlist(
    watchlist: list,
    us_market: dict = None,
    include_supply: bool = True,
    progress_cb=None,
) -> list:
    """
    watchlist 종목만 스크리닝 (전체 시장 스캔보다 빠름, 필터 조건 없음)
    watchlist: [{"ticker": "005930", "name": "삼성전자"}, ...]
    """
    end_date   = _last_trading_date(0)
    start_date = (datetime.today() - timedelta(days=60)).strftime("%Y%m%d")

    def _log(msg):
        if progress_cb:
            progress_cb(msg)

    results = []
    for s in watchlist:
        ticker = s["ticker"]
        name   = s.get("name", ticker)
        _log(f"  {name} ({ticker}) 분석 중...")

        # min_body_ratio=False → 양봉/거래량 필터 적용 안 함 (전체 표시)
        result = _analyze_ticker(
            ticker, end_date, start_date, us_market,
            include_supply, min_vol_ratio=0, min_body_ratio=False,
        )
        if result:
            # 관심종목에서 지정한 이름 우선 사용
            result["name"] = name
            results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results
