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
GRID_DRIFT_INTERVAL_MIN = 5  # 격자 드리프트(트레일링 이동) 최소 재실행 간격(분)
_escape_times: dict = {}  # Gist 저장 실패 대비 메모리 이탈 타이머 (ticker→escaped_at_str)


def _maybe_drift_grid(job: dict, grid: dict, grids: list, cur_price: float) -> bool:
    """매도 체결로 방금 idle이 된 격자가 사다리 양 끝(최저/최고가)이면, 가격이 그 격자를
    확실히 지나쳐갔을 때 그 자리에서 재등록하는 대신 반대편 끝 바깥으로 옮겨서 격자
    개수를 유지한 채 현재가 주변에 계속 격자가 몰려있게 한다(2026-08-23, 사용자 요청 —
    "고정범위 그리드"라 현재가가 범위 상단으로 올라가도 하단 격자가 방치된다는 리포트).
    잡 단위로 GRID_DRIFT_INTERVAL_MIN(5분)에 한 번만 판단 — 매 체결마다 옮기면 너무
    잦은 이동이 됨. 조건 불충족 시 False → 호출부가 기존처럼 동일 격자 재등록으로 폴백."""
    last_at = job.get("last_grid_shift_at")
    if last_at:
        try:
            elapsed_min = (datetime.now(KST) - datetime.strptime(last_at, "%Y-%m-%d %H:%M").replace(tzinfo=KST)).total_seconds() / 60
        except (ValueError, TypeError):
            elapsed_min = GRID_DRIFT_INTERVAL_MIN  # 파싱 실패 시 안전하게 이동 허용
        if elapsed_min < GRID_DRIFT_INTERVAL_MIN:
            return False

    levels = sorted(float(g["level"]) for g in grids)
    if len(levels) < 2:
        return False
    grid_pct = float(job["grid_pct"])
    level    = float(grid["level"])

    if level == levels[0] and cur_price > levels[1]:
        new_level = upbit_api.round_bid_price(levels[-1] * (1 + grid_pct / 100))
        direction = "위"
    elif level == levels[-1] and cur_price < levels[-2]:
        new_level = upbit_api.round_bid_price(levels[0] / (1 + grid_pct / 100))
        direction = "아래"
    else:
        return False

    old_level = grid["level"]
    # 호출 시점엔 아직 state가 "sell_waiting"(sell_uuid만 빈 문자열로 비워진 상태)로
    # 남아있음 — 매수/매도 필드도 함께 초기화해서 initialize_grid()가 만드는 새
    # idle 격자와 동일한 모양으로 맞춘다(그대로 두면 다음 사이클에 "sell_uuid 없는
    # sell_waiting"으로 걸려서 영구히 무시되는 격자가 됨).
    grid.update(level=new_level, state="idle", buy_uuid="", sell_uuid="",
                coin_qty=0, last_buy_price=0, last_sell_price=0)
    job["lower_price"] = min(float(g["level"]) for g in grids)
    job["upper_price"] = max(float(g["level"]) for g in grids)
    job["last_grid_shift_at"] = now_kst()
    logger.info("↕ 그리드 드리프트(%s): %s %s원(idle) → %s원  (현재가 %s원, 범위 %s~%s원 갱신)",
                direction, job.get("name", job.get("ticker")),
                f"{old_level:,.0f}", f"{new_level:,.0f}", f"{cur_price:,.0f}",
                f"{job['lower_price']:,.0f}", f"{job['upper_price']:,.0f}")
    return True


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
            # 현재가 기준 아래 5개(매수) / 위 5개(매도 대기) 고정 범위
            n_each    = 5
            step      = 1 + float(job.get("grid_pct", 1.5)) / 100
            new_lower = round(cur_price / step ** n_each)
            new_upper = round(cur_price * step ** n_each)
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
    funds_exhausted = False
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
        if level in active_buy_set and not funds_exhausted:
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
                    funds_exhausted = True  # break 대신 flag: 남은 격자 idle로 추가
                elif "too_many_requests" in err or "429" in err:
                    logger.warning("  Rate limit(429) — 초기화 중단 (나머지 idle → 다음 사이클에 복구)")
                    funds_exhausted = True
                else:
                    logger.error("  격자 %s원 매수 주문 실패: %s", f"{level:,.0f}", e)
        grids.append(grid)  # 항상 추가 (idle 포함) → 고아 코인 매도 등록에 필요

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
                _buy_time = datetime.now(KST).strftime("%H:%M")
                grid.update(coin_qty=coin_qty, last_buy_price=avg_price, buy_uuid="", buy_time=_buy_time)
                # 2026-08-21 — 그리드가 실제로 매수 체결한 수량만 누적하는 장부.
                # _sync_orphan_coins()가 계좌 잔고 전체가 아니라 이 장부를 기준으로
                # "고아 코인"을 판단하도록 해서, 사용자가 같은 코인을 업비트에서 직접
                # 매매한 수량까지 그리드가 건드리지 않게 하기 위함(아래 _sync_orphan_coins
                # 주석 참고).
                job["grid_owned_qty"] = round(job.get("grid_owned_qty", 0) + coin_qty, 8)

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
                # 매도 체결로 그리드 보유 장부에서 차감 (buy_waiting 체결 시 증가시킨 것과 짝)
                job["grid_owned_qty"] = round(max(0, job.get("grid_owned_qty", 0) - coin_qty), 8)
                _now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                hist = job.setdefault("trade_history", [])
                hist.append({"date": _now[:10], "time": _now[11:],
                             "buy_time": grid.get("buy_time", ""),
                             "buy_price": round(buy_exec, 2),
                             "sell_price": round(sell_exec, 2),
                             "qty": round(coin_qty, 8),
                             "profit": round(pnl, 2)})
                if len(hist) > 500:
                    job["trade_history"] = hist[-500:]
                grid.update(sell_uuid="")
                changed = True
                logger.info("★ 매도체결 @ %s원 순수익 %+.0f원 (누적 %+.0f원 / %d회)",
                            f"{sell_exec:,.0f}", pnl,
                            job["total_profit_krw"], job["trade_count"])

                if _maybe_drift_grid(job, grid, grids, sell_exec):
                    # 사다리 반대편 끝으로 이동 — 새 레벨은 idle 그대로 두고, 기존
                    # idle 재등록 경로(이 루프의 idle 분기 또는 다음 사이클)가 가격이
                    # 닿으면 알아서 매수 등록하게 둔다(여기서 별도 주문 로직 안 만듦).
                    changed = True
                else:
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

    # 드리프트로 격자 level이 바뀌었을 수 있어 배열을 다시 가격순 정렬 —
    # 매수체결 시 "상위 격자" 판단이 grids[i+1] 인덱스 인접성에 의존하므로
    # (위 buy_waiting 분기의 sell_price 클램프) 다음 사이클 전에 반드시 맞춰둔다.
    # 루프 도중이 아니라 끝난 뒤 1회만 정렬 — 순회 중 정렬하면 인덱스 기반
    # enumerate가 같은 격자를 두 번 처리하거나 건너뛸 수 있음.
    grids.sort(key=lambda g: float(g["level"]))

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
        job["grid_owned_qty"] = round(max(0, job.get("grid_owned_qty", 0) - coin_qty), 8)
        grid.update(state="idle", sell_uuid="", coin_qty=0)
        logger.info("▼ 손절 매도: %s %.8f개 (매수가 %s원 / 격자 %s원)",
                    job["ticker"], coin_qty, f"{buy_price:,.0f}", f"{level:,.0f}")
        return True
    except Exception as e:
        logger.error("손절 매도 실패 %s원: %s", f"{level:,.0f}", e)
        grid.update(state="idle", sell_uuid="")
        return False


def _grid_range(job: dict) -> tuple:
    """이탈 판단 기준 범위 — 실제 격자 레벨 범위가 lower/upper_price보다 넓을 수 있어
    둘 다 감안. (lower, upper) 반환."""
    grids = job.get("grids", [])
    if grids:
        levels = [float(g["level"]) for g in grids]
        lower = min(min(levels), float(job["lower_price"]))
        upper = max(max(levels), float(job["upper_price"]))
    else:
        lower = float(job["lower_price"])
        upper = float(job["upper_price"])
    return lower, upper


def _track_escape(job: dict, cur_price: float) -> tuple:
    """그리드 범위 이탈 여부를 추적해서 job["escaped_at"]를 갱신.
    반환: (changed, is_escaped).

    2026-08-22 — 예전엔 이 이탈 감지 자체가 _check_auto_reinit() 안에 있었고
    그 함수는 auto_reinit_minutes가 설정된 잡만 호출됐음. 그래서
    auto_reinit_minutes를 안 쓰고 stop_loss_on_escape만 켜둔 잡은 이탈 감지
    자체가 시작도 안 돼서 손절이 영구히 발동 안 하는 버그가 있었음(실측:
    2026-08-22 리플 그리드가 하단 8% 넘게 이탈한 채 2시간 넘게 손절 없이
    방치됨 — 사용자가 "여러 번 오류 발생시켰다"고 리포트). 이탈 감지를
    두 안전장치(손절/자동재설정) 모두가 공유하는 이 함수로 분리해서,
    아래 main()에서 auto_reinit_minutes 설정 여부와 무관하게 항상 호출한다."""
    lower, upper = _grid_range(job)
    ticker_key = job.get("ticker", "")

    if lower <= cur_price <= upper:
        if job.get("escaped_at") or ticker_key in _escape_times:
            job.pop("escaped_at", None)
            _escape_times.pop(ticker_key, None)
            logger.info("그리드 범위 복귀: %s (현재가 %s원)", job.get("name"), f"{cur_price:,.0f}")
            return True, False
        return False, False

    # 이탈 상태
    escaped_at_str = job.get("escaped_at") or _escape_times.get(ticker_key)
    if escaped_at_str and not job.get("escaped_at"):
        job["escaped_at"] = escaped_at_str  # Gist 저장 실패 대비 메모리에서 복원
        return True, True

    if not escaped_at_str:
        escaped_now = now_kst()
        _escape_times[ticker_key] = escaped_now
        job["escaped_at"] = escaped_now
        direction = "하단" if cur_price < lower else "상단"
        logger.info("그리드 %s 이탈 감지: %s 현재가 %s원 (범위 %s~%s원)",
                    direction, job.get("name"), f"{cur_price:,.0f}", f"{lower:,.0f}", f"{upper:,.0f}")
        return True, True

    return False, True


# auto_reinit_minutes를 안 쓰는 잡에서 stop_loss_on_escape가 손절 판단까지
# 기다리는 기본 대기 시간(분) — auto_reinit_minutes가 설정돼있으면 그 값을 대신 씀
# (기존 동작과 동일하게 유지).
STOP_LOSS_DEFAULT_WAIT_MIN = 15


def _check_stop_loss_on_escape(job: dict, cur_price: float) -> bool:
    """이탈 상태가 대기시간 이상 지속되면 최상단 보유 격자부터 순차 손절.
    **auto_reinit_minutes 설정 여부와 무관하게 항상 평가함** — stop_loss_on_escape
    설정 하나만으로 독립적으로 동작해야 하는 안전장치(위 _track_escape 주석 참고).
    job["escaped_at"]가 이미 채워져 있다고 가정(_track_escape를 먼저 호출했어야 함).
    Gist 저장이 필요한 변경 발생 시 True 반환."""
    if not job.get("stop_loss_on_escape", True):
        return False
    escaped_at_str = job.get("escaped_at")
    if not escaped_at_str:
        return False

    lower, _ = _grid_range(job)
    if cur_price >= lower:
        return False  # 손절은 하단 이탈 때만 — 상단 돌파는 오히려 유리한 상황

    wait_min = job.get("auto_reinit_minutes") or STOP_LOSS_DEFAULT_WAIT_MIN
    try:
        escaped_at = datetime.strptime(escaped_at_str, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        elapsed_min = (datetime.now(KST) - escaped_at).total_seconds() / 60
    except (ValueError, TypeError):
        logger.error("이탈 시간 파싱 실패(%s): %s", job.get("name"), escaped_at_str)
        return False

    if elapsed_min < wait_min:
        logger.info("그리드 이탈 지속(손절 대기 중): %s %.0f분 경과 (손절 판단까지 %.0f분)",
                    job.get("name"), elapsed_min, max(0.0, wait_min - elapsed_min))
        return False

    return _stop_loss_top_grid(job)


def _check_auto_reinit(job: dict, cur_price: float) -> bool:
    """이탈 상태가 auto_reinit_minutes 이상 지속되면 현재가 기준으로 범위를 재설정.
    auto_reinit_minutes 미설정 시 비활성(기존 동작 유지) — 이 재설정 자체는
    범위를 바꾸는 더 큰 변화라 명시적으로 켠 잡에만 적용한다(손절과 달리
    기본값으로 켜두지 않음). job["escaped_at"]가 이미 채워져 있다고 가정
    (_track_escape를 먼저 호출했어야 함). Gist 저장 필요 시 True 반환."""
    reinit_min = job.get("auto_reinit_minutes")
    # 2026-08-22 — 최소값 10→5분 (사용자 요청). 프론트엔드(tab-cointrade.js)의
    # 검증 최소값과 반드시 같이 맞출 것 — 다르면 프론트는 통과시켰는데 여기서
    # 조용히 무시되는 식의 혼선이 재발함.
    if not reinit_min or reinit_min < 5:
        return False
    escaped_at_str = job.get("escaped_at")
    if not escaped_at_str:
        return False

    lower, upper = _grid_range(job)
    try:
        escaped_at = datetime.strptime(escaped_at_str, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        elapsed_min = (datetime.now(KST) - escaped_at).total_seconds() / 60
    except (ValueError, TypeError):
        logger.error("이탈 시간 파싱 실패(%s): %s", job.get("name"), escaped_at_str)
        return False

    logger.info("그리드 이탈 지속: %s %.0f분 경과 (재설정까지 %.0f분)",
                job.get("name"), elapsed_min, max(0.0, reinit_min - elapsed_min))
    if elapsed_min < reinit_min:
        return False

    half = (upper - lower) / 2
    job["lower_price"] = round(cur_price - half, 2)
    job["upper_price"] = round(cur_price + half, 2)
    job.pop("escaped_at", None)
    _escape_times.pop(job.get("ticker", ""), None)
    job["status"] = "reinit"
    logger.info("그리드 자동 재설정 트리거: %s → 새 범위 %s~%s원",
                job.get("name"), f"{job['lower_price']:,.1f}", f"{job['upper_price']:,.1f}")
    return True


def _check_grid_safety(job: dict, cur_price: float) -> bool:
    """이탈 감지 → 손절(stop_loss_on_escape, 항상 평가) → 자동재설정
    (auto_reinit_minutes 설정 시에만)을 한 사이클에 순서대로 처리하는 통합
    진입점. main()은 auto_reinit_minutes 설정 여부와 무관하게 이 함수를
    호출해야 손절 안전장치가 항상 살아있는다(2026-08-22 버그 수정 핵심).
    Gist 저장 필요 시 True 반환."""
    changed, is_escaped = _track_escape(job, cur_price)
    if not is_escaped:
        return changed

    # 손절 먼저 — 손절이 일어났으면 이번 사이클은 종료(재설정은 다음 사이클에)
    if _check_stop_loss_on_escape(job, cur_price):
        return True

    if _check_auto_reinit(job, cur_price):
        return True

    return changed


# ─────────────────────────────────────────
# 고아 코인 동기화
# ─────────────────────────────────────────

def _sync_orphan_coins(job: dict) -> bool:
    """reinit 후 "그리드 자신이 매수 체결로 확보한 걸로 장부에 남아있는데" 현재
    격자엔 미추적인 코인(고아 코인)을 현재가 위 idle 격자에 지정가 매도로 등록.
    변경 발생 시 True 반환.

    2026-08-21 — 예전엔 Upbit 계좌 잔고 전체(actual_qty)를 기준으로 고아 여부를
    판단해서, 사용자가 같은 코인을 업비트 앱에서 직접 매매한 수량까지 "고아
    코인"으로 오판해 자동 매도 등록해버리는 위험이 있었음(job_coin_sell.py의
    auto_sell_by_rule()이 계좌 전체를 훑다 사용자 직접매매 코인을 자동매도한
    사고와 같은 종류 — 2026-08-21 사용자 요청으로 "그리드가 직접 산 코인에만"
    엄격히 한정). job["grid_owned_qty"](process_grid의 매수/매도 체결 시점마다
    누적·차감되는 장부, 계좌 잔고와 무관)를 기준 상한으로 쓰고, 실제 계좌 잔고와
    비교해 그 중 더 작은 값만 사용 — 그리드 장부에 없는 수량은 계좌에 아무리
    많이 있어도(=사용자 직접 매매분) 절대 건드리지 않는다."""
    ticker       = job["ticker"]
    grids        = job.get("grids", [])
    krw_per_grid = float(job.get("krw_per_grid", 0))

    tracked_qty = sum(
        float(g.get("coin_qty", 0))
        for g in grids if g.get("state") == "sell_waiting"
    )

    ledger_qty    = float(job.get("grid_owned_qty", 0))
    ledger_orphan = ledger_qty - tracked_qty
    if ledger_orphan < 1e-8:
        logger.info("고아 코인 없음(그리드 장부 기준) %s: 장부보유 %.8f개 = 추적 %.8f개",
                    ticker, ledger_qty, tracked_qty)
        return False

    try:
        bal = upbit_api.get_balance()
    except Exception as e:
        logger.warning("고아 코인 동기화 — 잔고 조회 실패: %s", e)
        return False

    try:
        cur_price = upbit_api.get_price(ticker)["price"]
    except Exception as e:
        logger.warning("고아 코인 동기화 — 현재가 조회 실패 %s: %s", ticker, e)
        return False

    coin_key   = ticker.split("-")[-1].upper()
    actual_qty = 0.0
    for h in bal.get("holdings", []):
        if h.get("symbol", "").upper() == coin_key:  # ticker는 KRW-XRP, symbol은 XRP
            actual_qty = float(h.get("qty", 0))
            break

    # 그리드 장부상 고아 수량과 "계좌 잔고 - 추적중" 중 더 작은 쪽만 사용 —
    # 계좌에 실제로 없는 걸 팔 수는 없고(안전장치 ①), 계좌엔 있어도 장부보다
    # 많으면(=사용자 직접 매매분 포함) 장부를 넘는 초과분은 절대 건드리지
    # 않는다(안전장치 ② — 이번 수정의 핵심).
    orphan_qty = min(ledger_orphan, actual_qty - tracked_qty)
    if orphan_qty < 1e-8:
        logger.info("고아 코인 없음(계좌 잔고 기준) %s: 실제 %.8f개 = 추적 %.8f개",
                    ticker, actual_qty, tracked_qty)
        return False

    logger.warning("고아 코인 감지 %s: 그리드장부 %.8f개 · 실제잔고 %.8f개 - 추적 %.8f개 = 고아 %.8f개 (≈ %s원)",
                   ticker, ledger_qty, actual_qty, tracked_qty, orphan_qty,
                   f"{orphan_qty * cur_price:,.0f}")

    # 현재가 위 idle 격자에 낮은 가격순(현재가에 가까운 순)으로 매도 등록
    idle_above = sorted(
        [g for g in grids if g.get("state") == "idle" and float(g["level"]) > cur_price],
        key=lambda g: float(g["level"])
    )

    changed   = False
    remaining = orphan_qty

    for grid in idle_above:
        if remaining < 1e-8:
            break
        level    = float(grid["level"])
        grid_qty = _buy_qty(krw_per_grid, level)
        sell_qty = min(remaining, grid_qty)

        sell_price = upbit_api.round_ask_price(level)
        try:
            time.sleep(0.12)
            r = upbit_api.place_order(
                market=ticker, side="ask", ord_type="limit",
                price=sell_price, volume=sell_qty
            )
            grid.update(
                state="sell_waiting",
                sell_uuid=r["uuid"],
                coin_qty=sell_qty,
                last_sell_price=sell_price,
            )
            remaining -= sell_qty
            changed    = True
            logger.info("  고아 코인 매도 등록 %s원 %.8f개 UUID:%s",
                        f"{sell_price:,.0f}", sell_qty, r["uuid"][:8])
        except Exception as e:
            logger.error("  고아 코인 매도 등록 실패 %s원: %s", f"{level:,.0f}", e)

    if remaining > 1e-8:
        logger.warning("고아 코인 미등록 잔여: %s %.8f개 (≈ %s원) — idle 격자 여유 부족",
                       ticker, remaining, f"{remaining * cur_price:,.0f}")

    return changed


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
                if _sync_orphan_coins(job):
                    changed = True
            else:
                changed = True      # stop 만 됐어도 저장 필요

        elif status in ("init",) or (status == "active" and not job.get("grids")):
            # grids:[] 상태에서도 이탈 체크 (escaped_at 미설정 버그 수정) — 보유
            # 코인이 없는 상태라 손절 대상은 없지만, 일관성을 위해 동일 진입점 사용.
            if status == "active":
                try:
                    cur_p = upbit_api.get_price(job["ticker"])["price"]
                    if _check_grid_safety(job, cur_p):
                        changed = True
                except Exception as e:
                    logger.warning("이탈 체크 실패 %s: %s", name, e)

            if job.get("status") == "reinit":
                logger.info("그리드 재초기화 실행: %s", name)
                stop_grid(job)
                job["status"] = "init"
                if initialize_grid(job):
                    changed = True
                    if _sync_orphan_coins(job):
                        changed = True
                else:
                    changed = True
            elif not job.get("escaped_at"):
                logger.info("그리드 초기화: %s", name)
                if initialize_grid(job):
                    changed = True
                    if _sync_orphan_coins(job):
                        changed = True
            else:
                logger.info("이탈 감지 후 reinit 대기 중: %s", name)

        elif status == "active":
            logger.info("그리드 처리: %s", name)
            is_escaped = False
            # 이탈 감지 + 손절(stop_loss_on_escape) + 자동재설정(auto_reinit_minutes)
            # — 2026-08-22 수정: auto_reinit_minutes 설정 여부와 무관하게 항상 평가.
            # 예전엔 이 블록 전체가 auto_reinit_minutes 설정된 잡에서만 돌아서,
            # stop_loss_on_escape만 켜고 auto_reinit_minutes를 안 쓴 잡은 손절
            # 안전장치 자체가 영구히 발동 안 하는 버그가 있었음(_check_grid_safety
            # 주석 참고, 사용자가 반복 리포트).
            if job.get("grids"):
                try:
                    cur_for_reinit = upbit_api.get_price(job["ticker"])["price"]
                    if _check_grid_safety(job, cur_for_reinit):
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
                    if _sync_orphan_coins(job):
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
