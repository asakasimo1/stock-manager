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

    # 트레일링 스탑 파라미터
    trail_trigger = CFG.trail_trigger   # 이 수익률 달성 시 트레일링 활성화
    trail_pct     = CFG.trail_pct       # 활성화 후 고점 대비 낙폭 허용치
    safe_trigger  = trail_trigger * 0.8 # 수익보호(원금) 활성화 기준 (trail의 80%)

    to_sell    = []
    sl_changed = {}   # {ticker: new_sl} — 매도 아닌 포지션의 sl 갱신

    for ticker, pos in positions.items():
        cur_price = price_map.get(ticker)
        if cur_price is None:
            logger.warning("%s 잔고에서 현재가 미확인 — 건너뜀", ticker)
            continue

        buy_price = pos["buy_price"]
        pnl_pct   = (cur_price - buy_price) / buy_price
        orig_sl   = pos["sl"]
        new_sl    = orig_sl   # sl은 단조 증가만 허용 (내리지 않음)

        # ── 트레일링 / 수익보호 손절선 동적 상향 ────────────────────
        if pnl_pct >= trail_trigger:
            # 트레일링 스탑: 현재가 기준 고점 추적 (최대 손실 고정)
            trail_sl = cur_price * (1 - trail_pct)
            if trail_sl > new_sl:
                new_sl = trail_sl
                logger.info("[트레일링] %s  수익 %+.1f%%  sl %.0f→%.0f원",
                            ticker, pnl_pct * 100, orig_sl, new_sl)
        elif pnl_pct >= safe_trigger:
            # 수익보호: 원금 이상으로 손절선 상향 (손실 완전 방지)
            if buy_price > new_sl:
                new_sl = buy_price
                logger.info("[수익보호] %s  수익 %+.1f%%  sl %.0f→%.0f원(원금 확보)",
                            ticker, pnl_pct * 100, orig_sl, new_sl)

        if new_sl != orig_sl:
            pos["sl"] = new_sl
            sl_changed[ticker] = new_sl
            # 원금 미만 → 원금 이상 전환 시 (처음 수익 보호 돌입) 알림
            if orig_sl < buy_price <= new_sl:
                notify.send(
                    f"📈 <b>수익보호 돌입</b>  {pos.get('name','')} ({ticker})\n"
                    f"  현재가 {cur_price:,}원  수익 {pnl_pct:+.1f}%\n"
                    f"  손절선 {orig_sl:,.0f}원 → {new_sl:,.0f}원 (손실 없는 구간)"
                )

        # ── 매도 조건 체크 ────────────────────────────────────────
        if cur_price <= pos["sl"]:
            if pos["sl"] > buy_price:
                reason = f"트레일링/수익보호 ({pnl_pct:+.1f}%)"
                emoji  = "📉"
            else:
                reason = "손절"
                emoji  = "🔴"
            to_sell.append((ticker, pos, reason, cur_price, emoji))
        elif cur_price >= pos["tp"]:
            to_sell.append((ticker, pos, "익절", cur_price, "💰"))

    # ── sl 변경 포지션 DB 반영 (매도 예정 제외) ─────────────────
    sell_tickers = {t for t, *_ in to_sell}
    for ticker, new_sl in sl_changed.items():
        if ticker not in sell_tickers:
            pos = positions[ticker]
            state_db.upsert_position(ticker, pos)

    for ticker, pos, reason, cur_price, emoji in to_sell:
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
