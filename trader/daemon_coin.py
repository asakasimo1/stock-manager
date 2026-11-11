"""
코인 자동매매 통합 데몬 (Oracle VM 상시 실행)
- coin_buy_jobs  : 매수 조건 체크 → Upbit 매수
- coin_sell_jobs : 수익매도 체크 → Upbit 매도
- 30초 폴링 (24/7, 시장 시간 제한 없음)

실행:
  python daemon_coin.py

종료:
  Ctrl+C
"""
import logging
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

import gist_writer
import job_coin_buy
import job_coin_sell
import job_coin_grid
import job_coin_balance

KST = timezone(timedelta(hours=9))
POLL_INTERVAL    = 30   # 초
BALANCE_INTERVAL = 300  # 잔고 Gist 갱신 주기 (5분)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s KST [COIN] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def now_kst():
    return datetime.now(KST).strftime("%H:%M:%S")


def main():
    logger.info("=" * 50)
    logger.info("  코인 자동매매 데몬 시작")
    logger.info("  폴링 간격: %d초  (24/7)", POLL_INTERVAL)
    logger.info("  종료: Ctrl+C")
    logger.info("=" * 50)

    last_balance_at = 0  # 마지막 잔고 갱신 epoch
    handled_req_ts = 0   # 처리한 웹 새로고침 요청의 requested_at(ms)

    while True:
        try:
            logger.info("[%s] 잡 체크 시작", now_kst())
            job_coin_grid.main()
            job_coin_buy.main()
            job_coin_sell.main()

            # 웹 "잔고 새로고침" 요청(2026-11-11~): Vercel은 업비트를 직접 호출하지 않고
            # Gist에 coin_balance_request.json 만 남긴다 → 여기서 확인해 즉시 갱신
            req_ts = 0
            try:
                req = gist_writer._read_gist_file("coin_balance_request.json")
                if isinstance(req, dict):
                    req_ts = int(req.get("requested_at") or 0)
            except Exception as re_:
                logger.warning("잔고 갱신 요청 확인 실패: %s", re_)
            requested = req_ts > handled_req_ts

            # 5분마다(또는 웹 요청 시 즉시) 업비트 잔고 → Gist 갱신
            now_epoch = time.time()
            if requested or now_epoch - last_balance_at >= BALANCE_INTERVAL:
                try:
                    job_coin_balance.run()
                    last_balance_at = now_epoch
                    if requested:
                        handled_req_ts = req_ts
                        logger.info("웹 잔고 새로고침 요청 처리 완료")
                except Exception as be:
                    logger.error("잔고 갱신 실패: %s", be)

            logger.info("[%s] 잡 체크 완료", now_kst())
        except Exception as e:
            logger.error("오류 발생: %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("데몬 종료")
