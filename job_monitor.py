"""
Job 3 — 09:00~15:59 매 10분 모니터링
보유 포지션의 손절/익절 조건 확인 후 매도
"""

import os, time, logging
from dotenv import load_dotenv
from trader import load_strategy
import kis_api
import state_db

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STRATEGY_NAME = os.getenv("STRATEGY", "optimized")
CFG, _PRESET  = load_strategy(STRATEGY_NAME)
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
        return

    to_sell = []
    for ticker, pos in positions.items():
        try:
            price_info = kis_api.get_price(ticker)
            cur_price  = int(price_info["stck_prpr"])
            time.sleep(0.3)

            if cur_price <= pos["sl"]:
                to_sell.append((ticker, pos, "손절", cur_price))
                logger.info("손절 신호  %s  현재가: %d  손절가: %d", ticker, cur_price, pos["sl"])
            elif cur_price >= pos["tp"]:
                to_sell.append((ticker, pos, "익절", cur_price))
                logger.info("익절 신호  %s  현재가: %d  익절가: %d", ticker, cur_price, pos["tp"])

        except Exception as e:
            logger.error("%s 모니터링 실패: %s", ticker, e)

    for ticker, pos, reason, cur_price in to_sell:
        try:
            result = kis_api.place_order(ticker, "SELL", pos["qty"])
            pnl = (cur_price - pos["buy_price"]) * pos["qty"]
            daily_pnl += pnl
            state_db.delete_position(ticker)
            state_db.set_meta("daily_pnl", daily_pnl)
            logger.info("매도 완료  %s  %d주  손익: %+d원  [%s]  주문번호: %s",
                        ticker, pos["qty"], pnl, reason, result["order_no"])
            time.sleep(0.3)
        except Exception as e:
            logger.error("%s 매도 실패: %s", ticker, e)


if __name__ == "__main__":
    main()
