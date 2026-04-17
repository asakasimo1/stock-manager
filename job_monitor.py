"""
Job 3 — 09:00~15:59 매 10분 모니터링
보유 포지션의 손절/익절 조건 확인 후 매도
"""

import os, logging
from datetime import datetime, timezone, timedelta
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
    meta = state_db.get_meta_multi(["bot_active", "daily_pnl"], {"bot_active": True, "daily_pnl": 0})
    bot_active = meta["bot_active"]
    if not bot_active:
        return

    positions = state_db.get_positions()
    if not positions:
        return

    daily_pnl = meta["daily_pnl"] or 0
    if daily_pnl <= -MAX_DAILY_LOSS:
        logger.warning("당일 손실 한도 초과 — 봇 중단")
        state_db.set_meta("bot_active", False)
        notify.send(f"⛔ 당일 손실 한도 초과 ({daily_pnl:+,}원) — 봇 중단")
        return

    # ── get_balance() 1번으로 현재가 + 잔고 동시 획득 ───────────────
    # 기존: N × get_price() 병렬 호출
    # 개선: get_balance() 1번 → eval_price 사용 → update_account_balance()도 재활용
    bal = kis_api.get_balance()
    price_map = {h["ticker"]: h["eval_price"] for h in bal["holdings"]}

    to_sell = []
    for ticker, pos in positions.items():
        cur_price = price_map.get(ticker)
        if cur_price is None:
            logger.warning("%s 잔고에서 현재가 미확인 — 건너뜀", ticker)
            continue
        if cur_price <= pos["sl"]:
            to_sell.append((ticker, pos, "손절", cur_price))
        elif cur_price >= pos["tp"]:
            to_sell.append((ticker, pos, "익절", cur_price))

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

        except Exception as e:
            logger.error("%s 매도 실패: %s", ticker, e)
            notify.send(f"❌ 매도 실패: {ticker} — {e}")

    # 거래 내역 일괄 저장 (매도 N건 → Gist 1회)
    gist_writer.flush_trades()

    # bal 재활용 — 두 번째 get_balance() 불필요
    update_account_balance(bal, daily_pnl, meta)


def update_account_balance(bal: dict = None, daily_pnl: int = None, meta: dict = None):
    """잔고를 Gist에 저장 (stock_analyzer 자동매매 탭 실시간 반영)
    bal, daily_pnl, meta를 전달하면 추가 API 호출 없이 재활용합니다.
    """
    try:
        if bal is None:
            bal = kis_api.get_balance()
        if meta is None:
            meta = state_db.get_meta_multi(["daily_pnl", "initial_cash"], {"daily_pnl": 0})
        if daily_pnl is None:
            daily_pnl = meta["daily_pnl"] or 0

        KST     = timezone(timedelta(hours=9))
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        total   = bal["total_eval"]
        initial = meta.get("initial_cash") or total
        day_ret = (total - initial) / initial * 100 if initial else 0

        account_data = {
            "updated_at": now_kst,
            "cash":       bal["cash"],
            "total_eval": total,
            "day_pnl":    daily_pnl,
            "day_ret":    round(day_ret, 2),
            "holdings": [
                {
                    "ticker":     h["ticker"],
                    "name":       h["name"],
                    "qty":        h["qty"],
                    "avg_price":  h["avg_price"],
                    "eval_price": h["eval_price"],
                    "pnl_pct":    round(h["pnl_pct"], 2),
                    "eval_amt":   h["eval_price"] * h["qty"],
                    "buy_amt":    h["avg_price"] * h["qty"],
                }
                for h in bal["holdings"]
            ],
        }
        gist_writer._write_gist({"account_balance.json": account_data})
        logger.info("계좌 잔액 Gist 업데이트 완료 (보유 %d종목)", len(bal["holdings"]))
    except Exception as e:
        logger.warning("계좌 잔액 업데이트 실패: %s", e)


if __name__ == "__main__":
    main()
    update_account_balance()
