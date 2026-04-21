"""
매수 조건 잡 — GitHub Actions에서 5분마다 실행
Gist profit_buy_jobs.json 에서 활성 잡 읽기 → 조건 달성 시 즉시 매수 → 상태 업데이트
Gist profit_cycle_jobs.json 의 waiting_buy 잡도 처리 (즉시 매수 포함)

조건 유형:
  market : 등록 즉시 시장가 매수
  limit  : 현재가 <= target_price 일 때 매수
"""
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import kis_api
import gist_writer

KST = timezone(timedelta(hours=9))

logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
_fmt = logging.Formatter("%(asctime)s KST %(levelname)s %(message)s")
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("profit_buy_cloud.log", encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)

BUY_FEE  = 0.00015
SELL_FEE = 0.00195


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def _calc_sell_price(buy_price: int, qty: int, take_pct: float) -> int:
    target_gross = buy_price * qty * (1 + BUY_FEE) * (1 + take_pct)
    sell_unit    = target_gross / qty / (1 - SELL_FEE)
    return math.ceil(sell_unit) + 1


def process_cycle_buy():
    """profit_cycle_jobs.json 의 waiting_buy 처리 — 매수 잡 실행 시 즉시 처리"""
    if not kis_api.is_any_market_open():
        return

    jobs = gist_writer._read_gist_file("profit_cycle_jobs.json")
    if jobs is None:
        logger.error("profit_cycle_jobs.json Gist 읽기 실패")
        return
    jobs = jobs if isinstance(jobs, list) else []

    waiting = [j for j in jobs if j.get("phase") == "waiting_buy"
               and j.get("status") not in ("done", "cancelled", "stopped")]
    if not waiting:
        logger.info("사이클 waiting_buy 잡 없음")
        return

    tickers = list({j["ticker"] for j in waiting})

    def _fetch(ticker):
        try:
            return ticker, kis_api.get_price(ticker)
        except Exception as e:
            logger.error("현재가 조회 실패 %s: %s", ticker, e)
            return ticker, None

    price_cache = {}
    with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as ex:
        for ticker, info in ex.map(_fetch, tickers):
            if info:
                price_cache[ticker] = info

    changed = False
    for job in jobs:
        if job.get("phase") != "waiting_buy":
            continue
        if job.get("status") in ("done", "cancelled", "stopped"):
            continue

        ticker     = job["ticker"]
        name       = job.get("name", ticker)
        cond_type  = job.get("condition_type", "market")
        buy_price  = int(job.get("buy_price", 0))
        take_pct   = float(job.get("take_pct", 0.03))
        qty        = int(job.get("qty", 0))
        amount     = int(job.get("amount", 0))

        info = price_cache.get(ticker)
        if not info:
            continue
        cur_price = int(info["stck_prpr"])

        should_buy = (cond_type == "market") or (buy_price > 0 and cur_price <= buy_price)
        if not should_buy:
            logger.info("  %s(%s) 매수 대기 현재가=%d / 목표=%d", name, ticker, cur_price, buy_price)
            continue

        if qty <= 0 and amount > 0:
            qty = amount // cur_price
        if qty < 1:
            logger.warning("%s 수량/금액 미설정 — 건너뜀", name)
            continue

        logger.info("★ [cycle waiting_buy] 매수 실행: %s(%s) %d주 @ %d원", name, ticker, qty, cur_price)
        try:
            result = kis_api.place_order(ticker, "BUY", qty, order_type="market")
            sell_price = _calc_sell_price(cur_price, qty, take_pct)
            job["phase"]       = "holding"
            job["buy_price"]   = cur_price
            job["sell_price"]  = sell_price
            job["qty"]         = qty
            job["cycle_no"]    = 1
            job["last_buy_at"] = now_kst()
            job["order_no"]    = result.get("order_no", "")
            job["status"]      = "active"
            changed = True
            logger.info("매수 완료 %s %d주 @ %d원 → 매도 목표: %d원",
                        ticker, qty, cur_price, sell_price)
        except Exception as e:
            logger.error("%s 사이클 매수 실패: %s", ticker, e)

    if changed:
        ok = gist_writer._write_gist({"profit_cycle_jobs.json": jobs})
        logger.info("profit_cycle_jobs Gist 저장 %s", "완료" if ok else "실패")


def main():
    if not kis_api.is_any_market_open():
        logger.info("거래 시간 외 (KRX/NXT 모두 닫힘) — 종료")
        return

    nxt_mode = kis_api._is_nxt_time()
    logger.info("매수 잡 체크 시작 [%s]", "NXT 시간대" if nxt_mode else "KRX 정규")

    raw = gist_writer._read_gist_file("profit_buy_jobs.json")
    if raw is None:
        logger.error("Gist 읽기 실패 — GH_TOKEN 권한(gist scope) 또는 GIST_ID 확인 필요")
        return
    jobs = raw if isinstance(raw, list) else []
    logger.info("Gist에서 총 %d개 잡 로드", len(jobs))
    for j in jobs:
        logger.info("  · %s(%s) status=%s phase=%s",
                    j.get("name", "?"), j.get("ticker", "?"),
                    j.get("status", "?"), j.get("phase", "-"))

    active = [j for j in jobs if j.get("status") == "active"]

    if not active:
        logger.info("활성 매수 잡 없음")
        process_cycle_buy()
        return

    logger.info("활성 잡 %d개 처리", len(active))
    changed = False

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
        if job.get("status") != "active":
            continue

        ticker         = job["ticker"]
        name           = job.get("name", ticker)
        condition_type = job.get("condition_type", "limit")  # "market" | "limit"
        target_price   = int(job.get("target_price", 0))
        qty            = int(job.get("qty", 0))
        amount         = int(job.get("amount", 0))           # 금액 기준일 때

        try:
            info = price_cache.get(ticker)
            if info is None:
                logger.warning("%s 현재가 캐시 없음 — 건너뜀", ticker)
                continue
            cur_price = int(info["stck_prpr"])

            # 매수 수량 결정
            if qty > 0:
                order_qty = qty
            elif amount > 0:
                order_qty = amount // cur_price
                if order_qty < 1:
                    logger.warning("%s 금액 %d원으로 현재가 %d원 1주 매수 불가", name, amount, cur_price)
                    continue
            else:
                logger.warning("%s 수량/금액 미설정", name)
                continue

            # 조건 판단
            if condition_type == "market":
                should_buy = True
                logger.info("%s(%s) 즉시 시장가 매수 예정 — %d주", name, ticker, order_qty)
            else:  # limit
                should_buy = cur_price <= target_price
                logger.info("%s(%s) 현재가 %d원 / 목표매수가 %d원 — %s",
                            name, ticker, cur_price, target_price,
                            "★ 매수 조건 달성" if should_buy else "대기 중")

            if should_buy:
                result = kis_api.place_order(ticker, "BUY", order_qty, order_type="market")
                job["status"]      = "done"
                job["buy_price"]   = cur_price
                job["buy_qty"]     = order_qty
                job["executed_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                job["order_no"]    = result.get("order_no", "")
                changed = True
                logger.info("✅ 매수 완료: %s %d주 @ %d원  주문번호: %s",
                            ticker, order_qty, cur_price, job["order_no"])

        except Exception as e:
            logger.error("%s(%s) 처리 실패: %s", name, ticker, e)

    if changed:
        ok = gist_writer._write_gist({"profit_buy_jobs.json": jobs})
        logger.info("Gist 업데이트 %s", "완료" if ok else "실패")

    logger.info("매수 잡 체크 완료")

    process_cycle_buy()


if __name__ == "__main__":
    main()
