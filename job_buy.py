"""
Job 2 — 09:05 매수 실행
state_db watchlist를 읽어 매수 주문 후 positions에 저장
"""

import os, time, logging
from datetime import date
from dotenv import load_dotenv
from strategy import load_strategy
import kis_api
import state_db
import notify
import gist_writer

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STRATEGY_NAME    = os.getenv("STRATEGY", "optimized")
CFG, _PRESET     = load_strategy(STRATEGY_NAME)
MAX_POSITIONS    = CFG.max_positions
MAX_ORDER_AMOUNT = _PRESET["max_order_amount"]
MAX_DAILY_LOSS   = _PRESET["max_daily_loss"]


def main():
    logger.info("=== 매수 실행 시작 (09:05) | 전략: %s ===", STRATEGY_NAME.upper())

    meta = state_db.get_meta_multi(
        ["bot_active", "daily_pnl", "initial_cash"],
        {"bot_active": True, "daily_pnl": 0},
    )
    bot_active = meta["bot_active"]
    if not bot_active:
        logger.warning("봇 비활성 상태 — 매수 건너뜀")
        return

    daily_pnl = meta["daily_pnl"] or 0
    if daily_pnl <= -MAX_DAILY_LOSS:
        logger.warning("당일 손실 한도 초과 (%d원) — 매수 중단", daily_pnl)
        state_db.set_meta("bot_active", False)
        notify.send(f"⛔ 당일 손실 한도 초과 ({daily_pnl:+,}원) — 봇 중단")
        return

    watchlist = state_db.get_watchlist()
    positions = state_db.get_positions()

    if not watchlist:
        logger.info("매수 후보 없음")
        return

    # 초기 자산 기록
    if not meta.get("initial_cash"):
        bal = kis_api.get_balance()
        state_db.set_meta("initial_cash", bal["total_eval"])
        for h in bal["holdings"]:
            if h["ticker"] not in positions:
                pos = {
                    "buy_price": h["avg_price"],
                    "qty":       h["qty"],
                    "tp":        h["avg_price"] * (1 + CFG.take_profit),
                    "sl":        h["avg_price"] * (1 + CFG.stop_loss),
                    "buy_date":  date.today(),
                    "name":      h["name"],
                }
                state_db.upsert_position(h["ticker"], pos)
                positions[h["ticker"]] = pos
        time.sleep(0.5)

    bal  = kis_api.get_balance()
    cash = bal["cash"]
    time.sleep(0.3)

    slots = MAX_POSITIONS - len(positions)
    if slots <= 0:
        logger.info("보유 종목 꽉 참 (%d/%d) — 매수 건너뜀", len(positions), MAX_POSITIONS)
        return

    for sig in watchlist[:slots]:
        ticker = sig["ticker"]
        if ticker in positions:
            continue
        try:
            price_info = kis_api.get_price(ticker)
            cur_price  = int(price_info["stck_prpr"])
            if cur_price <= 0:
                continue

            alloc = min(cash // slots, MAX_ORDER_AMOUNT)
            qty   = int(alloc / cur_price)
            if qty <= 0:
                logger.warning("%s 수량 0 — 건너뜀 (가격: %d원)", ticker, cur_price)
                continue

            result = kis_api.place_order(ticker, "BUY", qty)
            amount = qty * cur_price
            logger.info("매수 주문  %s(%s)  %d주  약 %d원  주문번호: %s",
                        ticker, sig.get("name",""), qty, amount, result["order_no"])

            gist_writer.log_trade(
                ticker=ticker, name=sig.get("name", ""),
                trade_type="buy", price=cur_price, qty=qty,
                order_no=result["order_no"],
            )

            notify.send(
                f"🟢 <b>매수</b>  {sig.get('name','')} ({ticker})\n"
                f"  {qty}주 × {cur_price:,}원 = <b>{amount:,}원</b>\n"
                f"  손절 {CFG.stop_loss*100:.0f}%  익절 +{CFG.take_profit*100:.0f}%\n"
                f"  주문번호: {result['order_no']}"
            )

            pos = {
                "buy_price": cur_price,
                "qty":       qty,
                "tp":        cur_price * (1 + CFG.take_profit),
                "sl":        cur_price * (1 + CFG.stop_loss),
                "buy_date":  date.today(),
                "name":      sig.get("name", ""),
            }
            state_db.upsert_position(ticker, pos)
            cash -= amount
            time.sleep(0.3)

        except Exception as e:
            logger.error("%s 매수 실패: %s", ticker, e)
            notify.send(f"❌ 매수 실패: {ticker} — {e}")

    # 거래 내역 일괄 저장
    gist_writer.flush_trades()


if __name__ == "__main__":
    main()
