"""
Job 5 — 15:35 일간 리포트
당일 손익 출력 후 다음날 준비를 위해 메타 초기화
"""

import logging
from datetime import date
from dotenv import load_dotenv
import kis_api
import state_db

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
        logger.info("보유 종목 수   : %d", len(bal["holdings"]))
        for h in bal["holdings"]:
            logger.info("  %s %s  %d주  손익 %+.2f%%",
                        h["ticker"], h["name"], h["qty"], h["pnl_pct"])

    except Exception as e:
        logger.exception("리포트 오류: %s", e)

    # 다음날 준비
    state_db.set_meta("daily_pnl", 0)
    state_db.set_meta("initial_cash", None)
    state_db.set_meta("bot_active", True)
    logger.info("다음날 준비 완료")


if __name__ == "__main__":
    main()
