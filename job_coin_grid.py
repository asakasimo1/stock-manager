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
    cur_price        = None   # idle 격자 처리 시 1회만 조회
    idle_registered  = 0
    MAX_IDLE_PER_CYCLE = 3    # 사이클당 idle 재등록 최대 3건 (429 방지)

    def buy_waiting_count():
        return sum(1 for g in grids if g.get("state") == "buy_waiting")

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
                time.sleep(0.5)  # buy/sell_waiting 호출 후 rate limit 회복 대기
                try:
                    cur_price = upbit_api.get_price(ticker)["price"]
                except Exception:
                    break  # 현재가 조회 실패 시 이후 idle 처리 중단
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
# 이탈 자동 재설정
# ─────────────────────────────────────────

def _stop_loss_top_grid(job: dict) -> bool:
    """하단 이탈 시 KRW 잔고 < krw_per_grid 인 경우에만
    sell_waiting 격자 중 level이 가장 높은 것 1개를 시장가 손절.
    Gist 저장이 필요한 변경 발생 시 True 반환."""
    grids = job.get("grids", [])
    candidates = sorted(
        [g for g in grids if g.get("state") == "sell_waiting"],
        key=lambda g: -float(g["level"])
    )
    if not candidates:
        return False

    # KRW 잔고 확인 — 1 grid 매수 가능 잔고가 있으면 손절 불필요
    krw_per_grid = float(job.get("krw_per_grid", 0))
    try:
        bal = upbit_api.get_balance()
        krw_avail = bal.get("krw", 0)
        if krw_avail >= krw_per_grid:
            logger.info("손절 스킵: KRW 잔고 %s원 ≥ 격자당 %s원 (매수 가능)",
                        f"{krw_avail:,.0f}", f"{krw_per_grid:,.0f}")
            return False
        logger.info("손절 진행: KRW 잔고 %s원 < 격자당 %s원 (잔고 부족)",
                    f"{krw_avail:,.0f}", f"{krw_per_grid:,.0f}")
    except Exception as e:
        logger.warning("손절 전 잔고 조회 실패 — 손절 보류: %s", e)
        return False

    grid     = candidates[0]
    level    = float(grid["level"])
    sell_uuid = grid.get("sell_uuid", "")
    coin_qty  = float(grid.get("coin_qty", 0))

    # 기존 매도 지정가 주문 취소 — 실패 시 이중 매도 방지를 위해 손절 보류
    if sell_uuid:
        try:
            upbit_api.cancel_order(sell_uuid)
            grid["sell_uuid"] = ""
        except Exception as e:
            logger.warning("손절: 기존 매도 취소 실패 → 손절 보류 (이중 매도 방지): %s원: %s",
                           f"{level:,.0f}", e)
            return False

    if coin_qty <= 0:
        grid.update(state="idle", sell_uuid="", coin_qty=0)
        return True

    # 시장가 손절 매도
    try:
        upbit_api.place_order(
            market=job["ticker"], side="ask", ord_type="market", volume=coin_qty
        )
        buy_price = grid.get("last_buy_price", 0)
        job["trade_count"] = job.get("trade_count", 0) + 1
        grid.update(state="idle", sell_uuid="", coin_qty=0)
        logger.info("▼ 손절 매도: %s %.8f개 (매수가 %s원 / 격자 %s원)",
                    job["ticker"], coin_qty, f"{buy_price:,.0f}", f"{level:,.0f}")
        return True
    except Exception as e:
        logger.error("손절 매도 실패 %s원: %s", f"{level:,.0f}", e)
        grid.update(state="idle", sell_uuid="")
        return False


def _check_auto_reinit(job: dict, cur_price: float) -> bool:
    """현재가가 그리드 범위를 이탈했을 때 auto_reinit_minutes 경과 후 reinit 트리거.
    하단 이탈 + X분 경과 + 잔고 부족 시에만 sell_waiting 최상단 1개 손절.
    Gist 저장이 필요한 변경이 발생하면 True 반환."""
    reinit_min = job.get("auto_reinit_minutes")
    if not reinit_min or reinit_min < 10:
        return False

    # 이탈 기준: 실제 그리드 격자 레벨 범위 (lower/upper_price 보다 넓을 수 있음)
    grids = job.get("grids", [])
    if grids:
        levels = [float(g["level"]) for g in grids]
        lower = min(min(levels), float(job["lower_price"]))
        upper = max(max(levels), float(job["upper_price"]))
    else:
        lower = float(job["lower_price"])
        upper = float(job["upper_price"])

    if lower <= cur_price <= upper:
        if job.get("escaped_at"):
            del job["escaped_at"]
            logger.info("그리드 범위 복귀: %s (현재가 %s원)", job.get("name"), f"{cur_price:,.0f}")
            return True
        return False

    # 이탈 상태
    is_below = cur_price < lower
    now = datetime.now(KST)
    escaped_at_str = job.get("escaped_at")

    if not escaped_at_str:
        # 첫 이탈 감지: 기록만 하고 손절하지 않음 (X분 대기)
        job["escaped_at"] = now_kst()
        direction = "하단" if is_below else "상단"
        logger.info("그리드 %s 이탈 감지: %s 현재가 %s원 (범위 %s~%s원, %d분 후 재설정)",
                    direction, job.get("name"), f"{cur_price:,.0f}",
                    f"{lower:,.0f}", f"{upper:,.0f}", reinit_min)
        return True

    try:
        escaped_at = datetime.strptime(escaped_at_str, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        elapsed_min = (now - escaped_at).total_seconds() / 60
        logger.info("그리드 이탈 지속: %s %.0f분 경과 (재설정까지 %.0f분)",
                    job.get("name"), elapsed_min, max(0.0, reinit_min - elapsed_min))

        if elapsed_min >= reinit_min:
            # X분 경과: 하단 이탈 시 잔고 부족하면 손절 1개 먼저
            if is_below and _stop_loss_top_grid(job):
                logger.info("그리드 손절 후 다음 사이클에 reinit 시도: %s", job.get("name"))
                return True  # 이번 사이클은 손절만, 다음 사이클에 reinit

            # 잔고 충분하거나 손절 대상 없음 → reinit 실행
            half = (upper - lower) / 2
            job["lower_price"] = round(cur_price - half, 2)
            job["upper_price"] = round(cur_price + half, 2)
            job.pop("escaped_at", None)
            job["status"] = "reinit"
            logger.info("그리드 자동 재설정 트리거: %s → 새 범위 %s~%s원",
                        job.get("name"), f"{job['lower_price']:,.1f}", f"{job['upper_price']:,.1f}")
            return True
    except Exception as e:
        logger.error("이탈 시간 파싱 실패: %s", e)

    return False


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
            is_escaped = False
            # 이탈 자동 재설정 체크
            if job.get("auto_reinit_minutes") and job.get("grids"):
                try:
                    cur_for_reinit = upbit_api.get_price(job["ticker"])["price"]
                    if _check_auto_reinit(job, cur_for_reinit):
                        changed = True
                    is_escaped = bool(job.get("escaped_at"))  # 이탈 중이면 True
                except Exception as e:
                    logger.warning("이탈 체크 현재가 조회 실패 %s: %s", name, e)

            # reinit 전환된 경우 즉시 처리
            if job.get("status") == "reinit":
                logger.info("그리드 재초기화 실행 (이탈 자동 재설정): %s", name)
                stop_grid(job)
                job["status"] = "init"
                if initialize_grid(job):
                    changed = True
                else:
                    changed = True
            elif is_escaped:
                # 이탈 중 — 새 매수 주문 등록 방지
                logger.info("이탈 대기 중 — 그리드 처리 스킵: %s", name)
            else:
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
