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
import time
from datetime import datetime, timezone, timedelta

import upbit_api
import gist_writer
import notify

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


UPBIT_MIN_ORDER = 5000  # 업비트 최소 주문금액(원)
MAX_ACTIVE_BUYS = 10   # 동시 매수 대기 주문 최대 개수

def _buy_qty(krw: float, price: float) -> float:
    # ceil 사용: 수수료 차감 없이 올림 → 주문금액이 krw 이상 보장
    qty = math.ceil(krw / price * 1e8) / 1e8
    # 최소주문금액(5,000원)도 반드시 충족
    min_qty = math.ceil(UPBIT_MIN_ORDER / price * 1e8) / 1e8
    return max(qty, min_qty)


def _fill_one_idle(grids: list, ticker: str, cur_price: float, krw_per_grid: float) -> bool:
    """현재가 이하 idle 격자 중 가장 가까운 1개에 매수 주문 등록.
    성공 True / 잔액·rate-limit 등으로 불가 False 반환."""
    candidates = sorted(
        [g for g in grids if g.get("state") == "idle" and float(g["level"]) < cur_price],
        key=lambda g: -float(g["level"])  # 현재가에 가장 가까운 순
    )
    for grid in candidates:
        level = float(grid["level"])
        try:
            order_price = upbit_api.round_bid_price(level)
            coin_qty    = _buy_qty(krw_per_grid, order_price)
            r = upbit_api.place_order(
                market=ticker, side="bid", ord_type="limit",
                price=order_price, volume=coin_qty
            )
            grid.update(state="buy_waiting", buy_uuid=r["uuid"],
                        coin_qty=coin_qty, last_buy_price=order_price)
            logger.info("  매수 보충 %s원 UUID:%s", f"{order_price:,.0f}", r["uuid"][:8])
            return True
        except Exception as e:
            err = str(e)
            if "insufficient_funds" in err or "too_many_requests" in err or "429" in err:
                return False
            logger.error("  idle 보충 실패 %s원: %s", f"{level:,.0f}", e)
    return False


# ─────────────────────────────────────────
# 범위 이탈 감지 / 자동 재초기화
# ─────────────────────────────────────────

def _check_out_of_range(job: dict, cur_price: float) -> bool:
    """가격이 그리드 범위 벗어난 경우 알림 + 자동 재초기화(auto_reinit_minutes 설정 시).
    job 수정 시 True 반환. reinit 필요 시 job['status']='reinit' 설정."""
    lower  = float(job.get("lower_price", 0))
    upper  = float(job.get("upper_price", float("inf")))
    name   = job.get("name", job.get("ticker", "?"))
    ticker = job["ticker"]

    in_range = lower <= cur_price <= upper

    if in_range:
        if job.get("out_of_range_since"):
            job["out_of_range_since"]     = None
            job["out_of_range_notified"]  = False
            logger.info("그리드 범위 복귀: %s (%s원)", name, f"{cur_price:,.0f}")
            return True
        return False

    # ── 범위 이탈 ──────────────────────────────────────────────────
    direction = "하단 이탈 🔻" if cur_price < lower else "상단 돌파 🔺"
    modified  = False

    if not job.get("out_of_range_since"):
        job["out_of_range_since"]    = now_kst()
        job["out_of_range_notified"] = False
        modified = True

    # 1회 알림
    if not job.get("out_of_range_notified"):
        job["out_of_range_notified"] = True
        modified = True
        auto_min = job.get("auto_reinit_minutes")
        hint = f"자동 재초기화 예정 ({auto_min}분 후)" if auto_min else "수동 reinit 필요 (Gist에서 status→reinit)"
        notify.send(
            f"⚠️ <b>그리드 범위 이탈 [{direction}]</b>  {name} ({ticker})\n"
            f"  현재가 {cur_price:,.0f}원  |  설정 범위 {lower:,.0f}~{upper:,.0f}원\n"
            f"  {hint}"
        )
        logger.info("그리드 범위 이탈 알림: %s %s 현재가 %s원", name, direction, f"{cur_price:,.0f}")

    # 자동 재초기화
    auto_min = job.get("auto_reinit_minutes")
    if auto_min:
        since_str = job.get("out_of_range_since", "")
        try:
            since   = datetime.strptime(since_str, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
            elapsed = (datetime.now(KST) - since).total_seconds() / 60
        except (ValueError, TypeError):
            elapsed = 0

        if elapsed < auto_min:
            logger.info("범위 이탈 %d분 경과 / 자동 재초기화 기준 %d분", int(elapsed), auto_min)
        else:
            # 현재가 기준으로 동일 비율 범위 이동 (기하평균 보존)
            ratio     = math.sqrt(upper / lower) if lower > 0 else 1.0
            new_lower = round(cur_price / ratio)
            new_upper = round(cur_price * ratio)
            job["lower_price"]          = new_lower
            job["upper_price"]          = new_upper
            job["status"]               = "reinit"
            job["out_of_range_since"]   = None
            job["out_of_range_notified"] = False
            modified = True
            notify.send(
                f"🔄 <b>그리드 자동 재초기화</b>  {name} ({ticker})\n"
                f"  {elapsed:.0f}분 범위 이탈 → 현재가 기준 재설정\n"
                f"  이전 {lower:,.0f}~{upper:,.0f}원  →  새 범위 {new_lower:,.0f}~{new_upper:,.0f}원"
            )
            logger.info("그리드 자동 재초기화 트리거: %s 새 범위 %s~%s원",
                        name, f"{new_lower:,.0f}", f"{new_upper:,.0f}")

    return modified


# ─────────────────────────────────────────
# 초기화
# ─────────────────────────────────────────

def initialize_grid(job: dict) -> bool:
    """그리드 초기화: 현재가 이하 격자 중 가장 가까운 MAX_ACTIVE_BUYS 개만 매수 등록"""
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

    # 현재가 이하 레벨 중 가장 가까운 MAX_ACTIVE_BUYS 개만 활성화 (나머지는 idle)
    buy_candidates = [l for l in levels if l < cur_price]
    active_buy_set = set(buy_candidates[-MAX_ACTIVE_BUYS:])
    logger.info("그리드 초기화 %s: 격자 %d개, 현재가 %s원, 매수등록 최대 %d개",
                ticker, len(levels), f"{cur_price:,.0f}", min(len(buy_candidates), MAX_ACTIVE_BUYS))

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
        if level in active_buy_set:
            try:
                order_price = upbit_api.round_bid_price(level)
                coin_qty    = _buy_qty(krw_per_grid, order_price)
                time.sleep(0.12)  # 429 방지
                r = upbit_api.place_order(
                    market=ticker, side="bid", ord_type="limit",
                    price=order_price, volume=coin_qty
                )
                grid.update(state="buy_waiting", buy_uuid=r["uuid"],
                            coin_qty=coin_qty, last_buy_price=order_price)
                logger.info("  매수 등록 %s원 %.8f개 UUID:%s",
                            f"{order_price:,.0f}", coin_qty, r["uuid"][:8])
            except Exception as e:
                err = str(e)
                if "insufficient_funds" in err:
                    logger.warning("  잔액 부족 — 초기화 중단 (이후 격자는 idle)")
                    break
                if "too_many_requests" in err or "429" in err:
                    logger.warning("  Rate limit(429) — 초기화 중단 (나머지 idle → 다음 사이클에 복구)")
                    break
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

    changed          = False
    idle_registered  = 0
    MAX_IDLE_PER_CYCLE = 3    # 사이클당 idle 재등록 최대 3건 (429 방지)

    def buy_waiting_count():
        return sum(1 for g in grids if g.get("state") == "buy_waiting")

    # ── 현재가 선행 조회 (범위 이탈 체크 + idle 격자 공용) ──────────
    try:
        cur_price = upbit_api.get_price(ticker)["price"]
    except Exception as e:
        logger.warning("현재가 선행 조회 실패 — 범위 체크 건너뜀: %s", e)
        cur_price = None

    if cur_price is not None:
        if _check_out_of_range(job, cur_price):
            changed = True
        if job.get("status") == "reinit":
            return changed  # 재초기화 예약됨 — 이번 사이클 격자 처리 중단

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
                    # 매수 슬롯 1개 비었으므로 idle에서 즉시 보충
                    if buy_waiting_count() < MAX_ACTIVE_BUYS:
                        if _fill_one_idle(grids, ticker, avg_price, krw_per_grid):
                            changed = True
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
                    # 재매수 후에도 슬롯 여유가 있으면 idle 보충
                    if buy_waiting_count() < MAX_ACTIVE_BUYS:
                        if _fill_one_idle(grids, ticker, sell_exec, krw_per_grid):
                            changed = True
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
            if idle_registered >= MAX_IDLE_PER_CYCLE:
                continue  # 이번 사이클 한도 초과 → 다음 사이클에 처리
            if buy_waiting_count() >= MAX_ACTIVE_BUYS:
                continue  # 이미 최대 매수 주문 수 도달
            if cur_price is None:
                continue  # 선행 조회 실패 시 idle 처리 건너뜀
            if level < cur_price:
                idle_registered += 1  # 성공/실패 무관하게 시도 횟수 카운트
                try:
                    order_price = upbit_api.round_bid_price(level)
                    coin_qty    = _buy_qty(krw_per_grid, order_price)
                    if idle_registered > 1:
                        time.sleep(0.15)  # 연속 주문 시 rate limit 회피
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
                    err = str(e)
                    if "insufficient_funds" in err:
                        logger.warning("  잔액 부족 — idle 격자 재등록 중단 (이후 생략)")
                        break
                    if "too_many_requests" in err or "429" in err:
                        logger.warning("  Rate limit(429) — idle 격자 재등록 중단 (다음 사이클에 재시도)")
                        break
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

        elif status == "reinit":
            logger.info("그리드 재초기화: %s", name)
            stop_grid(job)          # 기존 주문 전부 취소
            job["status"] = "init"  # stop_grid 가 stopped 로 바꾼 것을 init 으로 재설정
            if initialize_grid(job):
                changed = True
            else:
                changed = True      # stop 만 됐어도 저장 필요

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
