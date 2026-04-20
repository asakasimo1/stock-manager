"""
사이클 트레이딩 로컬 데몬
- Gist의 profit_cycle_jobs.json을 30초마다 폴링
- waiting_buy / holding / waiting_rebuy 상태 감지 즉시 KIS 주문 실행
- GitHub Actions 5분 cron 대신 즉각 반응 (지연 0~30초)

실행:
  python daemon_cycle.py

종료:
  Ctrl+C
"""
import logging
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

import job_cycle_cloud   # 사이클 로직 재사용
import kis_api

KST = timezone(timedelta(hours=9))
POLL_INTERVAL = 30   # 초

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s KST [DAEMON] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def now_kst():
    return datetime.now(KST).strftime("%H:%M:%S")


def main():
    logger.info("=" * 50)
    logger.info("  사이클 트레이딩 데몬 시작")
    logger.info("  폴링 간격: %d초  (GitHub Actions 5분 대비 즉각 반응)", POLL_INTERVAL)
    logger.info("  종료: Ctrl+C")
    logger.info("=" * 50)

    while True:
        try:
            if kis_api.is_any_market_open():
                logger.info("[%s] 잡 체크 시작", now_kst())
                job_cycle_cloud.main()
            else:
                logger.info("[%s] 거래 시간 외 — 대기", now_kst())
        except Exception as e:
            logger.error("오류 발생: %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("데몬 종료")
