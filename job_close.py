"""
Job 4 — 15:20 마감 청산
보유기간 초과 포지션 종가 매도
"""

import os, logging
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

STRATEGY_NAME = os.getenv("STRATEGY", "optimized")
CFG, _PRESET  = load_strategy(STRATEGY_NAME)

# 매도 시 증권거래세+수수료 등 왕복 비용 추정치 (기본 0.3%) — 이보다 수익률이
# 높아야 "수수료 제외 실질 수익"으로 보고 마감 강제 익절 대상에 포함시킨다.
# 실제 계좌 수수료율에 맞춰 GH Secret CLOSE_PROFIT_FEE_PCT로 조정 가능.
CLOSE_PROFIT_FEE_PCT = float(os.getenv("CLOSE_PROFIT_FEE_PCT", "0.003"))


def main():
    logger.info("=== 마감 청산 (15:20) ===")
    positions = state_db.get_positions()
    if not positions:
        logger.info("보유 포지션 없음")
        return

    today     = date.today()
    daily_pnl = state_db.get_meta("daily_pnl", 0) or 0

    # ── get_balance() 1번으로 보유 종목 현재가 일괄 획득 ────────────
    try:
        bal = kis_api.get_balance()
        price_map = {h["ticker"]: h["eval_price"] for h in bal["holdings"]}
    except Exception as e:
        logger.warning("잔고 조회 실패, get_price() 폴백: %s", e)
        price_map = {}

    for ticker, pos in list(positions.items()):
        hold = (today - pos["buy_date"]).days if isinstance(pos["buy_date"], date) else 0
        try:
            cur_price = price_map.get(ticker)
            if not cur_price:
                # 잔고에 없는 경우(이미 매도됐거나 다른 계좌) get_price() 폴백
                cur_price = int(kis_api.get_price(ticker)["stck_prpr"])
        except Exception as e:
            logger.error("%s 현재가 조회 실패: %s", ticker, e)
            continue

        pnl_pct = (cur_price - pos["buy_price"]) / pos["buy_price"]

        if hold >= CFG.hold_days:
            reason, emoji = "기간청산", "🏁"
        elif pnl_pct > CLOSE_PROFIT_FEE_PCT:
            # 보유기간은 남았지만, 수수료 제외 실질 수익이 나 있으면 장마감 전 강제 익절
            reason, emoji = "마감익절", "💰"
        else:
            continue  # 보유기간 남았고 수수료 제외 실익도 없음 → 다음날로 유지

        try:
            result      = kis_api.place_order(ticker, "SELL", pos["qty"])
            pnl         = (cur_price - pos["buy_price"]) * pos["qty"]
            pnl_pct_100 = pnl_pct * 100
            daily_pnl  += pnl
            state_db.delete_position(ticker)
            state_db.set_meta("daily_pnl", daily_pnl)

            gist_writer.log_trade(
                ticker=ticker, name=pos.get("name", ""),
                trade_type="sell", price=cur_price, qty=pos["qty"],
                pnl=pnl, pnl_pct=pnl_pct_100, reason=reason,
                order_no=result["order_no"],
            )

            notify.send(
                f"{emoji} <b>{reason}</b>  {pos.get('name','')} ({ticker})\n"
                f"  {hold}일 보유  매도가 {cur_price:,}원\n"
                f"  손익: <b>{pnl:+,}원 ({pnl_pct_100:+.2f}%)</b>\n"
                f"  주문번호: {result['order_no']}"
            )
            logger.info("%s  %s  %d일 보유  주문번호: %s",
                        reason, ticker, hold, result["order_no"])
        except Exception as e:
            logger.error("%s 마감 청산 실패: %s", ticker, e)
            notify.send(f"❌ 마감 청산 실패: {ticker} — {e}")

    # 거래 내역 일괄 저장
    gist_writer.flush_trades()


if __name__ == "__main__":
    main()
