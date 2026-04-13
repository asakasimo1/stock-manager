"""
Job 5 — 15:35 일간 리포트
당일 손익 출력 후 다음날 준비를 위해 메타 초기화
"""

import logging
from datetime import date, datetime, timezone, timedelta
from dotenv import load_dotenv
import kis_api
import state_db
import notify
import gist_writer

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 50)
    logger.info("  일간 리포트 — %s", date.today())
    logger.info("=" * 50)

    try:
        bal          = kis_api.get_balance()
        total        = bal["total_eval"]
        daily_pnl    = state_db.get_meta("daily_pnl", 0) or 0
        initial_cash = state_db.get_meta("initial_cash") or total
        day_ret      = (total - initial_cash) / initial_cash * 100 if initial_cash else 0

        logger.info("당일 실현 손익 : %+d원", daily_pnl)
        logger.info("총 자산        : %d원  (당일 수익률 %+.2f%%)", total, day_ret)

        # 텔레그램 리포트
        emoji  = "📈" if daily_pnl >= 0 else "📉"
        msg    = (
            f"{emoji} <b>일간 리포트 — {date.today()}</b>\n"
            f"  당일 손익: <b>{daily_pnl:+,}원</b>\n"
            f"  총 자산:   {total:,}원  ({day_ret:+.2f}%)\n"
            f"  보유 종목: {len(bal['holdings'])}개"
        )
        for h in bal["holdings"]:
            msg += f"\n  • {h['name']} {h['qty']}주  {h['pnl_pct']:+.2f}%"
            logger.info("  %s %s  %d주  손익 %+.2f%%",
                        h["ticker"], h["name"], h["qty"], h["pnl_pct"])

        notify.send(msg)

        # Gist에 계좌 잔액 저장 (stock_analyzer 대시보드용)
        now_kst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
        account_data = {
            "updated_at":  now_kst,
            "cash":        bal["cash"],
            "total_eval":  bal["total_eval"],
            "day_pnl":     daily_pnl,
            "day_ret":     round(day_ret, 2),
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
        logger.info("계좌 잔액 Gist 저장 완료")

    except Exception as e:
        logger.exception("리포트 오류: %s", e)
        notify.send(f"❌ 리포트 오류: {e}")

    # 다음날 준비
    state_db.set_meta("daily_pnl", 0)
    state_db.set_meta("initial_cash", None)
    state_db.set_meta("bot_active", True)
    logger.info("다음날 준비 완료")


if __name__ == "__main__":
    main()
