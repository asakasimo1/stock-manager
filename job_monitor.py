"""
Job 3 — 09:00~15:59 매 10분 모니터링
보유 포지션의 손절/익절 조건 확인 후 매도
"""

import os, time, logging
from dotenv import load_dotenv
from strategy import load_strategy
import kis_api
import state_db
import notify
import gist_writer

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STRATEGY_NAME  = os.getenv("STRATEGY", "optimized")
CFG, _PRESET   = load_strategy(STRATEGY_NAME)
MAX_DAILY_LOSS = _PRESET["max_daily_loss"]


def main():
    bot_active = state_db.get_meta("bot_active", True)
    if not bot_active:
        return

    positions = state_db.get_positions()
    if not positions:
        return

    daily_pnl = state_db.get_meta("daily_pnl", 0) or 0
    if daily_pnl <= -MAX_DAILY_LOSS:
        logger.warning("당일 손실 한도 초과 — 봇 중단")
        state_db.set_meta("bot_active", False)
        notify.send(f"⛔ 당일 손실 한도 초과 ({daily_pnl:+,}원) — 봇 중단")
        return

    to_sell = []
    for ticker, pos in positions.items():
        try:
            price_info = kis_api.get_price(ticker)
            cur_price  = int(price_info["stck_prpr"])
            time.sleep(0.3)

            if cur_price <= pos["sl"]:
                to_sell.append((ticker, pos, "손절", cur_price))
            elif cur_price >= pos["tp"]:
                to_sell.append((ticker, pos, "익절", cur_price))

        except Exception as e:
            logger.error("%s 모니터링 실패: %s", ticker, e)

    for ticker, pos, reason, cur_price in to_sell:
        try:
            result  = kis_api.place_order(ticker, "SELL", pos["qty"])
            pnl     = (cur_price - pos["buy_price"]) * pos["qty"]
            pnl_pct = (cur_price - pos["buy_price"]) / pos["buy_price"] * 100
            daily_pnl += pnl
            state_db.delete_position(ticker)
            state_db.set_meta("daily_pnl", daily_pnl)

            gist_writer.log_trade(
                ticker=ticker, name=pos.get("name", ""),
                trade_type="sell", price=cur_price, qty=pos["qty"],
                pnl=pnl, pnl_pct=pnl_pct, reason=reason,
                order_no=result["order_no"],
            )

            emoji = "🔴" if reason == "손절" else "💰"
            notify.send(
                f"{emoji} <b>{reason}</b>  {pos.get('name','')} ({ticker})\n"
                f"  매수가 {pos['buy_price']:,}원 → 매도가 {cur_price:,}원\n"
                f"  손익: <b>{pnl:+,}원 ({pnl_pct:+.2f}%)</b>\n"
                f"  주문번호: {result['order_no']}"
            )
            logger.info("매도 완료  %s  %d주  손익: %+d원  [%s]", ticker, pos["qty"], pnl, reason)
            time.sleep(0.3)

        except Exception as e:
            logger.error("%s 매도 실패: %s", ticker, e)
            notify.send(f"❌ 매도 실패: {ticker} — {e}")


if __name__ == "__main__":
    main()
