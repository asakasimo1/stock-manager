"""
초단타(스캘핑) 통합 데몬 (Oracle VM 상시 실행) — 기존 daemon_coin/daemon_stock과 분리된 별도 프로세스
- job_scalp_coin  : 5초마다 실행 (24/7)
- job_scalp_stock : 10초마다 실행 (장중에만, kis_api.is_any_market_open() 게이트는 job 내부에서 처리)

실행:
  python daemon_scalp.py

종료:
  Ctrl+C
"""
import logging
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

import job_scalp_coin
import job_scalp_stock

KST = timezone(timedelta(hours=9))
COIN_INTERVAL  = 5   # 초
STOCK_INTERVAL = 10  # 초
TICK           = 5   # 루프 기본 틱 (COIN_INTERVAL과 동일해야 함)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s KST [SCALP] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def now_kst():
    return datetime.now(KST).strftime("%H:%M:%S")


def main():
    logger.info("=" * 50)
    logger.info("  초단타(스캘핑) 데몬 시작")
    logger.info("  코인 %d초 / 주식 %d초 폴링", COIN_INTERVAL, STOCK_INTERVAL)
    logger.info("  종료: Ctrl+C")
    logger.info("=" * 50)

    last_stock_at = 0.0

    while True:
        try:
            job_scalp_coin.main()

            now_epoch = time.time()
            if now_epoch - last_stock_at >= STOCK_INTERVAL:
                job_scalp_stock.main()
                last_stock_at = now_epoch
        except Exception as e:
            logger.error("오류 발생: %s", e)

        time.sleep(TICK)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("데몬 종료")
