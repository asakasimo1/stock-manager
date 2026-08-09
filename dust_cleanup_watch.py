"""더스트 정리 지정가 매도 감시 (v3: 마감시각을 사용자 요청으로 당일 22:00까지 연장).
기존 dust_cleanup_watch.py를 대체."""
import time
import logging
import upbit_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dust_cleanup_watch")

DEADLINE = 1786280400  # 2026-08-09 22:00:00 KST (사용자 요청으로 연장)
POLL_SEC = 30

TRACKED = [
    {"ticker": "KRW-ZIL",    "sell_uuid": "02a357b1-61b5-49d8-a65f-692080be29b6", "avg_price": 3.11995982,    "qty": 1605.78990901},
    {"ticker": "KRW-MET2",   "sell_uuid": "a9eedae2-17c6-4c69-a3e6-5470ef7a0b19", "avg_price": 237.0019813,   "qty": 21.11796691},
    {"ticker": "KRW-XEC",    "sell_uuid": "21186b68-b7f8-4fc3-81ee-a58e6f1ac662", "avg_price": 0.00959973,    "qty": 521368.66523911},
    {"ticker": "KRW-ARX",    "sell_uuid": "13d0523b-5e5b-470f-ba39-ee86e2b08767", "avg_price": 186.04707272,  "qty": 26.92866879},
    {"ticker": "KRW-KITE",   "sell_uuid": "77506a96-5dba-4a10-adac-6642e3140a1a", "avg_price": 141.00776421,  "qty": 35.52995842},
    {"ticker": "KRW-ARB",    "sell_uuid": "5d3d6fe5-879d-4808-b5e7-b259c01a0200", "avg_price": 110.00392087,  "qty": 45.54383117},
    {"ticker": "KRW-QUID",   "sell_uuid": "cd36bfe3-166f-4949-809d-a7d0d1ab34ee", "avg_price": 130.0,         "qty": 38.53846154},
    {"ticker": "KRW-SUI",    "sell_uuid": "87ff2417-8a88-4b8c-94fc-562ee03295da", "avg_price": 974.98996862,  "qty": 5.1385144},
    {"ticker": "KRW-XLM",    "sell_uuid": "72ce1aee-6fdf-4b83-a7b7-7d4492c6c094", "avg_price": 231.99187618,  "qty": 21.59558378},
    {"ticker": "KRW-XPL",    "sell_uuid": "25a826c3-e86b-4dc3-983b-9639c95ce859", "avg_price": 108.01153805,  "qty": 46.43022487},
]


def _raw_order(uuid: str) -> dict:
    """get_order() 래퍼는 executed_funds/paid_fee를 안 주므로(ask 주문 avg_price도 0으로 옴)
    수익 계산에 필요한 원본 필드를 얻기 위해 직접 조회"""
    r = upbit_api._session.get(f"{upbit_api.BASE_URL}/order",
                                params={"uuid": uuid}, headers=upbit_api._auth_header({"uuid": uuid}), timeout=10)
    r.raise_for_status()
    return r.json()


def _report_fill(ticker: str, avg_price: float, sell_uuid: str, forced: bool):
    """체결 완료 시 종목명/매수단가/매도단가/매수금액/매도금액/수익금을 계산해서 한 줄로 기록"""
    name = upbit_api.COIN_NAMES.get(ticker, ticker)
    try:
        raw = _raw_order(sell_uuid)
        executed = float(raw["executed_volume"])
        # 단건 조회(/order)는 executed_funds 필드가 없음 — trades[].funds 합산으로 계산
        executed_funds = sum(float(t["funds"]) for t in raw.get("trades", []))
        paid_fee = float(raw["paid_fee"])
        sell_unit = executed_funds / executed if executed else 0
        buy_amount = avg_price * executed
        sell_amount = executed_funds - paid_fee
        profit = sell_amount - buy_amount
        logger.info("[체결%s] %s(%s)  매수단가=%.4f  매도단가=%.4f  매수금액=%.0f원  매도금액=%.0f원  수익금=%+.0f원",
                    " (시장가 강제청산)" if forced else "", name, ticker, avg_price, sell_unit, buy_amount, sell_amount, profit)
    except Exception as e:
        logger.warning("[%s] 체결 상세 조회 실패(체결 자체는 완료): %s", ticker, e)


def main():
    pending = {t["ticker"]: t for t in TRACKED}
    remaining_min = max(0, int((DEADLINE - time.time()) / 60))
    logger.info("감시 재시작: %d건, 마감까지 약 %d분 남음(%s)", len(pending), remaining_min,
                time.strftime("%H:%M:%S", time.localtime(DEADLINE)))
    while pending:
        time.sleep(POLL_SEC)
        for ticker, info in list(pending.items()):
            try:
                o = upbit_api.get_order(info["sell_uuid"])
            except Exception as e:
                logger.warning("[%s] 매도 상태 확인 실패: %s", ticker, e)
                continue
            if o["remaining_volume"] == 0:
                _report_fill(ticker, info["avg_price"], info["sell_uuid"], forced=False)
                del pending[ticker]
                continue
            if time.time() >= DEADLINE:
                logger.warning("[%s] 마감 시각 경과, 미체결 — 시장가로 강제청산", ticker)
                try:
                    upbit_api.cancel_order(info["sell_uuid"])
                    time.sleep(1)
                    bal = upbit_api.get_balance()
                    h = next((x for x in bal["holdings"] if x["ticker"] == ticker), None)
                    if h and h["qty"] > 0:
                        result = upbit_api.place_order(market=ticker, side="ask", ord_type="market", volume=h["qty"])
                        time.sleep(1)
                        _report_fill(ticker, info["avg_price"], result["uuid"], forced=True)
                except Exception as e:
                    logger.error("[%s] 강제청산 실패: %s", ticker, e)
                del pending[ticker]
    logger.info("전체 정리 완료")


if __name__ == "__main__":
    main()
