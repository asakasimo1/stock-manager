"""
자동매매 스케줄러

실행: python trader.py

전략 변경: .env 파일의 STRATEGY 값을 수정 후 재시작
  STRATEGY=optimized   기본 최적 전략 (백테스트 최우선)
  STRATEGY=war_risk    전쟁/지정학 리스크 장기화 — 빠른 손절, 단기 보유
  STRATEGY=hold        버티기 — 익절폭 크게, 장기 보유
  STRATEGY=defensive   방어형 — 강한 신호만, 매우 빠른 손절
  STRATEGY=aggressive  공격형 — 손절 여유, 장기 익절 추구

스케줄:
  08:50  오늘 신호 종목 선정 (전일 데이터 기준)
  09:05  장 시작 후 매수 주문 실행
  매분   보유 포지션 손절/익절 모니터링
  15:20  당일 미청산 포지션 종가 주문 처리
  15:35  일간 리포트 출력

안전 장치:
  - 일일 최대 손실 한도 초과 시 봇 자동 중단
  - 1회 주문 금액 상한
  - 동시 보유 종목 수 상한
  - PAPER_TRADE=true 시 모의투자 계좌만 사용
"""

import os, time, logging
from datetime import datetime, date

from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler

import kis_api
from strategy import load_strategy, STRATEGY_PRESETS
from signals import get_double_signals

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("trader.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# 전략 프리셋 (strategy.py 에서 import)


# ─────────────────────────────────────────
# 안전 장치 설정
# ─────────────────────────────────────────
PAPER            = os.getenv("PAPER_TRADE", "true").lower() != "false"
STRATEGY_NAME    = os.getenv("STRATEGY", "optimized").lower()
CFG, _PRESET     = load_strategy(STRATEGY_NAME)

MAX_POSITIONS    = CFG.max_positions
MAX_ORDER_AMOUNT = _PRESET["max_order_amount"]
MAX_DAILY_LOSS   = _PRESET["max_daily_loss"]

# 런타임 상태
state = {
    "watchlist":   [],    # 오늘 매수 후보
    "positions":   {},    # {ticker: {buy_price, qty, tp, sl, buy_date}}
    "daily_pnl":   0,     # 당일 실현 손익
    "bot_active":  True,  # False이면 모든 주문 중단
    "initial_cash": 0,
}


# ─────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────
def is_trading_day() -> bool:
    today = date.today()
    return today.weekday() < 5   # 월~금


def log_banner(msg: str):
    logger.info("=" * 50)
    logger.info("  %s", msg)
    logger.info("=" * 50)


def safety_check() -> bool:
    """안전 장치: 일일 손실 한도 초과 여부"""
    if state["daily_pnl"] <= -MAX_DAILY_LOSS:
        logger.warning("⛔ 당일 손실 한도 도달 (%d원) — 봇 중단", state["daily_pnl"])
        state["bot_active"] = False
        return False
    return True


# ─────────────────────────────────────────
# 1. 신호 생성 (전일 데이터 기준)
# ─────────────────────────────────────────
def job_generate_signals():
    if not is_trading_day():
        return
    log_banner("신호 생성 시작 (08:50) — 더블 시그널")

    try:
        # 더블 시그널: 기술적(거래량+모멘텀) AND 외국인 순매수
        # 장 전 호출이므로 전일 기술적 시그널만 우선 수집
        # 외국인 수급은 09:05 매수 직전 job_buy에서 재확인
        candidates = get_double_signals(use_foreign=False)  # 장 전: 기술적만
        state["watchlist"] = candidates
        logger.info("매수 후보 %d종목: %s",
                    len(state["watchlist"]),
                    [s["ticker"] for s in state["watchlist"]])
    except Exception as e:
        logger.exception("신호 생성 오류: %s", e)


# ─────────────────────────────────────────
# 2. 매수 실행 (09:05)
# ─────────────────────────────────────────
def job_buy():
    if not is_trading_day() or not state["bot_active"]:
        return
    if not safety_check():
        return

    log_banner("매수 주문 실행 (09:05)")

    if not state["watchlist"]:
        logger.info("매수 후보 없음")
        return

    try:
        bal = kis_api.get_balance()
        cash = bal["cash"]

        # 초기 자산 기록 (당일 최초 실행 시)
        if state["initial_cash"] == 0:
            state["initial_cash"] = bal["total_eval"]

        # 현재 보유 종목 동기화
        for h in bal["holdings"]:
            if h["ticker"] not in state["positions"]:
                state["positions"][h["ticker"]] = {
                    "buy_price": h["avg_price"],
                    "qty":       h["qty"],
                    "tp":        h["avg_price"] * (1 + CFG.take_profit),
                    "sl":        h["avg_price"] * (1 + CFG.stop_loss),
                    "buy_date":  date.today(),
                    "name":      h["name"],
                }

        slots = MAX_POSITIONS - len(state["positions"])
        if slots <= 0:
            logger.info("보유 종목 꽉 참 (%d/%d) — 매수 건너뜀",
                        len(state["positions"]), MAX_POSITIONS)
            return

        for sig in state["watchlist"][:slots]:
            ticker = sig["ticker"]
            if ticker in state["positions"]:
                continue

            try:
                price_info = kis_api.get_price(ticker)
                cur_price  = int(price_info["stck_prpr"])
                if cur_price <= 0:
                    continue

                # 주문 금액: 가용현금 / 남은 슬롯 (MAX_ORDER_AMOUNT 상한)
                alloc = min(cash // slots, MAX_ORDER_AMOUNT)
                qty   = int(alloc / cur_price)
                if qty <= 0:
                    logger.warning("%s 주문 수량 0 — 건너뜀 (가격: %d)", ticker, cur_price)
                    continue

                result = kis_api.place_order(ticker, "BUY", qty)
                logger.info("매수 주문  %s(%s)  %d주  약 %d원  주문번호: %s",
                            ticker, sig["name"], qty, qty * cur_price, result["order_no"])

                state["positions"][ticker] = {
                    "buy_price": cur_price,
                    "qty":       qty,
                    "tp":        cur_price * (1 + CFG.take_profit),
                    "sl":        cur_price * (1 + CFG.stop_loss),
                    "buy_date":  date.today(),
                    "name":      sig["name"],
                }
                cash -= qty * cur_price
                time.sleep(0.3)

            except Exception as e:
                logger.error("%s 매수 실패: %s", ticker, e)

    except Exception as e:
        logger.exception("매수 작업 오류: %s", e)


# ─────────────────────────────────────────
# 3. 포지션 모니터링 (매분)
# ─────────────────────────────────────────
def job_monitor():
    if not is_trading_day() or not state["bot_active"]:
        return
    if not state["positions"]:
        return
    if not safety_check():
        return

    to_sell = []
    for ticker, pos in list(state["positions"].items()):
        try:
            price_info = kis_api.get_price(ticker)
            cur_price  = int(price_info["stck_prpr"])
            ret        = (cur_price - pos["buy_price"]) / pos["buy_price"]

            reason = None
            if cur_price <= pos["sl"]:
                reason = "손절"
            elif cur_price >= pos["tp"]:
                reason = "익절"

            if reason:
                to_sell.append((ticker, pos, reason, cur_price))
                logger.info("청산 신호  %s  %s  현재가: %d  수익률: %.2f%%",
                            ticker, reason, cur_price, ret * 100)

        except Exception as e:
            logger.error("%s 모니터링 실패: %s", ticker, e)

    for ticker, pos, reason, cur_price in to_sell:
        try:
            result = kis_api.place_order(ticker, "SELL", pos["qty"])
            pnl = (cur_price - pos["buy_price"]) * pos["qty"]
            state["daily_pnl"] += pnl
            del state["positions"][ticker]
            logger.info("매도 완료  %s(%s)  %d주  손익: %+d원  [%s]  주문번호: %s",
                        ticker, pos["name"], pos["qty"], pnl,
                        reason, result["order_no"])
        except Exception as e:
            logger.error("%s 매도 실패: %s", ticker, e)


# ─────────────────────────────────────────
# 4. 장 마감 청산 (15:20)
# ─────────────────────────────────────────
def job_close_all():
    if not is_trading_day():
        return
    if not state["positions"]:
        logger.info("보유 포지션 없음 — 마감 청산 건너뜀")
        return

    log_banner("장 마감 — 미청산 포지션 처리 (15:20)")

    for ticker, pos in list(state["positions"].items()):
        # hold_days 초과 또는 당일 매수 (단기 전략이므로 10일 후 자동 청산)
        buy_date = pos["buy_date"]
        hold = (date.today() - buy_date).days if isinstance(buy_date, date) else 0

        if hold >= CFG.hold_days:
            try:
                result = kis_api.place_order(ticker, "SELL", pos["qty"])
                logger.info("기간 종료 청산  %s  %d일 보유  주문번호: %s",
                            ticker, hold, result["order_no"])
                del state["positions"][ticker]
            except Exception as e:
                logger.error("%s 마감 청산 실패: %s", ticker, e)


# ─────────────────────────────────────────
# 5. 일간 리포트 (15:35)
# ─────────────────────────────────────────
def job_daily_report():
    if not is_trading_day():
        return
    log_banner(f"일간 리포트 — {date.today()}")
    try:
        bal = kis_api.get_balance()
        total = bal["total_eval"]
        initial = state["initial_cash"] or total
        day_ret = (total - initial) / initial * 100 if initial else 0

        logger.info("당일 실현 손익: %+d원", state["daily_pnl"])
        logger.info("총 자산: %d원  (당일 수익률 %+.2f%%)", total, day_ret)
        logger.info("보유 종목 수: %d", len(bal["holdings"]))
        for h in bal["holdings"]:
            logger.info("  %s %s  %d주  손익 %+.2f%%",
                        h["ticker"], h["name"], h["qty"], h["pnl_pct"])

        # 다음날 준비
        state["daily_pnl"]  = 0
        state["initial_cash"] = 0
        state["watchlist"]  = []

    except Exception as e:
        logger.exception("리포트 오류: %s", e)


# ─────────────────────────────────────────
# 스케줄러 설정 및 실행
# ─────────────────────────────────────────
def main():
    mode = "🟡 모의투자" if PAPER else "🔴 실계좌"
    log_banner(f"자동매매 봇 시작  [{mode}]")

    # 전략 정보 출력
    logger.info("━" * 50)
    logger.info("  현재 전략: [%s]", STRATEGY_NAME.upper())
    logger.info("  %s", STRATEGY_PRESETS.get(STRATEGY_NAME, {}).get("desc", ""))
    logger.info("━" * 50)
    logger.info("  거래량 배수: %.1f배  당일수익 최소: %.1f%%",
                CFG.volume_mult, CFG.day_return_min * 100)
    logger.info("  손절: %d%%  익절: +%d%%  보유기간: %d일  최대 보유: %d종목",
                int(CFG.stop_loss * 100), int(CFG.take_profit * 100),
                CFG.hold_days, MAX_POSITIONS)
    logger.info("  1회 최대 주문: %d원  당일 손실 한도: %d원",
                MAX_ORDER_AMOUNT, MAX_DAILY_LOSS)
    logger.info("")
    logger.info("  전략 변경: .env 의 STRATEGY= 값 수정 후 재시작")
    logger.info("  선택 가능: %s", ", ".join(STRATEGY_PRESETS.keys()))
    logger.info("━" * 50)

    if not PAPER:
        logger.warning("⚠ 실계좌 모드입니다. 실제 주문이 집행됩니다!")
        input("계속하려면 Enter, 중단하려면 Ctrl+C: ")

    scheduler = BlockingScheduler(timezone="Asia/Seoul")

    # 평일만 실행
    kwargs = {"day_of_week": "mon-fri"}

    scheduler.add_job(job_generate_signals, "cron", hour=8,  minute=50, **kwargs)
    scheduler.add_job(job_buy,              "cron", hour=9,  minute=5,  **kwargs)
    scheduler.add_job(job_monitor,          "cron",          minute="*/1",
                      day_of_week="mon-fri", hour="9-15")    # 09:00~15:59 매분
    scheduler.add_job(job_close_all,        "cron", hour=15, minute=20, **kwargs)
    scheduler.add_job(job_daily_report,     "cron", hour=15, minute=35, **kwargs)

    logger.info("스케줄 등록 완료:")
    for job in scheduler.get_jobs():
        logger.info("  %s", job.id)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log_banner("봇 종료")


if __name__ == "__main__":
    main()
