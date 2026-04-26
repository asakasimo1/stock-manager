"""
코인 그리드 트레이딩 잡

전략:
  - 지정 가격 범위를 grid_pct% 간격으로 나눠 지정가 매수 주문 일괄 등록
  - 매수 체결 시 → 상위 격자(+grid_pct%)에 즉시 매도 주문 등록
  - 매도 체결 시 → 동일 격자에 즉시 매수 주문 재등록 (사이클 반복)
  - 가격이 오르내릴수록 체결 빈도 증가 → 소액 수익 누적

상태:
  job.status : init → active → stopping → stopped
  grid.state : idle | buy_waiting | sell_waiting

설정 파일 (Gist): coin_grid_jobs.json
"""
import logging
import math
from datetime import datetime, timezone, timedelta

import upbit_api
import gist_writer

KST      = timezone(timedelta(hours=9))
BUY_FEE  = upbit_api.BUY_FEE
SELL_FEE = upbit_api.SELL_FEE

logger = logging.getLogger(__name__)


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def build_levels(lower: float, upper: float, grid_pct: float) -> list:
    """하한~상한 사이 grid_pct% 기하급수 간격 격자 가격 목록"""
    levels, price = [], lower
    while price <= upper * 1.0001:
        levels.append(upbit_api.round_bid_price(price))
        price *= (1 + grid_pct / 100)
    return sorted(set(levels))


def _buy_qty(krw: float, price: float) -> float:
    return math.floor(krw / price * (1 - BUY_FEE) * 1e8) / 1e8


# ─────────────────────────────────────────
# 초기화
# ─────────────────────────────────────────

def initialize_grid(job: dict) -> bool:
    """그리드 초기화: 현재가 이하 격자 전부 매수 주문 등록"""
    ticker       = job["ticker"]
    grid_pct     = float(job["grid_pct"])
    lower        = float(job["lower_price"])
    upper        = float(job["upper_price"])
    krw_per_grid = float(job["krw_per_grid"])

    try:
        cur_price = upbit_api.get_price(ticker)["price"]
    except Exception as e:
        logger.error("그리드 초기화 현재가 조회 실패 %s: %s", ticker, e)
        return False

    levels = build_levels(lower, upper, grid_pct)
    logger.info("그리드 초기화 %s: 격자 %d개, 현재가 %s원",
                ticker, len(levels), f"{cur_price:,.0f}")

    grids = []
    for level in levels:
        grid = {
            "level":           level,
            "state":           "idle",
            "buy_uuid":        "",
            "sell_uuid":       "",
            "coin_qty":        0,
            "last_buy_price":  0,
            "last_sell_price": 0,
        }
        if level < cur_price:
            try:
                order_price = upbit_api.round_bid_price(level)
                coin_qty    = _buy_qty(krw_per_grid, order_price)
                r = upbit_api.place_order(
                    market=ticker, side="bid", ord_type="limit",
                    price=order_price, volume=coin_qty
                )
                grid.update(state="buy_waiting", buy_uuid=r["uuid"],
                            coin_qty=coin_qty, last_buy_price=order_price)
                logger.info("  매수 등록 %s원 %.8f개 UUID:%s",
                            f"{order_price:,.0f}", coin_qty, r["uuid"][:8])
            except Exception as e:
                logger.error("  격자 %s원 매수 주문 실패: %s", f"{level:,.0f}", e)
        grids.append(grid)

    job.update(grids=grids, init_price=cur_price,
               status="active", initialized_at=now_kst())
    return True


# ─────────────────────────────────────────
# 사이클 처리
# ─────────────────────────────────────────

def process_grid(job: dict) -> bool:
    ticker       = job["ticker"]
    grid_pct     = float(job["grid_pct"])
    krw_per_grid = float(job["krw_per_grid"])
    grids        = job.get("grids", [])

    changed  = False
    cur_price = None  # idle 격자 처리 시 1회만 조회

    for i, grid in enumerate(grids):
        state = grid.get("state", "idle")
        level = float(grid["level"])

        # ── 매수 대기 → 체결 확인 ──────────────────────────────
        if state == "buy_waiting":
            buy_uuid = grid.get("buy_uuid", "")
            if not buy_uuid:
                continue
            try:
                order = upbit_api.get_order(buy_uuid)
            except Exception as e:
                logger.error("  격자 %s원 매수 조회 실패: %s", f"{level:,.0f}", e)
                continue

            if order["state"] == "done":
                coin_qty  = order["executed_volume"] or grid["coin_qty"]
                avg_price = order["avg_price"] or level
                grid.update(coin_qty=coin_qty, last_buy_price=avg_price, buy_uuid="")

                # 상위 격자에 매도 주문 등록
                sell_price = avg_price * (1 + grid_pct / 100)
                if i + 1 < len(grids):
                    sell_price = max(sell_price, grids[i + 1]["level"])
                sell_price = upbit_api.round_ask_price(sell_price)
                try:
                    r = upbit_api.place_order(
                        market=ticker, side="ask", ord_type="limit",
                        price=sell_price, volume=coin_qty
                    )
                    grid.update(state="sell_waiting", sell_uuid=r["uuid"],
                                last_sell_price=sell_price)
                    changed = True
                    logger.info("★ 매수체결 @ %s원 → 매도등록 @ %s원 (%.8f개) UUID:%s",
                                f"{avg_price:,.0f}", f"{sell_price:,.0f}",
                                coin_qty, r["uuid"][:8])
                except Exception as e:
                    logger.error("  격자 %s원 매도 등록 실패: %s", f"{level:,.0f}", e)

            elif order["state"] == "cancel":
                logger.info("  격자 %s원 매수 취소 → 재등록", f"{level:,.0f}")
                grid.update(buy_uuid="", state="idle")
                changed = True
                # idle로 두면 다음 사이클에 _recover가 재등록

        # ── 매도 대기 → 체결 확인 ──────────────────────────────
        elif state == "sell_waiting":
            sell_uuid = grid.get("sell_uuid", "")
            if not sell_uuid:
                continue
            try:
                order = upbit_api.get_order(sell_uuid)
            except Exception as e:
                logger.error("  격자 %s원 매도 조회 실패: %s", f"{level:,.0f}", e)
                continue

            if order["state"] == "done":
                sell_exec = order["avg_price"] or grid["last_sell_price"]
                buy_exec  = grid["last_buy_price"]
                coin_qty  = grid["coin_qty"]
                pnl = (sell_exec * (1 - SELL_FEE) - buy_exec * (1 + BUY_FEE)) * coin_qty
                job["total_profit_krw"] = round(job.get("total_profit_krw", 0) + pnl, 2)
                job["trade_count"]      = job.get("trade_count", 0) + 1
                grid.update(sell_uuid="")
                changed = True
                logger.info("★ 매도체결 @ %s원 순수익 %+.0f원 (누적 %+.0f원 / %d회)",
                            f"{sell_exec:,.0f}", pnl,
                            job["total_profit_krw"], job["trade_count"])

                # 동일 격자에 매수 주문 재등록
                try:
                    order_price = upbit_api.round_bid_price(level)
                    coin_qty2   = _buy_qty(krw_per_grid, order_price)
                    r = upbit_api.place_order(
                        market=ticker, side="bid", ord_type="limit",
                        price=order_price, volume=coin_qty2
                    )
                    grid.update(state="buy_waiting", buy_uuid=r["uuid"],
                                coin_qty=coin_qty2, last_buy_price=order_price)
                    logger.info("  재매수 등록 %s원 UUID:%s", f"{order_price:,.0f}", r["uuid"][:8])
                except Exception as e:
                    grid.update(state="idle")
                    logger.error("  격자 %s원 재매수 실패: %s", f"{level:,.0f}", e)

            elif order["state"] == "cancel":
                logger.info("  격자 %s원 매도 취소 → 재등록", f"{level:,.0f}")
                sell_price = grid.get("last_sell_price") or level * (1 + grid_pct / 100)
                sell_price = upbit_api.round_ask_price(sell_price)
                try:
                    r = upbit_api.place_order(
                        market=ticker, side="ask", ord_type="limit",
                        price=sell_price, volume=grid["coin_qty"]
                    )
                    grid.update(sell_uuid=r["uuid"], last_sell_price=sell_price)
                    changed = True
                except Exception as e:
                    grid.update(sell_uuid="", state="idle")
                    logger.error("  격자 %s원 매도 재등록 실패: %s", f"{level:,.0f}", e)

        # ── idle → 매수 재등록 시도 ─────────────────────────────
        elif state == "idle":
            if cur_price is None:
                try:
                    cur_price = upbit_api.get_price(ticker)["price"]
                except Exception:
                    break  # 현재가 조회 실패 시 이후 idle 처리 중단
            if level < cur_price:
                try:
                    order_price = upbit_api.round_bid_price(level)
                    coin_qty    = _buy_qty(krw_per_grid, order_price)
                    r = upbit_api.place_order(
                        market=ticker, side="bid", ord_type="limit",
                        price=order_price, volume=coin_qty
                    )
                    grid.update(state="buy_waiting", buy_uuid=r["uuid"],
                                coin_qty=coin_qty, last_buy_price=order_price)
                    changed = True
                    logger.info("  idle 격자 %s원 재등록 UUID:%s",
                                f"{order_price:,.0f}", r["uuid"][:8])
                except Exception as e:
                    if "insufficient_funds" in str(e):
                        logger.warning("  잔액 부족 — idle 격자 재등록 중단 (이후 격자 생략)")
                        break  # 잔액 부족이면 이후 격자도 실패하므로 중단
                    logger.error("  idle 격자 %s원 재등록 실패: %s", f"{level:,.0f}", e)

    return changed


# ─────────────────────────────────────────
# 중단 처리
# ─────────────────────────────────────────

def stop_grid(job: dict) -> int:
    """모든 미체결 주문 취소 후 상태 초기화"""
    ticker    = job["ticker"]
    cancelled = 0
    for grid in job.get("grids", []):
        for key in ("buy_uuid", "sell_uuid"):
            uid = grid.get(key, "")
            if uid:
                try:
                    upbit_api.cancel_order(uid)
                    grid[key] = ""
                    cancelled += 1
                    logger.info("  주문 취소: %s", uid[:8])
                except Exception as e:
                    logger.warning("  취소 실패 %s: %s", uid[:8], e)
        grid["state"] = "idle"
    job["status"] = "stopped"
    logger.info("그리드 중단 %s: %d건 취소", ticker, cancelled)
    return cancelled


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

def main():
    logger.info("그리드 잡 체크 시작")

    jobs = gist_writer._read_gist_file("coin_grid_jobs.json")
    if jobs is None:
        logger.error("그리드 잡 읽기 실패")
        return
    if not jobs:
        logger.info("등록된 그리드 잡 없음")
        return

    changed = False

    for job in jobs:
        status = job.get("status", "active")
        name   = job.get("name", job.get("ticker", "?"))

        if status == "stopping":
            logger.info("그리드 중단 처리: %s", name)
            stop_grid(job)
            changed = True

        elif status in ("init",) or (status == "active" and not job.get("grids")):
            logger.info("그리드 초기화: %s", name)
            if initialize_grid(job):
                changed = True

        elif status == "active":
            logger.info("그리드 처리: %s", name)
            if process_grid(job):
                changed = True

    if changed:
        gist_writer._write_gist({"coin_grid_jobs.json": jobs})

    logger.info("그리드 잡 체크 완료")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    main()
