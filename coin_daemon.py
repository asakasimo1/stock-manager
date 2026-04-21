"""
코인 자동매매 데몬 — 30초마다 가격 체크 후 즉시 매수/매도
- 사이클 잡: job_coin_sell.process_cycle_jobs()
- 매수 잡:   job_coin_buy.process_cycle_buy()
- 잔고 현행화: job_coin_balance.run() (2분마다)
"""
import time
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

KST = timezone(timedelta(hours=9))
logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s KST %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("/home/ubuntu/coin_daemon.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

CHECK_INTERVAL_SEC  = 30   # 가격 체크 주기
BALANCE_INTERVAL_SEC = 120  # 잔고 현행화 주기


def main():
    import job_coin_buy
    import job_coin_sell
    import job_coin_balance

    logger.info("=== 코인 데몬 시작 (체크 주기: %ds) ===", CHECK_INTERVAL_SEC)
    last_balance = 0

    while True:
        now = time.monotonic()
        try:
            job_coin_buy.process_cycle_buy()
        except Exception as e:
            logger.error("매수 체크 오류: %s", e)

        try:
            job_coin_sell.process_cycle_jobs()
        except Exception as e:
            logger.error("매도/사이클 체크 오류: %s", e)

        if now - last_balance >= BALANCE_INTERVAL_SEC:
            try:
                job_coin_balance.run()
                last_balance = now
            except Exception as e:
                logger.error("잔고 업데이트 오류: %s", e)

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
