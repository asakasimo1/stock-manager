"""
Job 1 — 신호 생성 (08:50 장전 / 10:15·13:15 장중)
모멘텀 + 반등 포착 통합 시그널을 Supabase watchlist에 저장
"""

import logging
from dotenv import load_dotenv
from signals import get_combined_signals
import state_db
import notify

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SIGNAL_ICON = {"momentum": "📈", "rebound": "🔄"}

def main():
    logger.info("=== 통합 신호 생성 시작 ===")
    candidates = get_combined_signals()
    state_db.set_watchlist(candidates)
    state_db.set_meta("bot_active", True)
    logger.info("매수 후보 %d종목 저장: %s",
                len(candidates), [c["ticker"] for c in candidates])

    if candidates:
        lines = "\n".join(
            f"  {SIGNAL_ICON.get(c.get('signal_type',''), '📋')} "
            f"{c['ticker']} {c.get('name','')}  "
            f"거래량{c.get('vol_ratio',0):.1f}배  "
            f"당일{c.get('day_return',0)*100:+.1f}%"
            + (f"  5일{c.get('mom5',0)*100:+.1f}%" if c.get('signal_type') == 'rebound' else "")
            for c in candidates
        )
        notify.send(f"📋 <b>매수 후보 {len(candidates)}종목</b>\n{lines}")
    else:
        notify.send("📋 매수 후보: 없음")

if __name__ == "__main__":
    main()
