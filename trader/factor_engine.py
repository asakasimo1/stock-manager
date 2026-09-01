"""
멀티팩터 엔진 — Quantus 스타일 장기 팩터 투자

팩터 구성 (가중치):
  - 가치(Value)    40% : PBR 하위 + PER 하위 (저평가)
  - 모멘텀(Mom)   35% : 12개월 수익률 상위 (추세 추종)
  - 퀄리티(Quality) 25%: ROE 상위 (수익성 우량)

데이터 출처: pykrx (별도 API 키 불필요)
유니버스: KOSPI 200
"""

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pykrx import stock

logger = logging.getLogger(__name__)

# ── 팩터 가중치 ──────────────────────────────
W_VALUE    = 0.40   # PBR + PER 복합
W_MOMENTUM = 0.35   # 12개월 모멘텀
W_QUALITY  = 0.25   # ROE (EPS/BPS)

# ── 유니버스 ─────────────────────────────────
MIN_PER = 0.1    # 적자기업(음수 PER) 제외
MAX_PER = 60     # 과열 PER 제외
MAX_PBR = 5.0    # 극단적 고평가 제외
MIN_ROE = 0.0    # 적자 ROE 제외

TOP_N = 15       # 선택 종목 수


def _trading_day(offset_days: int = 0) -> str:
    """오늘 기준 영업일 근사치 (주말 보정)"""
    d = datetime.today() - timedelta(days=abs(offset_days))
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def get_kospi200_tickers() -> list[str]:
    """KOSPI 200 구성 종목 티커 목록 (당일 데이터 없으면 전일 사용)"""
    for offset in (0, 1, 2):
        date_str = _trading_day(offset)
        try:
            tickers = stock.get_index_portfolio_deposit_file("1028", date_str)
            if tickers:
                return tickers
        except Exception as e:
            logger.warning("KOSPI200 조회 실패 (%s): %s", date_str, e)

    # 모두 실패 시 전체 KOSPI로 폴백
    logger.warning("KOSPI200 구성 조회 실패 — 전체 KOSPI 사용")
    for offset in (0, 1, 2):
        try:
            return stock.get_market_ticker_list(_trading_day(offset), market="KOSPI")
        except Exception:
            pass
    return []


def fetch_fundamentals(date_str: str) -> pd.DataFrame:
    """pykrx로 PER·PBR·EPS·BPS 수집.
    당일 장중에는 데이터가 없으므로 빈 DataFrame 시 전일로 재시도."""
    REQUIRED_COLS = {"BPS", "PER", "PBR", "EPS"}
    df = pd.DataFrame()

    for d_str in (date_str, _trading_day(1), _trading_day(2)):
        try:
            raw = stock.get_market_fundamental_by_ticker(d_str, market="KOSPI")
            if not raw.empty and REQUIRED_COLS.issubset(set(raw.columns)):
                df = raw
                if d_str != date_str:
                    logger.info("펀더멘털 데이터: %s → %s로 폴백", date_str, d_str)
                break
        except Exception as e:
            logger.warning("펀더멘털 조회 실패 (%s): %s", d_str, e)

    if df.empty:
        logger.error("펀더멘털 데이터 수집 불가 — 빈 DataFrame 반환")
        return pd.DataFrame(columns=["ticker", "BPS", "PER", "PBR", "EPS", "DIV", "DPS", "ROE"])

    # columns: BPS, PER, PBR, EPS, DIV, DPS
    df.index.name = "ticker"
    df = df.reset_index()
    # ROE 근사: EPS / BPS
    df["ROE"] = np.where(
        (df["BPS"] > 0) & (df["EPS"] > 0),
        df["EPS"] / df["BPS"] * 100,
        np.nan
    )
    return df


def fetch_momentum(tickers: list[str]) -> pd.DataFrame:
    """12개월 주가 수익률 계산"""
    end   = _trading_day(0)
    start = _trading_day(252)   # 약 1년 영업일

    try:
        prices = stock.get_market_ohlcv_by_ticker(end, market="KOSPI")[["종가"]]
        prices_1y = stock.get_market_ohlcv_by_ticker(start, market="KOSPI")[["종가"]]

        prices.index.name = "ticker"
        prices_1y.index.name = "ticker"

        prices   = prices.reset_index().rename(columns={"종가": "price_now"})
        prices_1y = prices_1y.reset_index().rename(columns={"종가": "price_1y"})

        merged = prices.merge(prices_1y, on="ticker", how="inner")
        merged["momentum_12m"] = (merged["price_now"] - merged["price_1y"]) / merged["price_1y"]
        return merged[["ticker", "price_now", "momentum_12m"]]
    except Exception as e:
        logger.error("모멘텀 데이터 수집 실패: %s", e)
        return pd.DataFrame(columns=["ticker", "price_now", "momentum_12m"])


def _percentile_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    """백분위 순위 (0~1). ascending=True이면 낮을수록 높은 점수"""
    ranked = series.rank(ascending=ascending, pct=True, na_option="bottom")
    return ranked


def score_factors(universe: list[str]) -> pd.DataFrame:
    """
    유니버스 종목에 대해 멀티팩터 스코어 계산 후 상위 TOP_N 반환

    Returns:
        DataFrame with columns:
          ticker, name, score, pbr, per, roe, momentum_12m,
          value_score, quality_score, momentum_score
    """
    today = _trading_day(0)
    logger.info("팩터 데이터 수집 시작 — 기준일: %s", today)

    # 1. 펀더멘털
    fund = fetch_fundamentals(today)
    logger.info("펀더멘털 %d종목 수집", len(fund))

    # 2. 모멘텀
    mom = fetch_momentum(universe)
    logger.info("모멘텀 %d종목 수집", len(mom))

    # 3. 종목명
    names = {}
    try:
        name_df = stock.get_market_ticker_list(today, market="KOSPI")
        names = {t: stock.get_market_ticker_name(t) for t in universe[:5]}  # 샘플
        # 전체 종목명
        all_names = {t: stock.get_market_ticker_name(t) for t in universe}
        names = all_names
    except Exception:
        names = {}

    # 4. 병합
    df = pd.DataFrame({"ticker": universe})
    df = df.merge(fund[["ticker","PER","PBR","ROE"]], on="ticker", how="left")
    df = df.merge(mom[["ticker","price_now","momentum_12m"]], on="ticker", how="left")
    df["name"] = df["ticker"].map(names).fillna("")

    # 5. 필터링
    before = len(df)
    df = df[
        (df["PER"]  >= MIN_PER) &
        (df["PER"]  <= MAX_PER) &
        (df["PBR"]  <= MAX_PBR) &
        (df["ROE"]  >= MIN_ROE) &
        (df["price_now"] > 0)
    ].copy()
    logger.info("필터 후 %d → %d종목", before, len(df))

    if len(df) < 5:
        logger.warning("종목 수 부족: %d", len(df))
        return pd.DataFrame()

    # 6. 팩터 스코어 (백분위 순위)
    df["pbr_rank"]  = _percentile_rank(df["PBR"], ascending=True)   # 낮을수록 좋음
    df["per_rank"]  = _percentile_rank(df["PER"], ascending=True)   # 낮을수록 좋음
    df["roe_rank"]  = _percentile_rank(df["ROE"], ascending=False)  # 높을수록 좋음
    df["mom_rank"]  = _percentile_rank(df["momentum_12m"], ascending=False)  # 높을수록 좋음

    df["value_score"]    = (df["pbr_rank"] + df["per_rank"]) / 2
    df["quality_score"]  = df["roe_rank"]
    df["momentum_score"] = df["mom_rank"]

    df["score"] = (
        W_VALUE    * df["value_score"]  +
        W_MOMENTUM * df["momentum_score"] +
        W_QUALITY  * df["quality_score"]
    )

    # 7. 상위 TOP_N 선택
    top = df.nlargest(TOP_N, "score").reset_index(drop=True)

    logger.info("멀티팩터 상위 %d종목 선정 완료", len(top))
    for _, r in top.iterrows():
        logger.info("  %s %s  점수%.3f  PBR%.2f  PER%.1f  ROE%.1f%%  Mom%+.1f%%",
                    r["ticker"], r["name"][:6], r["score"],
                    r["PBR"], r["PER"], r["ROE"], r["momentum_12m"]*100)

    return top[[
        "ticker","name","score",
        "PBR","PER","ROE","momentum_12m",
        "value_score","quality_score","momentum_score","price_now"
    ]].rename(columns={"PBR":"pbr","PER":"per","ROE":"roe"})
