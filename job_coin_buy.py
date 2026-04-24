"""
코인 매수 잡 — GitHub Actions에서 5분마다 24/7 실행
Gist coin_buy_jobs.json 에서 활성 잡 읽기 → 조건 달성 시 즉시 매수 → 상태 업데이트
Gist coin_cycle_jobs.json 의 waiting_buy 잡도 처리 (즉시 매수 포함)

조건 유형:
  market_krw : 등록 즉시 시장가 매수 (KRW 금액 기준)
  limit      : 현재가 <= buy_target_price 일 때 매수
"""
import logging
import math
from datetime import datetime, timezone, timedelta

import upbit_api
import gist_writer

KST = timezone(timedelta(hours=9))
logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
_fmt = logging.Formatter("%(asctime)s KST %(levelname)s %(message)s")
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("coin_buy_cloud.log", encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)


BUY_FEE = upbit_api.BUY_FEE


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def _calc_sell_price(buy_price: float, take_pct: float) -> float:
    cost_per = buy_price * (1 + BUY_FEE)
    return cost_per * (1 + take_pct / 100) / (1 - upbit_api.SELL_FEE)


def process_cycle_buy():
    """coin_cycle_jobs.json 의 waiting_buy 처리 — mode=buy 시 즉시 실행"""
    jobs = gist_writer._read_gist_file("coin_cycle_jobs.json")
    if jobs is None:
        logger.error("coin_cycle_jobs.json Gist 읽기 실패")
        return
    jobs = jobs if isinstance(jobs, list) else []

    waiting = [j for j in jobs if j.get("phase") == "waiting_buy"
               and j.get("status") not in ("done", "cancelled", "stopped")]
    if not waiting:
        logger.info("사이클 waiting_buy 잡 없음")
        return

    tickers = list({j["ticker"] for j in waiting})
    try:
        price_cache = upbit_api.get_prices(tickers)
    except Exception as e:
        logger.error("현재가 조회 실패: %s", e)
        return

    changed = False
    for job in jobs:
        if job.get("phase") != "waiting_buy":
            continue
        if job.get("status") in ("done", "cancelled", "stopped"):
            continue

        ticker     = job["ticker"]
        name       = job.get("name", ticker)
        cond_type  = job.get("condition_type", "market_krw")
        buy_target = float(job.get("buy_target_price", 0))
        take_pct   = float(job.get("take_pct", 3.0))

        cur_price = price_cache.get(ticker, {}).get("price")
        if not cur_price:
            continue

        # ── 지정가 즉시 등록 / 체결 확인 ──────────────────────────────
        if cond_type == "limit":
            buy_order_uuid = job.get("buy_order_uuid", "")
            if buy_order_uuid:
                try:
                    order = upbit_api.get_order(buy_order_uuid)
                    state = order["state"]
                    if state == "done":
                        ev = order["executed_volume"]
                        ap = order["avg_price"] or buy_target
                        job["phase"]      = "holding"
                        job["buy_price"]  = ap
                        job["hold_qty"]   = ev
                        job["sell_price"] = _calc_sell_price(ap, take_pct)
                        job["bought_at"]  = now_kst()
                        changed = True
                        logger.info("지정가 매수 체결 %s %.8f개 @ %s원 → 목표: %s원",
                                    ticker, ev, f"{ap:,.0f}", f"{job['sell_price']:,.0f}")
                    elif state == "cancel":
                        job["buy_order_uuid"] = ""
                        changed = True
                        logger.info("%s 지정가 매수 취소 — 재등록 예정", ticker)
                    else:
                        logger.info("  %s(%s) 지정가 매수 대기 @ %s원",
                                    name, ticker, f"{buy_target:,.0f}")
                except Exception as e:
                    logger.error("%s 주문 조회 실패: %s", ticker, e)
            else:
                if buy_target <= 0:
                    logger.warning("%s(%s) 지정가 미설정 — 건너뜀", name, ticker)
                    continue
                krw_amount = float(job.get("krw_amount", 0))
                coin_qty   = float(job.get("coin_qty", 0))
                if krw_amount > 0 and coin_qty <= 0:
                    coin_qty = math.floor(krw_amount / buy_target * 1e8) / 1e8
                if coin_qty <= 0:
                    logger.warning("%s(%s) 수량/금액 미설정 — 건너뜀", name, ticker)
                    continue
                logger.info("★ [cycle waiting_buy] 지정가 주문 등록: %s(%s) %.8f개 @ %s원",
                            name, ticker, coin_qty, f"{buy_target:,.0f}")
                try:
                    result = upbit_api.place_order(
                        market=ticker, side="bid", ord_type="limit",
                        volume=coin_qty, price=buy_target
                    )
                    job["buy_order_uuid"] = result.get("uuid", "")
                    changed = True
                    logger.info("지정가 주문 등록 %s %.8f개 @ %s원  UUID: %s",
                                ticker, coin_qty, f"{buy_target:,.0f}", result.get("uuid", ""))
                except Exception as e:
                    logger.error("%s 지정가 매수 주문 실패: %s", ticker, e)
            continue

        # ── 시장가 즉시 매수 ────────────────────────────────────────
        krw_amount = float(job.get("krw_amount", 0))
        coin_qty   = float(job.get("coin_qty", 0))

        logger.info("★ [cycle waiting_buy] 시장가 매수 실행: %s(%s) @ %s원", name, ticker, f"{cur_price:,.0f}")
        try:
            if krw_amount > 0 and coin_qty <= 0:
                result = upbit_api.place_order(
                    market=ticker, side="bid", ord_type="price", price=krw_amount
                )
                coin_qty = math.floor(krw_amount / cur_price * (1 - BUY_FEE) * 1e8) / 1e8
            elif coin_qty > 0:
                result = upbit_api.place_order(
                    market=ticker, side="bid", ord_type="price", price=cur_price * coin_qty
                )
                coin_qty = math.floor(coin_qty * (1 - BUY_FEE) * 1e8) / 1e8
            else:
                logger.warning("%s(%s) 수량/금액 미설정 — 건너뜀", name, ticker)
                continue

            job["phase"]      = "holding"
            job["buy_price"]  = cur_price
            job["hold_qty"]   = coin_qty
            job["sell_price"] = _calc_sell_price(cur_price, take_pct)
            job["bought_at"]  = now_kst()
            job["buy_uuid"]   = result.get("uuid", "")
            changed = True
            logger.info("매수 완료 %s %.8f개 @ %s원 → 매도 목표: %s원",
                        ticker, coin_qty, f"{cur_price:,.0f}", f"{job['sell_price']:,.0f}")
        except Exception as e:
            logger.error("%s 사이클 매수 실패: %s", ticker, e)

    if changed:
        ok = gist_writer._write_gist({"coin_cycle_jobs.json": jobs})
        logger.info("coin_cycle_jobs Gist 저장 %s", "완료" if ok else "실패")


def main():
    logger.info("코인 매수 잡 체크 시작")

    raw = gist_writer._read_gist_file("coin_buy_jobs.json")
    if raw is None:
        logger.error("Gist 읽기 실패 — GH_TOKEN 권한(gist scope) 또는 GIST_ID 확인 필요")
        return
    jobs = raw if isinstance(raw, list) else []
    logger.info("Gist에서 총 %d개 잡 로드", len(jobs))

    active = [j for j in jobs if j.get("status") in ("active", "submitted")]
    if not active:
        logger.info("활성 매수 잡 없음")
        process_cycle_buy()
        return

    logger.info("활성 잡 %d개 처리", len(active))
    changed = False

    # 현재가 일괄 조회
    active_tickers = list({j["ticker"] for j in active})
    price_cache: dict = {}
    try:
        price_cache = upbit_api.get_prices(active_tickers)
    except Exception as e:
        logger.error("현재가 일괄 조회 실패: %s", e)

    for job in jobs:
        if job.get("status") not in ("active", "submitted"):
            continue

        ticker = job["ticker"]
        name   = job.get("name", ticker)
        cond   = job.get("condition_type", "market_krw")

        price_info = price_cache.get(ticker)
        if not price_info:
            try:
                price_info = upbit_api.get_price(ticker)
            except Exception as e:
                logger.error("현재가 조회 실패 %s: %s", ticker, e)
                continue

        cur_price = price_info["price"]

        # ── 시장가 즉시 매수 ──────────────────────────────────────
        if cond == "market_krw":
            krw_amount = float(job.get("krw_amount", 0))
            coin_qty   = float(job.get("coin_qty", 0))
            if krw_amount <= 0 and coin_qty > 0:
                krw_amount = round(coin_qty * cur_price)
            if krw_amount <= 0:
                logger.warning("%s(%s) KRW 금액 미설정 — 건너뜀", name, ticker)
                continue

            logger.info("★ 시장가 매수 실행: %s(%s) %s원", name, ticker, f"{int(krw_amount):,}")
            try:
                result = upbit_api.place_order(
                    market=ticker,
                    side="bid",
                    ord_type="price",
                    price=krw_amount,
                )
                job["status"]      = "done"
                job["executed_at"] = now_kst()
                job["order_uuid"]  = result.get("uuid", "")
                job["exec_price"]  = cur_price
                job["exec_qty"]    = round(krw_amount / cur_price, 8)
                changed = True
                logger.info("시장가 매수 완료 %s @ %s원  UUID: %s",
                            ticker, f"{cur_price:,.0f}", result.get("uuid", ""))
            except Exception as e:
                logger.error("%s 시장가 매수 실패: %s", ticker, e)

        # ── 지정가 즉시 주문 등록 / 체결 확인 ──────────────────────────────
        elif cond == "limit":
            target_price = float(job.get("target_price", 0))
            if target_price <= 0:
                logger.warning("%s(%s) 목표가 미설정 — 건너뜀", name, ticker)
                continue

            status = job.get("status")

            # submitted → 체결 확인
            if status == "submitted":
                order_uuid = job.get("order_uuid", "")
                if not order_uuid:
                    job["status"] = "active"
                    changed = True
                    continue
                try:
                    order = upbit_api.get_order(order_uuid)
                    state = order["state"]
                    if state == "done":
                        job["status"]      = "done"
                        job["executed_at"] = now_kst()
                        job["exec_price"]  = order["avg_price"] or target_price
                        job["exec_qty"]    = order["executed_volume"]
                        changed = True
                        logger.info("지정가 매수 체결 완료 %s @ %s원  UUID: %s",
                                    ticker, f"{target_price:,.0f}", order_uuid)
                    elif state == "cancel":
                        job["status"] = "cancelled"
                        changed = True
                        logger.info("%s 지정가 주문 취소됨", ticker)
                    else:
                        logger.info("  %s(%s) 지정가 주문 대기 중 (state=%s)",
                                    name, ticker, state)
                except Exception as e:
                    logger.error("%s 주문 조회 실패: %s", ticker, e)
                continue

            # active → 즉시 지정가 주문 등록
            krw_amount = float(job.get("krw_amount", 0))
            coin_qty   = float(job.get("coin_qty", 0))

            if krw_amount > 0 and coin_qty <= 0:
                coin_qty = math.floor(krw_amount / target_price * 1e8) / 1e8

            if coin_qty <= 0:
                logger.warning("%s(%s) 수량 계산 불가 — 건너뜀", name, ticker)
                continue

            logger.info("★ 지정가 매수 주문 등록: %s(%s) %.8f개 @ %s원",
                        name, ticker, coin_qty, f"{target_price:,.0f}")
            try:
                result = upbit_api.place_order(
                    market=ticker,
                    side="bid",
                    ord_type="limit",
                    volume=coin_qty,
                    price=target_price,
                )
                job["status"]     = "submitted"
                job["order_uuid"] = result.get("uuid", "")
                job["exec_price"] = target_price
                job["exec_qty"]   = coin_qty
                changed = True
                logger.info("지정가 주문 등록 완료 %s %.8f개 @ %s원  UUID: %s",
                            ticker, coin_qty, f"{target_price:,.0f}", result.get("uuid", ""))
            except Exception as e:
                logger.error("%s 지정가 매수 실패: %s", ticker, e)

    if changed:
        ok = gist_writer._write_gist({"coin_buy_jobs.json": jobs})
        logger.info("Gist 저장 %s", "완료" if ok else "실패")
    else:
        logger.info("변경 없음 — Gist 저장 건너뜀")

    process_cycle_buy()


if __name__ == "__main__":
    main()
