"""
코인 잔고 조회 잡 — Upbit 계좌 잔고를 Gist coin_account.json 에 저장
"""
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import upbit_api
import gist_writer

load_dotenv()
KST = timezone(timedelta(hours=9))
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
logger = logging.getLogger(__name__)


def run():
    try:
        bal = upbit_api.get_balance()
        ok  = gist_writer._write_gist({"coin_account.json": bal})
        logger.info("코인 잔고 Gist 업데이트 %s — KRW %s원  보유 %d종",
                    "완료" if ok else "실패",
                    f"{bal['krw']:,.0f}", len(bal['holdings']))
    except Exception as e:
        logger.error("코인 잔고 업데이트 실패: %s", e)


if __name__ == "__main__":
    run()
