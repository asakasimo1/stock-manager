"""
더스트(초단타 매매 잔여 미세수량) 정리 — 1회성 스크립트
사용자 요청: 평가액 500원 미만 잔량을 5,000원(업비트 최소 주문금액)씩 추가매수해서
매도 가능한 크기로 만든 뒤, 업비트가 관리하는 평단가 기준 수수료 커버 + 여유분(0.2%)을 더한
가격으로 지정가 매도를 건다. 30분 내 미체결이면 시장가로 강제청산.
KRW 잔고가 부족해 한 번에 다 못 하면, 처리 가능한 만큼 먼저 하고 그 배치가 전부 정리(매도 완료)된
뒤 남은 더스트로 다음 배치를 이어서 진행한다 (전부 끝날 때까지 반복).

⚠ 그리드매매 등 다른 시스템이 들고 있는 진짜 포지션(KRW-XRP 등)은 평가액이 훨씬 크므로
DUST_MAX_KRW 임계값에 안전하게 걸리지 않는다 — 그래도 이중 안전장치로 EXCLUDE에 명시.

실행: nohup python3 dust_cleanup.py > dust_cleanup.log 2>&1 &
"""
import time
import logging
import upbit_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("dust_cleanup")

BUY_KRW = 5000            # 업비트 최소 주문금액
MIN_ORDER_KRW = 5010      # 이 금액 미만이면 이번 배치는 여기까지 (여유 10원)
TARGET_MARGIN = 1.002     # 평단가 대비 매도 목표가 배수 — 수수료 커버 + 0.2% 여유
TIMEOUT_SEC = 30 * 60     # 지정가 매도 30분 미체결 시 시장가로 강제청산
POLL_SEC = 30
DUST_MAX_KRW = 500        # 이 금액 미만만 "더스트"로 간주 (그리드매매 등 실제 포지션 절대 안 건드림)
EXCLUDE = {"KRW-XRP"}     # 이중 안전장치


def get_dust_candidates():
    bal = upbit_api.get_balance()
    dust = [h for h in bal["holdings"]
            if h["ticker"] not in EXCLUDE and 0 < h["eval_amount"] < DUST_MAX_KRW]
    return dust, bal["krw_avail"]


def _wait_filled(order_uuid: str, tries: int = 10, delay: float = 1.0):
    for _ in range(tries):
        time.sleep(delay)
        try:
            o = upbit_api.get_order(order_uuid)
            if o["remaining_volume"] == 0 and o["executed_volume"] > 0:
                return o
        except Exception as e:
            logger.warning("체결 확인 실패 %s: %s", order_uuid, e)
    return None


def process_one(ticker: str):
    """5,000원 매수 → 블렌디드 평단가 기준 지정가 매도 등록. 반환: 추적 정보 dict (실패 시 None)"""
    logger.info("[%s] %d원 매수 시작", ticker, BUY_KRW)
    buy = upbit_api.place_order(market=ticker, side="bid", ord_type="price", price=BUY_KRW)
    filled = _wait_filled(buy["uuid"])
    if not filled:
        logger.warning("[%s] 매수 체결 확인 타임아웃 — 잔고 기준으로 계속 진행", ticker)

    time.sleep(1)
    bal = upbit_api.get_balance()
    h = next((x for x in bal["holdings"] if x["ticker"] == ticker), None)
    if not h or h["qty"] <= 0:
        logger.error("[%s] 매수 후 잔고 확인 실패 — 건너뜀", ticker)
        return None

    avg_price = h["avg_price"]
    qty = h["qty"]
    if avg_price <= 0:
        logger.error("[%s] 평단가 조회 이상(%.4f) — 건너뜀", ticker, avg_price)
        return None

    target_price = upbit_api.round_ask_price(avg_price / (1 - upbit_api.SELL_FEE) * TARGET_MARGIN)
    sell = upbit_api.place_order(market=ticker, side="ask", ord_type="limit", price=target_price, volume=qty)
    logger.info("[%s] 지정가 매도 등록: qty=%.8f 목표가=%s원 (평단가=%s원)", ticker, qty, target_price, avg_price)
    return {"ticker": ticker, "sell_uuid": sell["uuid"], "qty": qty, "price": target_price, "placed_at": time.time()}


def monitor_and_force(tracked: list):
    """등록된 지정가 매도를 지켜보다가 체결되면 제외, 타임아웃되면 시장가로 강제청산"""
    pending = {t["ticker"]: t for t in tracked}
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
    logger.info("이번 배치 전체 정리 완료")


def run_batch() -> bool:
    candidates, krw_avail = get_dust_candidates()
    if not candidates:
        logger.info("정리할 더스트 없음")
        return False

    candidates.sort(key=lambda h: -h["eval_amount"])
    logger.info("이번 배치 대상 %d개, 주문가능 KRW=%.0f원", len(candidates), krw_avail)

    tracked = []
    for h in candidates:
        krw_avail = upbit_api.get_balance()["krw_avail"]
        if krw_avail < MIN_ORDER_KRW:
            logger.info("KRW 부족(%.0f원) — 이번 배치는 %d개 처리 후 종료", krw_avail, len(tracked))
            break
        try:
            info = process_one(h["ticker"])
            if info:
                tracked.append(info)
        except Exception as e:
            logger.error("[%s] 처리 실패: %s", h["ticker"], e)
        time.sleep(1)

    if not tracked:
        logger.warning("이번 배치에서 처리된 코인 없음 — 종료")
        return False

    monitor_and_force(tracked)
    return True


def main():
    batch_num = 1
    while True:
        logger.info("=== %d차 배치 시작 ===", batch_num)
        if not run_batch():
            break
        batch_num += 1
    logger.info("모든 더스트 정리 완료 (총 %d개 배치)", batch_num)


if __name__ == "__main__":
    main()
