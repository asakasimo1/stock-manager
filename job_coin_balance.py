"""
코인 잔고 조회 잡 — Upbit 계좌 잔고를 Gist coin_account.json 에 저장
+ 업비트 실제 체결 내역을 trader_trades.json에 통합 기록 (reconcile)
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


def _reconcile_trades():
    """업비트 실제 체결 전체(그리드/사이클/스캘핑 잡 + 업비트 앱 수동주문 포함)를
    조회해서 아직 trader_trades.json에 없는 체결만 uuid 기준 중복 없이 추가.
    job_balance.py(주식, KIS 체결 통합기록)와 동일한 역할 — 이게 없어서 스캘핑
    외(그리드/사이클/수동) 코인 매도가 거래내역/챗봇 조회에서 통째로 누락되고
    있었음(2026-08-10 확인)."""
    try:
        orders = upbit_api.get_closed_orders(state="done", limit=100)
    except Exception as e:
        logger.warning("업비트 체결조회 실패, 거래내역 반영 건너뜀: %s", e)
        return
    if not orders:
        return

    existing = gist_writer._read_trades()
    known = {t.get("order_no") for t in existing if t.get("order_no")}

    new_count = 0
    for o in orders:
        uid = o["uuid"]
        if not uid or uid in known:
            continue
        try:
            detail = upbit_api.get_order_detail(uid)
        except Exception as e:
            logger.warning("주문 상세조회 실패 %s: %s", uid, e)
            continue
        if detail["executed_volume"] <= 0:
            continue
        market = detail["market"] or o["market"]
        gist_writer.log_trade(
            ticker=market,
            name=upbit_api.COIN_NAMES.get(market, market),
            trade_type="buy" if detail["side"] == "bid" else "sell",
            price=detail["avg_price"],
            qty=detail["executed_volume"],
            order_no=uid,
        )
        new_count += 1

    if new_count:
        gist_writer.flush_trades()
        logger.info("코인 거래내역 Gist 반영: 신규 체결 %d건", new_count)


def run():
    try:
        bal = upbit_api.get_balance()
        ok  = gist_writer._write_gist({"coin_account.json": bal})
        logger.info("코인 잔고 Gist 업데이트 %s — KRW %s원  보유 %d종",
                    "완료" if ok else "실패",
                    f"{bal['krw']:,.0f}", len(bal['holdings']))
    except Exception as e:
        logger.error("코인 잔고 업데이트 실패: %s", e)

    try:
        _reconcile_trades()
    except Exception as e:
        logger.warning("거래내역 반영 건너뜀: %s", e)


if __name__ == "__main__":
    run()
