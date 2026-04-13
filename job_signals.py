"""
Job 1 — 08:50 신호 생성
기술적 시그널(거래량+모멘텀) 종목을 Supabase watchlist에 저장
"""

import logging
from dotenv import load_dotenv
from signals import get_double_signals
import state_db
import notify

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def main():
    logger.info("=== 신호 생성 시작 (08:50) ===")
    candidates = get_double_signals(use_foreign=False)
    state_db.set_watchlist(candidates)
    state_db.set_meta("bot_active", True)
    logger.info("매수 후보 %d종목 저장: %s",
                len(candidates), [c["ticker"] for c in candidates])

    if candidates:
        lines = "\n".join(
            f"  {c['ticker']} {c.get('name','')}  "
            f"거래량{c.get('vol_ratio',0):.1f}배  "
            f"당일{c.get('day_return',0)*100:+.1f}%"
            for c in candidates
        )
        notify.send(f"📋 <b>오늘의 매수 후보 {len(candidates)}종목</b>\n{lines}")
    else:
        notify.send("📋 오늘의 매수 후보: 없음")

if __name__ == "__main__":
    main()
