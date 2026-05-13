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


def main():
    logger.info("잔고 조회 시작")
    try:
        bal = kis_api.get_balance()
        try:
            meta         = state_db.get_meta_multi(["daily_pnl", "initial_cash"], {"daily_pnl": 0})
            daily_pnl    = meta["daily_pnl"] or 0
            initial_cash = meta.get("initial_cash") or bal["total_eval"]
        except Exception as e:
            logger.warning("state_db 건너뜀 (Supabase 미설정): %s", e)
            daily_pnl    = 0
            initial_cash = bal["total_eval"]
        day_ret = round((bal["total_eval"] - initial_cash) / initial_cash * 100, 2) if initial_cash else 0

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
                    "eval_amt":   h["eval_price"] * h["qty"],
                    "buy_amt":    h["avg_price"] * h["qty"],
                }
                for h in bal["holdings"]
            ],
        }
        ok = gist_writer._write_gist({"account_balance.json": account_data})
        logger.info("잔고 Gist 업데이트 %s — 보유 %d종목  당일손익 %+,d원 (%+.2f%%)",
                    "완료" if ok else "실패", len(bal["holdings"]), daily_pnl, day_ret)
        for h in bal["holdings"]:
            logger.info("  %s %s  %d주  평균 %d원  현재 %d원  손익 %+.2f%%",
                        h["ticker"], h["name"], h["qty"],
                        h["avg_price"], h["eval_price"], h["pnl_pct"])
        try:
            _sync_positions(bal["holdings"])
        except Exception as e:
            logger.warning("포지션 동기화 건너뜀 (Supabase 미설정): %s", e)
    except Exception as e:
        logger.exception("잔고 조회 오류: %s", e)
        raise


if __name__ == "__main__":
    main()
