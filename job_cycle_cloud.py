"""
사이클 트레이딩 잡 — GitHub Actions에서 5분마다 실행
Gist profit_cycle_jobs.json에서 잡 읽기 → 현재 phase에 따라 매수/매도 실행

사이클 흐름:
  [waiting_buy]  → 현재가 <= buy_price  → 매수 실행 → [holding]
  [holding]      → 현재가 >= sell_price → 매도 실행 → [waiting_rebuy]
  [waiting_rebuy]→ 현재가 <= rebuy_price→ 매수 실행 → [holding] (사이클 반복)

NXT 시간대 (08:00~08:50, 15:30~20:00):
  시장가 불가 → 목표가에 즉시 지정가 주문 후 체결 감지 방식으로 동작
  - waiting_buy  : buy_price(또는 현재가)로 지정가 매수 즉시 제출
  - holding      : sell_price로 지정가 매도 즉시 제출 (가격 폴링 없음)
  - waiting_rebuy: rebuy_price로 지정가 재매수 즉시 제출

최초 등록 시:
  - buy_price  : 등록 시 즉시 매수(market) 또는 지정가 이하 시 매수
  - take_pct   : 매수 후 이 수익률 달성 시 매도 (기본 3%)
  - rebuy_drop : 매도 후 이 비율 하락 시 재매수 (기본 2%)
  - repeat_take: 재매수 후 이 수익률 달성 시 재매도 (기본 2%)
  - max_cycles : 최대 반복 횟수 (0=무제한)
"""
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import kis_api
import gist_writer
import job_balance

KST = timezone(timedelta(hours=9))

logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
_fmt = logging.Formatter("%(asctime)s KST %(levelname)s %(message)s")
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("cycle_cloud.log", encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)

BUY_FEE  = 0.00015   # 0.015%
SELL_FEE = 0.00195   # 0.015% + 0.18% (거래세)

def calc_sell_price(buy_price: int, qty: int, take_pct: float) -> int:
    """수수료 포함 take_pct 순이익률을 달성하는 최소 매도 단가"""
    target_gross = buy_price * qty * (1 + BUY_FEE) * (1 + take_pct)
    sell_unit    = target_gross / qty / (1 - SELL_FEE)
    return math.ceil(sell_unit) + 1

def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def main():
    if not kis_api.is_any_market_open():
        logger.info("거래 시간 외 (KRX/NXT 모두 닫힘) — 종료")
        return

    nxt_mode = kis_api._is_nxt_time()
    logger.info("사이클 잡 체크 시작 [%s]", "NXT 시간대" if nxt_mode else "KRX 정규")

    jobs = gist_writer._read_gist_file("profit_cycle_jobs.json") or []
    active = [j for j in jobs if j.get("status") not in ("done", "cancelled", "stopped")]

    if not active:
        logger.info("활성 사이클 잡 없음 — 종료")
        return

    logger.info("활성 잡 %d개 처리", len(active))
    changed = False

    # ── NXT: 제출된 지정가 주문 체결 감지용 미체결 목록 1회 조회 ──
    has_nxt_submitted = any(j.get("nxt_submitted") for j in active)
    pending_order_nos: set = set()
    pending_fetched = False
    if nxt_mode and has_nxt_submitted:
        try:
            pending_order_nos = {o["order_no"] for o in kis_api.get_pending_orders()}
            pending_fetched = True
            logger.info("미체결 주문 %d건 조회 완료", len(pending_order_nos))
        except Exception as e:
            logger.warning("미체결 주문 조회 실패 (체결 감지 건너뜀): %s", e)

    # ── KRX 정규장: nxt_submitted 플래그 초기화 (NXT 주문 만료) ──
    if not nxt_mode:
        for job in active:
            if job.get("nxt_submitted"):
                logger.info("%s(%s) KRX 정규장 진입 — NXT 지정가 주문 만료, 플래그 초기화",
                            job.get("name", job["ticker"]), job["ticker"])
                job["nxt_submitted"] = False
                job["nxt_order_no"]  = ""
                changed = True

    # ── 활성 잡 현재가 일괄 병렬 조회 ──────────────────────────────
    active_tickers = list({j["ticker"] for j in active})

    def _fetch_price(ticker: str):
        try:
            return ticker, kis_api.get_price(ticker)
        except Exception as e:
            logger.error("현재가 조회 실패 %s: %s", ticker, e)
            return ticker, None

    price_cache: dict = {}
    with ThreadPoolExecutor(max_workers=min(len(active_tickers), 5)) as ex:
        futures = {ex.submit(_fetch_price, t): t for t in active_tickers}
        for fut in as_completed(futures):
            ticker_key, result = fut.result()
            if result is not None:
                price_cache[ticker_key] = result

    logger.info("현재가 병렬 조회 완료 (%d/%d)", len(price_cache), len(active_tickers))

    for job in jobs:
        phase = job.get("phase", "waiting_buy")
        if job.get("status") in ("done", "cancelled", "stopped"):
            continue

        ticker      = job["ticker"]
        name        = job.get("name", ticker)
        qty         = int(job.get("qty", 0))
        take_pct    = float(job.get("take_pct",    0.03))
        rebuy_drop  = float(job.get("rebuy_drop",  0.02))
        repeat_take = float(job.get("repeat_take", 0.02))
        max_cycles  = int(job.get("max_cycles", 0))
        cycle_no    = int(job.get("cycle_no", 0))

        try:
            info = price_cache.get(ticker)
            if info is None:
                logger.warning("%s 현재가 캐시 없음 — 건너뜀", ticker)
                continue
            cur_price = int(info["stck_prpr"])

            # ══════════════════════════════════════════════════
            # NXT 시간대: 목표가 지정가 주문 즉시 제출 방식
            # ══════════════════════════════════════════════════
            if nxt_mode:
                nxt_submitted = job.get("nxt_submitted", False)
                nxt_order_no  = job.get("nxt_order_no", "")

                # ── NXT waiting_buy: 목표 매수가(또는 현재가)로 즉시 지정가 제출 ──
                if phase == "waiting_buy":
                    buy_price   = int(job.get("buy_price", 0))
                    order_price = buy_price if buy_price > 0 else cur_price
                    if qty <= 0 and job.get("amount", 0) > 0:
                        qty = int(job["amount"]) // order_price
                        if qty < 1:
                            logger.warning("%s 금액 부족 — 매수 불가", name)
                            continue
                        job["qty"] = qty
                    sell_price = calc_sell_price(order_price, qty, take_pct)
                    logger.info("%s(%s) [NXT waiting_buy] 지정가 매수 제출 — %d원 × %d주 / 목표매도=%d원",
                                name, ticker, order_price, qty, sell_price)
                    result = kis_api.place_order(ticker, "BUY", qty, price=order_price, order_type="limit")
                    job["phase"]       = "holding"
                    job["buy_price"]   = order_price
                    job["sell_price"]  = sell_price
                    job["cycle_no"]    = 1
                    job["last_buy_at"] = now_kst()
                    job["order_no"]    = result.get("order_no", "")
                    job["nxt_submitted"] = True
                    job["nxt_order_no"]  = result.get("order_no", "")
                    changed = True
                    logger.info("✅ [NXT 매수 제출] %s %d주 @ %d원  주문번호: %s",
                                ticker, qty, order_price, job["order_no"])

                # ── NXT holding: sell_price로 지정가 매도 즉시 제출 ──
                elif phase == "holding":
                    sell_price = int(job.get("sell_price", 0))
                    if not nxt_submitted:
                        # 아직 NXT 주문 미제출 → 목표 매도가로 즉시 지정가 제출
                        logger.info("%s(%s) [NXT holding] 목표매도가 %d원 지정가 제출",
                                    name, ticker, sell_price)
                        result = kis_api.place_order(ticker, "SELL", qty, price=sell_price, order_type="limit")
                        job["nxt_submitted"] = True
                        job["nxt_order_no"]  = result.get("order_no", "")
                        job["order_no"]      = result.get("order_no", "")
                        changed = True
                        logger.info("✅ [NXT 매도 제출] %s %d주 @ %d원  주문번호: %s",
                                    ticker, qty, sell_price, job["nxt_order_no"])
                    else:
                        # 이미 제출됨 → 체결 감지
                        order_filled = pending_fetched and nxt_order_no not in pending_order_nos
                        if order_filled:
                            rebuy_price = math.floor(sell_price * (1 - rebuy_drop))
                            job["phase"]        = "waiting_rebuy"
                            job["last_sell"]    = sell_price
                            job["rebuy_price"]  = rebuy_price
                            job["last_sell_at"] = now_kst()
                            job["nxt_submitted"] = False
                            job["nxt_order_no"]  = ""
                            changed = True
                            logger.info("✅ [NXT 매도 체결] %s @ %d원  재매수 목표=%d원",
                                        ticker, sell_price, rebuy_price)
                            if max_cycles > 0 and cycle_no >= max_cycles:
                                job["phase"]  = "done"
                                job["status"] = "done"
                                logger.info("%s 최대 사이클(%d회) 완료", name, max_cycles)
                        else:
                            logger.info("%s(%s) [NXT 매도 대기 중] 주문번호: %s  목표: %d원",
                                        name, ticker, nxt_order_no, sell_price)

                # ── NXT waiting_rebuy: rebuy_price로 지정가 재매수 즉시 제출 ──
                elif phase == "waiting_rebuy":
                    rebuy_price = int(job.get("rebuy_price", 0))
                    if not nxt_submitted:
                        logger.info("%s(%s) [NXT waiting_rebuy] 재매수가 %d원 지정가 제출",
                                    name, ticker, rebuy_price)
                        result = kis_api.place_order(ticker, "BUY", qty, price=rebuy_price, order_type="limit")
                        job["nxt_submitted"] = True
                        job["nxt_order_no"]  = result.get("order_no", "")
                        job["order_no"]      = result.get("order_no", "")
                        changed = True
                        logger.info("✅ [NXT 재매수 제출] %s %d주 @ %d원  주문번호: %s",
                                    ticker, qty, rebuy_price, job["nxt_order_no"])
                    else:
                        # 체결 감지
                        order_filled = pending_fetched and nxt_order_no not in pending_order_nos
                        if order_filled:
                            new_cycle  = cycle_no + 1
                            sell_price = calc_sell_price(rebuy_price, qty, repeat_take)
                            job["phase"]         = "holding"
                            job["buy_price"]     = rebuy_price
                            job["sell_price"]    = sell_price
                            job["cycle_no"]      = new_cycle
                            job["last_buy_at"]   = now_kst()
                            job["nxt_submitted"] = False
                            job["nxt_order_no"]  = ""
                            changed = True
                            logger.info("✅ [NXT 재매수 체결] %s @ %d원  목표매도=%d원",
                                        ticker, rebuy_price, sell_price)
                        else:
                            logger.info("%s(%s) [NXT 재매수 대기 중] 주문번호: %s  목표: %d원",
                                        name, ticker, nxt_order_no, rebuy_price)

            # ══════════════════════════════════════════════════
            # KRX 정규장: 기존 폴링 방식
            # ══════════════════════════════════════════════════
            else:
                order_dvsn = job.get("order_dvsn", "market")

                # ── phase: waiting_buy (최초 매수 대기) ──────────────
                if phase == "waiting_buy":
                    buy_price = int(job.get("buy_price", 0))
                    cond_type = job.get("condition_type", "market")
                    should_buy = (cond_type == "market") or (buy_price > 0 and cur_price <= buy_price)
                    logger.info("%s(%s) [%s] 현재가=%d 매수가=%d → %s",
                                name, ticker, phase, cur_price, buy_price,
                                "★ 매수" if should_buy else "대기")
                    if should_buy:
                        if qty <= 0 and job.get("amount", 0) > 0:
                            qty = int(job["amount"]) // cur_price
                            if qty < 1:
                                logger.warning("%s 금액 부족 — 매수 불가", name)
                                continue
                            job["qty"] = qty
                        if order_dvsn == "limit":
                            limit_price = int(job.get("limit_price", 0)) or cur_price
                            result = kis_api.place_order(ticker, "BUY", qty, price=limit_price, order_type="limit")
                            actual_price = limit_price
                            order_label  = f"지정가({limit_price:,}원)"
                        else:
                            result = kis_api.place_order(ticker, "BUY", qty, order_type="market")
                            actual_price = cur_price
                            order_label  = "시장가"
                        sell_price = calc_sell_price(actual_price, qty, take_pct)
                        job["phase"]       = "holding"
                        job["buy_price"]   = actual_price
                        job["sell_price"]  = sell_price
                        job["cycle_no"]    = 1
                        job["last_buy_at"] = now_kst()
                        job["order_no"]    = result.get("order_no", "")
                        job["status"]      = "active"
                        changed = True
                        logger.info("✅ [사이클1] 매수(%s): %s %d주 @ %d원  목표매도가=%d원",
                                    order_label, ticker, qty, actual_price, sell_price)

                # ── phase: holding (매도 대기) ────────────────────────
                elif phase == "holding":
                    sell_price  = int(job.get("sell_price", 0))
                    should_sell = cur_price >= sell_price
                    logger.info("%s(%s) [holding #%d] 현재가=%d 목표매도가=%d → %s",
                                name, ticker, cycle_no, cur_price, sell_price,
                                "★ 매도" if should_sell else "대기")
                    if should_sell:
                        try:
                            if order_dvsn == "limit":
                                result = kis_api.place_order(ticker, "SELL", qty, price=sell_price, order_type="limit")
                                order_label = f"지정가({sell_price:,}원)"
                            else:
                                result = kis_api.place_order(ticker, "SELL", qty, order_type="market")
                                order_label = "시장가"
                            rebuy_price = math.floor(cur_price * (1 - rebuy_drop))
                            job["phase"]        = "waiting_rebuy"
                            job["last_sell"]    = cur_price
                            job["rebuy_price"]  = rebuy_price
                            job["last_sell_at"] = now_kst()
                            job["order_no"]     = result.get("order_no", "")
                            changed = True
                            logger.info("✅ [사이클%d] 매도(%s): %s %d주 @ %d원  재매수 목표=%d원",
                                        cycle_no, order_label, ticker, qty, cur_price, rebuy_price)
                            if max_cycles > 0 and cycle_no >= max_cycles:
                                job["phase"]  = "done"
                                job["status"] = "done"
                                logger.info("%s 최대 사이클(%d회) 완료", name, max_cycles)
                        except Exception as sell_err:
                            err_str = str(sell_err)
                            if "주문 가능한 수량을 초과" in err_str or "초과" in err_str:
                                logger.warning("%s(%s) 매도 실패 (수량 초과) — 이미 매도된 것으로 간주, waiting_rebuy로 복구",
                                               name, ticker)
                                rebuy_price = math.floor(cur_price * (1 - rebuy_drop))
                                job["phase"]        = "waiting_rebuy"
                                job["last_sell"]    = cur_price
                                job["rebuy_price"]  = rebuy_price
                                job["last_sell_at"] = now_kst()
                                job["order_no"]     = "recovered"
                                changed = True
                            else:
                                logger.error("%s(%s) 매도 실패: %s", name, ticker, sell_err)

                # ── phase: waiting_rebuy (재매수 대기) ────────────────
                elif phase == "waiting_rebuy":
                    rebuy_price = int(job.get("rebuy_price", 0))
                    should_buy  = cur_price <= rebuy_price
                    logger.info("%s(%s) [rebuy #%d] 현재가=%d 재매수가=%d → %s",
                                name, ticker, cycle_no, cur_price, rebuy_price,
                                "★ 재매수" if should_buy else "대기")
                    if should_buy:
                        try:
                            if order_dvsn == "limit":
                                result = kis_api.place_order(ticker, "BUY", qty, price=rebuy_price, order_type="limit")
                                order_label = f"지정가({rebuy_price:,}원)"
                            else:
                                result = kis_api.place_order(ticker, "BUY", qty, order_type="market")
                                order_label = "시장가"
                            new_cycle  = cycle_no + 1
                            sell_price = calc_sell_price(cur_price, qty, repeat_take)
                            job["phase"]       = "holding"
                            job["buy_price"]   = cur_price
                            job["sell_price"]  = sell_price
                            job["cycle_no"]    = new_cycle
                            job["last_buy_at"] = now_kst()
                            job["order_no"]    = result.get("order_no", "")
                            changed = True
                            logger.info("✅ [사이클%d] 재매수(%s): %s %d주 @ %d원  목표매도가=%d원",
                                        new_cycle, order_label, ticker, qty, cur_price, sell_price)
                        except Exception as buy_err:
                            logger.error("%s(%s) 재매수 실패: %s", name, ticker, buy_err)

        except Exception as e:
            logger.error("%s(%s) 처리 실패: %s", name, ticker, e)

    if changed:
        ok = gist_writer._write_gist({"profit_cycle_jobs.json": jobs})
        logger.info("Gist 업데이트 %s", "완료" if ok else "실패")
        try:
            job_balance.main()
            logger.info("잔고 Gist 갱신 완료")
        except Exception as bal_err:
            logger.warning("잔고 갱신 실패 (무시): %s", bal_err)

    logger.info("사이클 잡 체크 완료")


if __name__ == "__main__":
    main()
