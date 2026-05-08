"""
주식 자동매매 통합 데몬 (Oracle VM 상시 실행)
- profit_buy_jobs  : 매수 조건 체크 → KIS 매수
- profit_sell_jobs : 수익매도 체크 → KIS 매도
- profit_cycle_jobs: 사이클 트레이딩
- 30초 폴링 (GitHub Actions 5분 cron 대체)

실행:
  python daemon_stock.py

종료:
  Ctrl+C
"""
import logging
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

import job_profit_buy_cloud
import job_profit_sell_cloud
import job_cycle_cloud
import job_balance
import kis_api

KST = timezone(timedelta(hours=9))
POLL_INTERVAL  = 30   # 초
BALANCE_EVERY  = 10   # N 사이클(=5분)마다 잔고 갱신

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s KST [STOCK] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def now_kst():
    return datetime.now(KST).strftime("%H:%M:%S")


def main():
    logger.info("=" * 50)
    logger.info("  주식 자동매매 데몬 시작")
    logger.info("  폴링 간격: %d초", POLL_INTERVAL)
    logger.info("  잔고 갱신: %d사이클마다 (약 %d분)", BALANCE_EVERY, POLL_INTERVAL * BALANCE_EVERY // 60)
    logger.info("  종료: Ctrl+C")
    logger.info("=" * 50)

    cycle = 0
    while True:
        try:
            if kis_api.is_any_market_open():
                logger.info("[%s] 잡 체크 시작 (사이클 %d)", now_kst(), cycle)
                job_profit_buy_cloud.main()
                job_profit_sell_cloud.main()
                job_cycle_cloud.main()
                # 매 N 사이클마다 잔고 Gist 업데이트 (heartbeat + 대시보드 갱신)
                if cycle % BALANCE_EVERY == 0:
                    try:
                        job_balance.main()
                    except Exception as be:
                        logger.warning("잔고 갱신 실패: %s", be)
                logger.info("[%s] 잡 체크 완료", now_kst())
            else:
                logger.info("[%s] 거래 시간 외 — 대기", now_kst())
        except Exception as e:
            logger.error("오류 발생: %s", e)

        cycle += 1
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("데몬 종료")
