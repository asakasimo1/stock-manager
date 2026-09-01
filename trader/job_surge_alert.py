"""
급등 포착 알림 전용 — 매매 없이 감시만 하고 텔레그램으로 알림.

2026-08-25 신설 — 사용자가 초단타 자동매매(scalp)를 수익률 저조로 꺼두고,
대신 "직접 판단해서 대응할 테니 급등만 감지해서 알려달라"고 요청. 코인/주식
어느 쪽도 실제 주문을 넣지 않는 순수 감시 스크립트 — scalp_engine의 가격
이력 추적 기능만 재사용하고 매수/매도 로직은 전혀 없음.

조건: 유동성(거래대금) 상위 20% 이내 + 최근 60초간 3% 이상 급등.
  - 시가총액이 이상적이지만 Upbit API가 시가총액(유통량×가격)을 제공하지
    않아, 이미 초단타 자동발굴에 쓰던 "거래대금(거래량×가격) 상위 %" 방식을
    그대로 재사용(사용자 승인, 2026-08-25) — 거래대금 큰 코인은 대체로
    시가총액도 크다는 상관관계에 기댐.
  - 주식은 KIS 등락률 순위(당일 상승률 상위 top_n) 안에서만 거래대금 상위
    20%를 가림 — 당일 기준 하락 중인데 순간적으로만 3% 튄 종목은 이 방식으로는
    못 잡음(KIS API로 전체 종목 실시간 스캔은 종목 수만큼 개별 조회가 필요해
    비용이 큼). 필요해지면 나중에 보완.

쿨다운: 같은 종목 재알림은 COOLDOWN_SEC(기본 10분) 지나야 다시 보냄 — 급등이
한동안 지속돼도 매 사이클(20초)마다 알림이 반복되는 스팸을 방지.
"""
import time
import logging
from datetime import datetime, timezone, timedelta

import upbit_api
import kis_api
import scalp_engine
import notify

KST = timezone(timedelta(hours=9))
logging.Formatter.converter = lambda *a: datetime.now(KST).timetuple()
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("surge_alert.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s KST %(levelname)s %(message)s"))
logger.addHandler(_fh)

SURGE_PCT           = 3.0   # 급등 판정 기준(%)
LOOKBACK_SEC         = 60   # "1분간" 판정 구간(초)
TOP_LIQUIDITY_PCT    = 0.2  # 유동성(거래대금) 상위 비율 — 시가총액 상위 20% 대체
COOLDOWN_SEC         = 600  # 같은 종목 재알림 최소 간격(초)
POLL_INTERVAL_SEC    = 20   # 감시 주기(초) — 매매가 없어 스캘핑보다 여유있게 잡음
STOCK_RANKING_TOP_N  = 200  # KIS 등락률 순위에서 가져올 후보 폭

_last_alert: dict = {}   # ticker -> 마지막 알림 epoch


def _should_alert(ticker: str, now: float) -> bool:
    last = _last_alert.get(ticker, 0)
    if now - last < COOLDOWN_SEC:
        return False
    _last_alert[ticker] = now
    return True


def check_coins(now_epoch: float) -> None:
    try:
        tickers = upbit_api.get_all_krw_markets()
        prices = upbit_api.get_prices(tickers)
    except Exception as e:
        logger.warning("코인 시세 조회 실패: %s", e)
        return

    liquidity_ranked = []
    for ticker, info in prices.items():
        price = info.get("price", 0)
        vol = info.get("volume", 0)
        scalp_engine.record_price(ticker, price, now=now_epoch)
        scalp_engine.record_volume(ticker, vol, now=now_epoch)
        liquidity_ranked.append((ticker, vol * price))

    liquidity_ranked.sort(key=lambda x: -x[1])
    cutoff = max(1, int(len(liquidity_ranked) * TOP_LIQUIDITY_PCT))
    top_tickers = [t for t, _ in liquidity_ranked[:cutoff]]

    for ticker in top_tickers:
        chg = scalp_engine.momentum_pct(ticker, LOOKBACK_SEC, now=now_epoch)
        if chg is None or chg < SURGE_PCT:
            continue
        if not _should_alert(ticker, now_epoch):
            continue
        name = upbit_api.COIN_NAMES.get(ticker, ticker)
        price = prices.get(ticker, {}).get("price", 0)
        notify.send(
            f"🚀 <b>코인 급등 포착</b>  {name} ({ticker})\n"
            f"  최근 {LOOKBACK_SEC}초간 +{chg:.2f}%  현재가 {price:,.0f}원\n"
            f"  거래대금 상위 {int(TOP_LIQUIDITY_PCT*100)}% 이내 (감시 전용, 매매 없음)"
        )
        logger.info("★ 코인 급등 알림: %s(%s) +%.2f%%", name, ticker, chg)


def check_stocks(now_epoch: float) -> None:
    if not kis_api.is_any_market_open():
        return
    try:
        ranking = kis_api.get_fluctuation_ranking(top_n=STOCK_RANKING_TOP_N, sort="gainers")
    except Exception as e:
        logger.warning("주식 등락률 순위 조회 실패: %s", e)
        return

    scored = [(row, row["acml_vol"] * row["price"]) for row in ranking]
    scored.sort(key=lambda x: -x[1])
    cutoff = max(1, int(len(scored) * TOP_LIQUIDITY_PCT))
    top_rows = [row for row, _ in scored[:cutoff]]

    for row in top_rows:
        ticker = row["ticker"]
        scalp_engine.record_price(ticker, row["price"], now=now_epoch)
        chg = scalp_engine.momentum_pct(ticker, LOOKBACK_SEC, now=now_epoch)
        if chg is None or chg < SURGE_PCT:
            continue
        if not _should_alert(ticker, now_epoch):
            continue
        notify.send(
            f"🚀 <b>주식 급등 포착</b>  {row['name']} ({ticker})\n"
            f"  최근 {LOOKBACK_SEC}초간 +{chg:.2f}%  현재가 {row['price']:,.0f}원\n"
            f"  거래대금 상위 {int(TOP_LIQUIDITY_PCT*100)}% 이내 (등락률 상위 {len(ranking)}종목 중, 감시 전용, 매매 없음)"
        )
        logger.info("★ 주식 급등 알림: %s(%s) +%.2f%%", row["name"], ticker, chg)


def main() -> None:
    logger.info("=" * 50)
    logger.info("  급등 포착 알림 데몬 시작 — 매매 없음, 감시 전용")
    logger.info("  조건: 거래대금 상위 %d%%, 최근 %d초간 +%.1f%% 이상", int(TOP_LIQUIDITY_PCT*100), LOOKBACK_SEC, SURGE_PCT)
    logger.info("=" * 50)
    while True:
        now_epoch = time.time()
        try:
            check_coins(now_epoch)
        except Exception as e:
            logger.error("코인 감시 오류: %s", e)
        try:
            check_stocks(now_epoch)
        except Exception as e:
            logger.error("주식 감시 오류: %s", e)
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("데몬 종료")
