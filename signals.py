"""
신호 개선 모듈 — 더블 시그널

시그널 1 (기술적): 거래량 급증 + 모멘텀 (backtest.py 기반)
시그널 2 (수급):   KIS API 장중 외국인/기관 순매수

두 시그널이 겹치는 종목만 최종 매수 후보로 선정.

사용법:
    from signal import get_double_signals
    candidates = get_double_signals()
"""

import time, logging
from datetime import datetime, date, timedelta

import pandas as pd
import FinanceDataReader as fdr
import requests
from dotenv import load_dotenv

import kis_api
from backtest import add_features, generate_signals, Cfg

load_dotenv()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# 전략 설정
# ─────────────────────────────────────────
CFG = Cfg()
CFG.volume_mult    = 2.0
CFG.day_return_min = 0.005
CFG.stop_loss      = -0.07
CFG.take_profit    = 0.10
CFG.hold_days      = 10

# 외국인 순매수 최소 기준 (원)
FOREIGN_MIN = 5_000_000_000    # 50억
INST_MIN    = 0                # 기관은 양수면 OK (0 이상)


# ─────────────────────────────────────────
# KIS API — 종목별 투자자 순매수 (장중)
# ─────────────────────────────────────────
def get_investor_trading(ticker: str) -> dict:
    """
    KIS API: 종목별 투자자 매매동향 (당일 누적)
    반환: {외국인: 순매수금액, 기관: 순매수금액}
    """
    token = kis_api.get_token()
    tr_id = "FHKST01010900"  # 투자자별 매매동향

    resp = requests.get(
        f"{kis_api.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor",
        headers={
            "authorization": f"Bearer {token}",
            "appkey":        kis_api.APP_KEY,
            "appsecret":     kis_api.APP_SECRET,
            "tr_id":         tr_id,
        },
        params={
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd":         ticker,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("rt_cd") != "0":
        logger.warning("%s 투자자 데이터 없음: %s", ticker, data.get("msg1"))
        return {"foreign": 0, "institution": 0}

    output = data.get("output", [])
    if not output:
        return {"foreign": 0, "institution": 0}

    # 당일 (첫 번째 행) 외국인/기관 순매수 금액
    today_row = output[0]
    foreign = int(today_row.get("frgn_ntby_qty", 0))      # 외국인 순매수 수량
    inst    = int(today_row.get("orgn_ntby_qty", 0))       # 기관 순매수 수량

    # 현재가로 금액 환산
    try:
        price_info = kis_api.get_price(ticker)
        cur_price  = int(price_info.get("stck_prpr", 0))
        foreign_amt = foreign * cur_price
        inst_amt    = inst * cur_price
    except Exception:
        foreign_amt = foreign * 10000   # 가격 조회 실패 시 만원 단위 추정
        inst_amt    = inst * 10000

    return {"foreign": foreign_amt, "institution": inst_amt}


# ─────────────────────────────────────────
# 시그널 1: 기술적 (거래량 + 모멘텀)
# ─────────────────────────────────────────
def get_technical_signals(top_n: int = 200) -> list[str]:
    """전일 기준 거래량 급증 + 모멘텀 종목 코드 리스트"""
    kospi = fdr.StockListing("KOSPI")
    kospi = kospi[kospi["Marcap"] > 0].sort_values("Marcap", ascending=False).head(top_n)
    tickers = kospi["Code"].tolist()
    names   = dict(zip(kospi["Code"], kospi["Name"]))

    frames = []
    for code in tickers:
        try:
            df = fdr.DataReader(code, periods=30)
            if df is None or df.empty:
                continue
            df = df.reset_index().rename(columns={
                "Date":"date","Open":"open","High":"high",
                "Low":"low","Close":"close","Volume":"volume"
            })
            df["ticker"] = code
            df["name"]   = names.get(code, "")
            df["date"]   = pd.to_datetime(df["date"])
            frames.append(df[["date","ticker","name","open","high","low","close","volume"]])
        except Exception:
            pass
        time.sleep(0.1)

    if not frames:
        return []

    ohlcv   = pd.concat(frames, ignore_index=True)
    ohlcv   = add_features(ohlcv, CFG)
    signals = generate_signals(ohlcv, CFG)

    # 전일 신호만
    latest = ohlcv["date"].max()
    today_sigs = signals[signals["date"] == latest]

    logger.info("기술적 시그널 %d종목 (기준일: %s)", len(today_sigs), latest.date())
    return today_sigs[["ticker", "name", "vol_ratio", "day_return"]].to_dict("records")


# ─────────────────────────────────────────
# 시그널 2: 수급 (외국인 순매수)
# ─────────────────────────────────────────
def get_foreign_buy_signals(tickers: list[str],
                            foreign_min: int = FOREIGN_MIN) -> set[str]:
    """
    KIS API로 당일 외국인 순매수 금액 조회
    foreign_min 이상인 종목만 반환
    """
    buy_set = set()

    for ticker in tickers:
        try:
            data = get_investor_trading(ticker)
            if data["foreign"] >= foreign_min:
                buy_set.add(ticker)
                logger.info("외국인 순매수  %s  %+.1f억원",
                            ticker, data["foreign"] / 1e8)
            time.sleep(0.2)   # API 호출 제한 방지
        except Exception as e:
            logger.debug("%s 수급 조회 실패: %s", ticker, e)

    logger.info("수급 시그널 %d종목 (외국인 순매수 %.0f억 이상)",
                len(buy_set), foreign_min / 1e8)
    return buy_set


# ─────────────────────────────────────────
# 더블 시그널 — 최종 매수 후보
# ─────────────────────────────────────────
def get_double_signals(use_foreign: bool = True,
                       foreign_min: int = FOREIGN_MIN) -> list[dict]:
    """
    시그널 1 AND 시그널 2 교집합 종목 반환

    use_foreign=False 이면 기술적 시그널만 사용
    (API 키 없거나 장 전에 사용 시)
    """
    tech_sigs = get_technical_signals()
    if not tech_sigs:
        logger.warning("기술적 시그널 없음")
        return []

    if not use_foreign:
        logger.info("수급 필터 비활성 — 기술적 시그널만 사용")
        return tech_sigs

    tech_tickers = [s["ticker"] for s in tech_sigs]
    foreign_set  = get_foreign_buy_signals(tech_tickers, foreign_min)

    # 교집합
    double = [s for s in tech_sigs if s["ticker"] in foreign_set]

    logger.info("기술적 %d개 ∩ 외국인순매수 %d개 = 최종 %d개",
                len(tech_sigs), len(foreign_set), len(double))
    return double


# ─────────────────────────────────────────
# CLI 테스트
# ─────────────────────────────────────────
if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    has_key = bool(os.getenv("KIS_APP_KEY", ""))

    print("\n=== 더블 시그널 테스트 ===")
    if has_key:
        print("KIS API 키 감지됨 — 외국인 수급 필터 활성화")
        candidates = get_double_signals(use_foreign=True)
    else:
        print("⚠ KIS API 키 없음 — 기술적 시그널만 사용")
        candidates = get_double_signals(use_foreign=False)

    print(f"\n최종 매수 후보: {len(candidates)}종목")
    for c in candidates:
        print(f"  {c['ticker']}  {c['name']:<10}  "
              f"거래량{c['vol_ratio']:.1f}배  당일수익{c['day_return']*100:+.2f}%")
