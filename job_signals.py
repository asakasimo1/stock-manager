"""
Job 1 — 08:50 신호 생성
기술적 시그널(거래량+모멘텀) 종목을 Supabase watchlist에 저장
"""

import logging
from dotenv import load_dotenv
from signals import get_double_signals
import state_db

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def main():
    logger.info("=== 신호 생성 시작 (08:50) ===")
    # 장 전이므로 기술적 시그널만 사용
    candidates = get_double_signals(use_foreign=False)
    state_db.set_watchlist(candidates)
    state_db.set_meta("bot_active", True)
    logger.info("매수 후보 %d종목 저장: %s",
                len(candidates), [c["ticker"] for c in candidates])

if __name__ == "__main__":
    main()
