"""더스트 정리 지정가 매도 감시 — 타임아웃을 45분으로 재설정하면서 만든 통합 감시 스크립트.
기존 dust_cleanup.py / dust_cleanup_extra.py를 대체 (남은 미체결 17건을 지금 시점부터 45분 감시).
30초마다 체결 여부 확인, 45분 지나도 미체결이면 시장가로 강제청산."""
import time
import logging
import upbit_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dust_cleanup_watch")

TIMEOUT_SEC = 45 * 60
POLL_SEC = 30

TRACKED = [
    {"ticker": "KRW-YGG",    "sell_uuid": "fa5cdf2f-8620-413c-ab0d-7fff9bea8e36"},
    {"ticker": "KRW-ZIL",    "sell_uuid": "02a357b1-61b5-49d8-a65f-692080be29b6"},
    {"ticker": "KRW-PUNDIX", "sell_uuid": "e1dc49d5-62cc-4a40-905e-77d819a361ff"},
    {"ticker": "KRW-ORDER",  "sell_uuid": "ad53ac2b-f7ae-4515-b889-ce92a105508e"},
    {"ticker": "KRW-MET2",   "sell_uuid": "a9eedae2-17c6-4c69-a3e6-5470ef7a0b19"},
    {"ticker": "KRW-XEC",    "sell_uuid": "21186b68-b7f8-4fc3-81ee-a58e6f1ac662"},
    {"ticker": "KRW-ARX",    "sell_uuid": "13d0523b-5e5b-470f-ba39-ee86e2b08767"},
    {"ticker": "KRW-KITE",   "sell_uuid": "77506a96-5dba-4a10-adac-6642e3140a1a"},
    {"ticker": "KRW-ARB",    "sell_uuid": "5d3d6fe5-879d-4808-b5e7-b259c01a0200"},
    {"ticker": "KRW-QUID",   "sell_uuid": "cd36bfe3-166f-4949-809d-a7d0d1ab34ee"},
    {"ticker": "KRW-TT",     "sell_uuid": "5f61fb44-197d-43fb-90f6-f11d36049e8f"},
    {"ticker": "KRW-FIL",    "sell_uuid": "5c1de064-c5b1-4677-b5a3-1ccec1f71dcd"},
    {"ticker": "KRW-SUI",    "sell_uuid": "87ff2417-8a88-4b8c-94fc-562ee03295da"},
    {"ticker": "KRW-XLM",    "sell_uuid": "72ce1aee-6fdf-4b83-a7b7-7d4492c6c094"},
    {"ticker": "KRW-0G",     "sell_uuid": "19e71855-6f47-46a9-a4f7-b0b88d38b49a"},
    {"ticker": "KRW-XPL",    "sell_uuid": "25a826c3-e86b-4dc3-983b-9639c95ce859"},
    {"ticker": "KRW-NEAR",   "sell_uuid": "022d50f8-4b91-4a6d-89cf-5119db85eb63"},
]


def main():
    now = time.time()
    pending = {t["ticker"]: {**t, "placed_at": now} for t in TRACKED}
    logger.info("감시 시작: %d건, 마감 %d분 후(%s)", len(pending), TIMEOUT_SEC // 60,
                time.strftime("%H:%M:%S", time.localtime(now + TIMEOUT_SEC)))
    while pending:
        time.sleep(POLL_SEC)
        for ticker, info in list(pending.items()):
            try:
                o = upbit_api.get_order(info["sell_uuid"])
            except Exception as e:
                logger.warning("[%s] 매도 상태 확인 실패: %s", ticker, e)
                continue
            if o["remaining_volume"] == 0:
                logger.info("[%s] 지정가 매도 체결 완료", ticker)
                del pending[ticker]
                continue
            elapsed = time.time() - info["placed_at"]
            if elapsed >= TIMEOUT_SEC:
                logger.warning("[%s] %d초 경과, 미체결 — 시장가로 강제청산", ticker, int(elapsed))
                try:
                    upbit_api.cancel_order(info["sell_uuid"])
                    time.sleep(1)
                    bal = upbit_api.get_balance()
                    h = next((x for x in bal["holdings"] if x["ticker"] == ticker), None)
                    if h and h["qty"] > 0:
                        upbit_api.place_order(market=ticker, side="ask", ord_type="market", volume=h["qty"])
                        logger.info("[%s] 시장가 강제청산 완료", ticker)
                except Exception as e:
                    logger.error("[%s] 강제청산 실패: %s", ticker, e)
                del pending[ticker]
    logger.info("전체 정리 완료")


if __name__ == "__main__":
    main()
