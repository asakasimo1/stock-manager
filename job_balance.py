"""
잔고 조회 잡 — workflow_dispatch로 즉시 실행 / 사이클 매매 후 자동 호출
KIS API에서 현재 잔고를 조회하여 Gist account_balance.json 업데이트
"""
import logging
import os
from datetime import datetime, timezone, timedelta, date
from dotenv import load_dotenv
import kis_api
import gist_writer
import state_db

load_dotenv()
KST = timezone(timedelta(hours=9))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _sync_positions(holdings: list):
    """KIS 잔고 기준으로 Supabase positions 동기화
    - KIS에 없는 종목 → Supabase에서 삭제 (매도 완료)
    - KIS에 있는데 Supabase에 없는 종목 → 추가 (avg_price 기준 tp/sl 계산)
    """
    from strategy import load_strategy
    cfg, _ = load_strategy(os.getenv("STRATEGY", "optimized"))

    kis_tickers = {h["ticker"] for h in holdings}
    db_positions = state_db.get_positions()
    db_tickers = set(db_positions.keys())

    to_delete = db_tickers - kis_tickers
    for ticker in to_delete:
        state_db.delete_position(ticker)
        logger.info("포지션 동기화: %s 삭제 (KIS에 없음)", ticker)

    to_add = kis_tickers - db_tickers
    for h in holdings:
        if h["ticker"] in to_add:
            avg = h["avg_price"]
            pos = {
                "buy_price": avg,
                "qty":       h["qty"],
                "tp":        avg * (1 + cfg.take_profit),
                "sl":        avg * (1 + cfg.stop_loss),
                "buy_date":  date.today().isoformat(),
                "name":      h["name"],
            }
            state_db.upsert_position(h["ticker"], pos)
            logger.info("포지션 동기화: %s 추가", h["ticker"])

    if to_delete or to_add:
        logger.info("포지션 동기화 완료: 삭제 %d개, 추가 %d개", len(to_delete), len(to_add))
    else:
        logger.info("포지션 동기화: 변경 없음 (%d종목 일치)", len(kis_tickers))


def _reconcile_trades():
    """오늘자 KIS 실제 체결 전체(자동매매 + MTS 등 수동 주문)를 조회해서
    아직 거래내역(trader_trades.json)에 없는 체결만 order_no 기준 중복 없이 추가.
    자동매매 각 잡이 개별적으로 log_trade()를 호출하지 않는 이유는, 여기서
    KIS 체결 이력을 단일 진실 공급원으로 삼아 이중 기록을 방지하기 위함."""
    try:
        executions = kis_api.get_daily_executions()
    except Exception as e:
        logger.warning("일별체결조회 실패, 거래내역 반영 건너뜀: %s", e)
        return

    if not executions:
        return

    existing = gist_writer._read_trades()
    known_order_nos = {t.get("order_no") for t in existing if t.get("order_no")}

    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    new_count = 0
    for ex in executions:
        order_no = ex.get("order_no")
        if not order_no or order_no in known_order_nos:
            continue
        # ex["time"]은 HHMMSS(체결 실제 시각) — 이걸 안 넘기면 log_trade()가
        # "지금(기록 시점)"으로 찍어서, 나중에 조회할 때 실제 체결시각과 다르게
        # 보임(2026-08-10 실측: 09시대 체결이 12시대 거래로 표시되는 버그 원인).
        raw_time = ex.get("time", "")
        trade_time = f"{raw_time[0:2]}:{raw_time[2:4]}" if len(raw_time) >= 4 else None
        gist_writer.log_trade(
            ticker=ex["ticker"],
            name=ex["name"],
            trade_type="buy" if ex["side"] == "BUY" else "sell",
            price=ex["price"],
            qty=ex["qty"],
            order_no=order_no,
            trade_date=today_str,
            trade_time=trade_time,
        )
        new_count += 1

    if new_count:
        gist_writer.flush_trades()
        logger.info("거래내역 Gist 반영: 신규 체결 %d건 (자동+수동 포함)", new_count)


def main():
    logger.info("잔고 조회 시작")
    try:
        bal = kis_api.get_balance()
        # 당일손익 = (보유종목의 전일종가 대비 평가변동) + (오늘 완료된 그리드
        # 매매 실현손익). 총자산평가금액 단순 비교(전일 대비) 방식은 당일
        # 입출금까지 손익으로 잡아버리는 문제가 있어(실측: 10만원 입금이
        # 그대로 손익에 섞임) 폐기 — 아래 방식은 가격 변동/체결 기반이라
        # 입출금과 무관하게 항상 정확함.
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        unrealized_day = sum(h.get("bfdy_close_diff", 0) * h["qty"] for h in bal["holdings"])

        realized_today = 0
        try:
            grid_jobs = gist_writer._read_gist_file("stock_grid_jobs.json") or []
            for gj in grid_jobs:
                for t in gj.get("trade_history", []):
                    if t.get("date") == today_str:
                        realized_today += t.get("profit", 0)
        except Exception as e:
            logger.warning("그리드 당일 실현손익 집계 실패: %s", e)

        daily_pnl = round(unrealized_day + realized_today, 0)
        bfdy_total_eval = bal.get("bfdy_total_eval", 0)
        day_ret = round(daily_pnl / bfdy_total_eval * 100, 2) if bfdy_total_eval else 0

        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        account_data = {
            "updated_at": now_kst,
            "cash":       bal["cash"],
            "total_eval": bal["total_eval"],
            "day_pnl":    daily_pnl,
            "day_ret":    day_ret,
            "holdings": [
                {
                    "ticker":     h["ticker"],
                    "name":       h["name"],
                    "qty":        h["qty"],
                    "avg_price":  h["avg_price"],
                    "eval_price": h["eval_price"],
                    "pnl_pct":    round(h["pnl_pct"], 2),
                    "pnl":        h.get("pnl", 0),  # 수수료+세금 포함 평가손익(원) — kis_api.get_balance()에서 계산됨
                    "eval_amt":   h["eval_price"] * h["qty"],
                    "buy_amt":    h["avg_price"] * h["qty"],
                }
                for h in bal["holdings"]
            ],
        }
        ok = gist_writer._write_gist({"account_balance.json": account_data})
        logger.info("잔고 Gist 업데이트 %s — 보유 %d종목  당일손익 %s원 (%+.2f%%)",
                    "완료" if ok else "실패", len(bal["holdings"]), f"{daily_pnl:+,.0f}", day_ret)
        for h in bal["holdings"]:
            logger.info("  %s %s  %d주  평균 %d원  현재 %d원  손익 %+.2f%%",
                        h["ticker"], h["name"], h["qty"],
                        h["avg_price"], h["eval_price"], h["pnl_pct"])
        try:
            _sync_positions(bal["holdings"])
        except Exception as e:
            logger.warning("포지션 동기화 건너뜀 (Supabase 미설정): %s", e)
        try:
            _reconcile_trades()
        except Exception as e:
            logger.warning("거래내역 반영 건너뜀: %s", e)
    except Exception as e:
        logger.exception("잔고 조회 오류: %s", e)
        raise


if __name__ == "__main__":
    main()
