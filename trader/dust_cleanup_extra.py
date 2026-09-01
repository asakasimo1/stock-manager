"""dust_cleanup.py 1차 배치에서 잔고 반영 지연(레이스)으로 실패했던 3건(ZIL/ZKP/YGG)을
place_order 가격 포맷 버그 수정 후 수동으로 재등록한 지정가 매도 주문 감시용 보조 스크립트.
동일하게 30분 미체결 시 시장가로 강제청산."""
import time
import logging
import upbit_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dust_cleanup_extra")

TIMEOUT_SEC = 30 * 60
POLL_SEC = 30

TRACKED = [
    {"ticker": "KRW-ZIL", "sell_uuid": "02a357b1-61b5-49d8-a65f-692080be29b6", "placed_at": time.time()},
    {"ticker": "KRW-YGG", "sell_uuid": "fa5cdf2f-8620-413c-ab0d-7fff9bea8e36", "placed_at": time.time()},
    {"ticker": "KRW-ZKP", "sell_uuid": "3417cf57-7d26-4efb-a36c-e24fae34aeb2", "placed_at": time.time()},
]


def main():
    pending = {t["ticker"]: t for t in TRACKED}
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
    logger.info("보조 정리 완료")


if __name__ == "__main__":
    main()
