"""
Job 4 — 15:20 마감 청산
보유기간 초과 포지션 종가 매도
"""

import os, time, logging
from datetime import date
from dotenv import load_dotenv
from strategy import load_strategy
import kis_api
import state_db
import notify

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STRATEGY_NAME = os.getenv("STRATEGY", "optimized")
CFG, _PRESET  = load_strategy(STRATEGY_NAME)


def main():
    logger.info("=== 마감 청산 (15:20) ===")
    positions = state_db.get_positions()
    if not positions:
        logger.info("보유 포지션 없음")
        return

    today     = date.today()
    daily_pnl = state_db.get_meta("daily_pnl", 0) or 0

    for ticker, pos in list(positions.items()):
        hold = (today - pos["buy_date"]).days if isinstance(pos["buy_date"], date) else 0
        if hold >= CFG.hold_days:
            try:
                price_info = kis_api.get_price(ticker)
                cur_price  = int(price_info["stck_prpr"])
                result     = kis_api.place_order(ticker, "SELL", pos["qty"])
                pnl        = (cur_price - pos["buy_price"]) * pos["qty"]
                pnl_pct    = (cur_price - pos["buy_price"]) / pos["buy_price"] * 100
                daily_pnl += pnl
                state_db.delete_position(ticker)
                state_db.set_meta("daily_pnl", daily_pnl)

                notify.send(
                    f"🏁 <b>기간 청산</b>  {pos.get('name','')} ({ticker})\n"
                    f"  {hold}일 보유  매도가 {cur_price:,}원\n"
                    f"  손익: <b>{pnl:+,}원 ({pnl_pct:+.2f}%)</b>\n"
                    f"  주문번호: {result['order_no']}"
                )
                logger.info("기간 청산  %s  %d일 보유  주문번호: %s",
                            ticker, hold, result["order_no"])
                time.sleep(0.3)
            except Exception as e:
                logger.error("%s 마감 청산 실패: %s", ticker, e)
                notify.send(f"❌ 마감 청산 실패: {ticker} — {e}")


if __name__ == "__main__":
    main()
