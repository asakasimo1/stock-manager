"""
주식 그리드 트레이딩 잡

전략:
  - 지정 가격 범위를 grid_pct% 간격으로 나눠 지정가 매수 주문 일괄 등록
  - 매수 체결(미체결 목록 소멸) -> 상위 격자에 지정가 매도 주문 등록
  - 매도 체결 -> 동일 격자에 매수 주문 재등록 (사이클 반복)

상태: job.status = init -> active -> stopping -> stopped
      grid.state = idle | buy_waiting | sell_waiting

동시활성 상한: job.max_active_orders (기본 DEFAULT_MAX_ACTIVE_ORDERS) —
  buy_waiting + sell_waiting 합계 기준. 예수금이 그리드 하나에 과도하게
  묶여 다른 매매에 못 쓰이는 것을 막기 위함.

재초기화(reinit) 시 보유물량 이월: stop_grid()는 미체결 주문만 취소할 뿐
  이미 매수 체결되어 보유 중인 주식은 계좌에 그대로 남는다. 재초기화 전
  _extract_held_inventory()로 보유물량을 회수해 initialize_grid()의
  carried 인자로 넘겨, 새 그리드에서도 매도 주문을 이어서 추적한다
  (분실/고아물량 방지).

KRX 정규장 동시호가(08:30~09:00 KST) 동안은 현재가가 예상체결가라
실제 체결가와 다를 수 있어, 이 구간에는 범위이탈/재초기화/체결처리 등
가격판단을 전부 보류한다 (kis_api._is_krx_call_auction() 참고).

설정 파일 (Gist): stock_grid_jobs.json
"""
import logging
import time
from datetime import datetime, timezone, timedelta

import kis_api
import gist_writer
import notify

KST = timezone(timedelta(hours=9))
DEFAULT_MAX_ACTIVE_ORDERS = 6  # job.max_active_orders 미설정 시 기본값
MIN_QTY = 1

logger = logging.getLogger(__name__)


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def build_levels(lower: float, upper: float, grid_pct: float) -> list:
    levels, price = [], lower
    while price <= upper * 1.0001:
        levels.append(kis_api.round_price(price))
        price *= (1 + grid_pct / 100)
    return sorted(set(levels))


def _buy_qty(krw: float, price: int) -> int:
    return max(MIN_QTY, int(krw / price))


REBALANCE_GRID_STEPS = 3  # 실시간 추적/손절 판단 기준 — "N격자 이상 벌어지면"


def _rebalance_grid(job: dict, grids: list, cur_price: float, market_open: bool) -> bool:
    """매 사이클(30초)마다 실시간으로 가격을 따라가도록 두 가지를 처리한다
    (2026-08-23, 사용자 요청 — "계속 따라다니면서 걸어줘, 손절도 해도 되는 것으로".
    코인 그리드(job_coin_grid.py)와 동일 로직, KIS API 차이만 반영):

    1) 아직 체결 안 된 매수 대기 주문이 현재가보다 REBALANCE_GRID_STEPS(3)격자 이상
       아래로 뒤처지면 취소하고 사다리 최상단 위로 재배치.
    2) 보유 중인(매도 대기) 포지션이 매수가 대비 REBALANCE_GRID_STEPS(3)격자 이상
       손실이면 시장가로 손절하고 사다리 최하단 아래로 재배치.

    KIS는 Upbit의 get_order 같은 "체결가 즉시 재조회" 수단이 없어(기존 체결감지도
    미체결 목록 소멸 여부로만 판단) 취소 레이스 확인을 미체결 목록(pending) 재조회로
    대신한다: 취소 시도 *전*에 이미 pending에 없으면 건드리지 않고(이미 체결/취소된
    것) 그대로 두어 아래 일반 체결감지 루프에 맡기고, 취소 *후*에도 pending 캐시를
    무효화하고 다시 확인해서 실제로 사라졌을 때만 재배치를 진행한다. 손절 체결가는
    시장가 주문의 실제 체결가를 바로 재조회할 방법이 없어 이번 사이클의 cur_price로
    근사한다(시장가라 큰 괴리는 없을 것으로 봄 — 필요시 추후 정교화)."""
    if not market_open or cur_price is None or len(grids) < 2:
        return False
    ticker     = job["ticker"]
    grid_pct   = float(job["grid_pct"])
    today_str  = datetime.now(KST).strftime("%Y-%m-%d")
    step_ratio = (1 + grid_pct / 100) ** REBALANCE_GRID_STEPS
    changed    = False

    # ── 너무 뒤처진 미체결 매수 대기 주문 재배치 ──────────────────
    for grid in grids:
        if grid.get("state") != "buy_waiting":
            continue
        level = float(grid["level"])
        if cur_price < level * step_ratio:
            continue
        order_no = grid.get("order_no", "")
        if not order_no or order_no not in _get_pending_set():
            continue  # 이미 체결/취소됨 — 일반 체결감지 루프에 맡김
        try:
            kis_api.cancel_order(order_no, grid.get("org_no", ""))
        except Exception as e:
            logger.error("  재배치용 매수취소 실패 %s원: %s", f"{level:,}", e)
            continue
        _invalidate_pending()
        if order_no in _get_pending_set():
            logger.warning("  재배치용 매수취소 확인 실패(여전히 미체결 목록에 있음) %s원", f"{level:,}")
            continue

        top = max(float(g["level"]) for g in grids)
        new_level = kis_api.round_price(top * (1 + grid_pct / 100))
        grid.update(level=new_level, state="idle", order_no="", org_no="",
                    qty=0, last_buy_price=0, last_sell_price=0)
        changed = True
        logger.info("↗ 미체결 매수 재배치: %s %s원 → %s원  (현재가 %s원, %d격자 이상 뒤처짐)",
                    job.get("name", ticker), f"{level:,}", f"{new_level:,}",
                    f"{cur_price:,}", REBALANCE_GRID_STEPS)

    # ── N격자 이상 손실 난 보유 포지션 손절 ────────────────────
    for grid in grids:
        if grid.get("state") != "sell_waiting":
            continue
        buy_price = float(grid.get("last_buy_price", 0) or 0)
        if buy_price <= 0:
            continue
        if cur_price > buy_price / step_ratio:
            continue
        order_no = grid.get("order_no", "")
        if not order_no or order_no not in _get_pending_set():
            continue  # 이미 목표가 체결/취소됨 — 일반 체결감지 루프에 맡김(이중매도 방지)
        try:
            kis_api.cancel_order(order_no, grid.get("org_no", ""))
        except Exception as e:
            logger.error("  손절용 매도취소 실패 %s원: %s", f"{buy_price:,}", e)
            continue
        _invalidate_pending()
        if order_no in _get_pending_set():
            logger.warning("  손절용 매도취소 확인 실패(여전히 미체결 목록에 있음) %s원", f"{buy_price:,}")
            continue

        qty = grid.get("qty", 0)
        try:
            r = kis_api.place_order(ticker, "SELL", qty, order_type="market")
            _invalidate_pending()
        except Exception as e:
            logger.error("  !!! 손절 시장가 매도 실패 %s %s원 %d주: %s — 지정가 재등록 시도",
                        ticker, f"{buy_price:,}", qty, e)
            try:
                fallback_price = int(grid.get("last_sell_price") or buy_price * (1 + grid_pct / 100))
                r2 = kis_api.place_order(ticker, "SELL", qty, fallback_price, "limit")
                grid.update(order_no=r2["order_no"], org_no=r2.get("org_no", ""), order_date=today_str)
                _invalidate_pending()
            except Exception as e2:
                grid.update(order_no="", org_no="")
                logger.error("  !!! 손절 실패 + 지정가 재등록도 실패 — 무방비 포지션: %s %d주 @ %s원: %s",
                            ticker, qty, f"{buy_price:,}", e2)
            continue

        sell_exec = cur_price  # KIS 시장가 체결가 즉시 재조회 수단이 없어 근사(위 docstring 참고)
        pnl = (sell_exec * (1 - kis_api.SELL_FEE) - buy_price * (1 + kis_api.BUY_FEE)) * qty
        job["total_profit_krw"] = round(job.get("total_profit_krw", 0) + pnl, 0)
        job["trade_count"]      = job.get("trade_count", 0) + 1
        _now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        hist = job.setdefault("trade_history", [])
        hist.append({"date": _now[:10], "time": _now[11:],
                     "buy_time": grid.get("buy_time", ""),
                     "buy_price": round(buy_price, 2), "sell_price": round(sell_exec, 2),
                     "qty": qty, "profit": round(pnl, 2), "reason": "손절"})
        if len(hist) > 500:
            job["trade_history"] = hist[-500:]
        logger.info("★ 손절체결 @ %s원 손익 %+.0f원 (매수가 %s원, %d격자 이상 손실)",
                    f"{sell_exec:,}", pnl, f"{buy_price:,}", REBALANCE_GRID_STEPS)

        bottom = min(float(g["level"]) for g in grids)
        new_level = kis_api.round_price(bottom / (1 + grid_pct / 100))
        grid.update(level=new_level, state="idle", order_no="", org_no="",
                    qty=0, last_buy_price=0, last_sell_price=0)
        changed = True

    if changed:
        job["lower_price"] = min(float(g["level"]) for g in grids)
        job["upper_price"] = max(float(g["level"]) for g in grids)
    return changed


_pending_cache: dict = {}
_pending_fetched_at: float = 0.0


def _get_pending_set() -> set:
    global _pending_cache, _pending_fetched_at
    now = time.monotonic()
    if now - _pending_fetched_at < 30:
        return set(_pending_cache.keys())
    try:
        orders = kis_api.get_pending_orders()
        _pending_cache = {o["order_no"]: o for o in orders if o["order_no"]}
        _pending_fetched_at = now
    except Exception as e:
        logger.warning("미체결 조회 실패: %s", e)
    return set(_pending_cache.keys())


def _invalidate_pending():
    global _pending_fetched_at
    _pending_fetched_at = 0.0


def _extract_held_inventory(job: dict) -> list:
    """재초기화 전, 실제 보유 중인(매수 체결된) 물량을 회수한다.
    stop_grid()는 미체결 주문만 취소하므로 보유 주식 자체는 계좌에 남는데,
    grids 배열을 통째로 새로 만들면 그 물량이 추적에서 빠져 고아가 된다."""
    pending = _get_pending_set()
    grid_pct = float(job.get("grid_pct", 1.5))
    held = []
    for g in job.get("grids", []):
        state    = g.get("state")
        order_no = g.get("order_no", "")
        qty      = g.get("qty", 0)
        if qty <= 0:
            continue
        if state == "sell_waiting":
            held.append({
                "qty":        qty,
                "buy_price":  g.get("last_buy_price", int(g["level"])),
                "sell_price": g.get("last_sell_price") or kis_api.round_price(
                    g.get("last_buy_price", int(g["level"])) * (1 + grid_pct / 100)),
            })
        elif state == "buy_waiting" and order_no and order_no not in pending:
            # 감지되지 않은 매수 체결 — 다음 사이클을 기다리지 않고 바로 회수
            level = int(g["level"])
            held.append({
                "qty":        qty,
                "buy_price":  level,
                "sell_price": kis_api.round_price(level * (1 + grid_pct / 100)),
            })
        elif state == "idle":
            # 매도등록/재매수 실패 후 idle로 남으며 수량만 남은 "고아물량" —
            # 이 분기가 없으면 sell_waiting/buy_waiting만 보는 위 조건들에
            # 걸리지 않아 재초기화가 와도 영구히 회수되지 않았음(2026-08-28
            # 실사용자 리포트로 발견 — 두산에너빌리티 1주가 이 상태로 방치돼
            # 있었음).
            held.append({
                "qty":        qty,
                "buy_price":  g.get("last_buy_price") or int(g["level"]),
                "sell_price": g.get("last_sell_price") or kis_api.round_price(
                    (g.get("last_buy_price") or int(g["level"])) * (1 + grid_pct / 100)),
            })
    return held


def initialize_grid(job: dict, carried: list = None) -> bool:
    ticker       = job["ticker"]
    grid_pct     = float(job.get("grid_pct", 1.5))
    lower        = float(job.get("lower_price", 0))
    upper        = float(job.get("upper_price", 0))
    krw_per_grid = float(job.get("krw_per_grid", 100000))
    max_active   = int(job.get("max_active_orders", DEFAULT_MAX_ACTIVE_ORDERS))
    carried      = carried or []

    if lower <= 0 or upper <= 0 or upper <= lower:
        logger.error("가격 범위 오류: %s lower=%s upper=%s", ticker, lower, upper)
        return False

    try:
        info = kis_api.get_price(ticker)
        cur_price = int(info["stck_prpr"])
    except Exception as e:
        logger.error("현재가 조회 실패 %s: %s", ticker, e)
        return False

    levels = build_levels(lower, upper, grid_pct)

    # 초기 시장가 매수 1건 — 그리드가 하락만 기다리면 가격이 계속 오르기만
    # 하는 장에서 지정가 매수가 영원히 체결 안 될 수 있어(2026-08-28
    # 사용자 리포트), 이월물량이 없을 때만(중복매수 방지) 현재가 부근에서
    # 즉시 1건 매수해 최소 1건은 보유하고 시작한다.
    will_init_buy = not carried
    buy_candidates = [l for l in levels if l < cur_price]
    remaining_active = max(0, max_active - len(carried) - (1 if will_init_buy else 0))
    active_buy_set = set(buy_candidates[-remaining_active:]) if remaining_active else set()

    logger.info("그리드 초기화 %s: 격자 %d개 현재가 %s원 (이월물량 %d건, 활성상한 %d)",
                ticker, len(levels), f"{cur_price:,}", len(carried), max_active)

    grids = []
    step_ratio = (1 + grid_pct / 100) ** REBALANCE_GRID_STEPS

    # 이월 보유물량 — 매도 주문 재등록 (재초기화로 인한 고아물량 방지)
    for h in carried:
        qty, buy_price, sell_price = h["qty"], h["buy_price"], h["sell_price"]
        # 2026-08-27 코인 그리드 실사고(seed_held_inventory가 실제 평균매수가를
        # 그대로 반영하다 시장이 이미 그보다 3격자 넘게 하락한 상태여서
        # _rebalance_grid가 지정가 체결을 기다리지도 않고 즉시 시장가
        # 손절해버림, 리플 101개분 -9,543원 손실)와 동일 위험 방지 — 이미
        # 3격자 이상 하락한 실제 취득가를 그대로 쓰면 재초기화 직후 첫
        # 사이클에 바로 손절될 수 있어, 그 경우 현재가 기준 근사가로 대체
        # (손익 통계는 실제보다 보수적으로 잡힘 — 안전을 위한 트레이드오프).
        # 과거엔 이월 sell_price 기준으로 근사했으나 낙폭이 그보다 더 크면
        # 여전히 오탐할 수 있어, 현재가에 직접 앵커링해 드리프트와 무관하게
        # 항상 안전하도록 수정.
        if cur_price <= buy_price / step_ratio:
            buy_price  = kis_api.round_price(cur_price)
            sell_price = kis_api.round_price(cur_price * (1 + grid_pct / 100))
        grid = {"level": buy_price, "state": "sell_waiting", "order_no": "", "org_no": "",
                "qty": qty, "last_buy_price": buy_price, "last_sell_price": sell_price,
                "order_date": str(datetime.now(KST).date())}
        try:
            time.sleep(0.25)
            r = kis_api.place_order(ticker, "SELL", qty, sell_price, "limit")
            grid.update(order_no=r["order_no"], org_no=r.get("org_no", ""))
            logger.info("  이월물량 매도재등록 %s원 %d주 %s", f"{sell_price:,}", qty, r["order_no"])
        except Exception as e:
            logger.error("  이월물량 매도등록실패 %s원 (다음 사이클 재시도): %s", f"{sell_price:,}", e)
        grids.append(grid)
    if carried:
        _invalidate_pending()

    if will_init_buy:
        qty0 = _buy_qty(krw_per_grid, cur_price)
        try:
            time.sleep(0.25)
            r0 = kis_api.place_order(ticker, "BUY", qty0, order_type="market")
            _invalidate_pending()
            above = [l for l in levels if l > cur_price]
            sell_price0 = above[0] if above else kis_api.round_price(cur_price * (1 + grid_pct / 100))
            time.sleep(0.25)
            r1 = kis_api.place_order(ticker, "SELL", qty0, sell_price0, "limit")
            grids.append({"level": cur_price, "state": "sell_waiting",
                          "order_no": r1["order_no"], "org_no": r1.get("org_no", ""),
                          "qty": qty0, "last_buy_price": cur_price, "last_sell_price": sell_price0,
                          "order_date": str(datetime.now(KST).date())})
            _invalidate_pending()
            logger.info("  초기 시장가매수 %s원 %d주 -> 매도등록 %s원", f"{cur_price:,}", qty0, f"{sell_price0:,}")
        except Exception as e:
            logger.error("  초기 시장가매수 실패(지정가 사다리만으로 시작): %s", e)

    funds_exhausted = False
    for level in levels:
        grid = {"level": level, "state": "idle", "order_no": "", "org_no": "",
                "qty": 0, "last_buy_price": 0, "last_sell_price": 0}
        if level in active_buy_set and not funds_exhausted:
            qty = _buy_qty(krw_per_grid, level)
            try:
                time.sleep(0.25)
                r = kis_api.place_order(ticker, "BUY", qty, level, "limit")
                grid.update(state="buy_waiting", order_no=r["order_no"],
                            org_no=r.get("org_no", ""), qty=qty, last_buy_price=level,
                            order_date=str(datetime.now(KST).date()))
                logger.info("  매수등록 %s원 %d주 %s", f"{level:,}", qty, r["order_no"])
                _invalidate_pending()
            except Exception as e:
                err = str(e)
                if any(k in err for k in ["주문가능금액", "잔고부족", "insufficient"]):
                    funds_exhausted = True
                else:
                    logger.error("  격자매수실패 %s원: %s", f"{level:,}", e)
        grids.append(grid)

    buy_cnt = sum(1 for g in grids if g["state"] == "buy_waiting")
    sell_cnt = sum(1 for g in grids if g["state"] == "sell_waiting")
    job.update(grids=grids, init_price=cur_price, status="active", initialized_at=now_kst())
    carried_note = f"  이월보유 {sell_cnt}건\n" if carried else ""
    notify.send(
        f"📊 <b>주식 그리드 초기화</b>  {job.get('name', ticker)}\n"
        f"  범위 {lower:,.0f}~{upper:,.0f}원  격자 {len(levels)}개  간격 {grid_pct}%\n"
        f"  현재가 {cur_price:,}원  매수주문 {buy_cnt}개  (활성상한 {max_active}건)\n"
        f"{carried_note}"
    )
    return True


def _fill_idle(grids, ticker, cur_price, krw_per_grid, bwc_fn, max_active):
    if bwc_fn() >= max_active:
        return
    candidates = sorted(
        [g for g in grids if g.get("state") == "idle" and int(g["level"]) < cur_price],
        key=lambda g: -int(g["level"])
    )
    for grid in candidates:
        if bwc_fn() >= max_active:
            break
        level = int(grid["level"])
        qty = _buy_qty(krw_per_grid, level)
        try:
            time.sleep(0.25)
            r = kis_api.place_order(ticker, "BUY", qty, level, "limit")
            grid.update(state="buy_waiting", order_no=r["order_no"],
                        org_no=r.get("org_no",""), qty=qty, last_buy_price=level,
                        order_date=str(datetime.now(KST).date()))
            _invalidate_pending()
            logger.info("  idle보충 %s원 %d주 %s", f"{level:,}", qty, r["order_no"])
            break
        except Exception as e:
            if any(k in str(e) for k in ["주문가능금액", "잔고부족"]):
                break
            logger.error("  idle보충실패 %s원: %s", f"{level:,}", e)


def process_grid(job: dict, cur_price: int = None) -> bool:
    ticker       = job["ticker"]
    grid_pct     = float(job.get("grid_pct", 1.5))
    krw_per_grid = float(job.get("krw_per_grid", 100000))
    max_active   = int(job.get("max_active_orders", DEFAULT_MAX_ACTIVE_ORDERS))
    grids        = job.get("grids", [])
    changed      = False

    _now_kst    = datetime.now(KST)
    today_str   = _now_kst.strftime("%Y-%m-%d")
    # KRX 정규장 + NXT 프리마켓/애프터마켓 전부 포함.
    # (과거 "정규시장(112) 시간 주문불가" 오류의 원인은 구버전 TR_ID(TTTC0802U/0801U)와
    #  존재하지 않는 ORD_SVR_DVSN_CD 필드 사용 때문이었음 — place_order()/cancel_order()를
    #  신TR_ID(TTTC0012U/0011U) + EXCG_ID_DVSN_CD로 수정하여 근본 해결, 실거래로 검증됨)
    market_open = kis_api.is_any_market_open()

    # 실시간 가격 추적/손절 — 매 사이클마다 판단(2026-08-23). 아래 pending 스냅샷을
    # 뜨기 전에 실행해서, 재배치로 취소된 주문이 아래 일반 체결감지 루프에서 stale한
    # pending 값 때문에 잘못 처리되지 않게 한다.
    if _rebalance_grid(job, grids, cur_price, market_open):
        changed = True

    pending = _get_pending_set()

    def bwc():
        return sum(1 for g in grids if g.get("state") in ("buy_waiting", "sell_waiting"))

    for i, grid in enumerate(grids):
        state    = grid.get("state", "idle")
        level    = int(grid["level"])
        order_no = grid.get("order_no", "")

        if state == "buy_waiting" and order_no:
            if order_no not in pending:
                order_date = grid.get("order_date", "")
                if order_date and order_date < today_str:
                    # 전일 미체결 → 장 시작 시 재등록
                    if market_open:
                        qty2 = _buy_qty(krw_per_grid, level)
                        try:
                            time.sleep(0.25)
                            r = kis_api.place_order(ticker, "BUY", qty2, level, "limit")
                            grid.update(state="buy_waiting", order_no=r["order_no"],
                                        org_no=r.get("org_no",""), qty=qty2,
                                        last_buy_price=level, order_date=today_str)
                            changed = True
                            _invalidate_pending()
                            logger.info("↻전일만료→재등록(매수) %s원 %d주", f"{level:,}", qty2)
                        except Exception as e:
                            grid.update(state="idle", order_no="", org_no="")
                            logger.error("만료재등록실패(매수) %s원: %s", f"{level:,}", e)
                else:
                    # 당일 체결 → 매도 등록 (장 중에만 처리)
                    if market_open:
                        qty = grid["qty"]
                        avg = grid["last_buy_price"]
                        _buy_time = datetime.now(KST).strftime("%H:%M")
                        grid.update(order_no="", org_no="", buy_time=_buy_time)
                        sell_price = kis_api.round_price(avg * (1 + grid_pct / 100))
                        if i + 1 < len(grids):
                            sell_price = max(sell_price, int(grids[i+1]["level"]))
                        try:
                            time.sleep(0.25)
                            r = kis_api.place_order(ticker, "SELL", qty, sell_price, "limit")
                            grid.update(state="sell_waiting", order_no=r["order_no"],
                                        org_no=r.get("org_no",""), last_sell_price=sell_price,
                                        order_date=today_str)
                            changed = True
                            _invalidate_pending()
                            logger.info("★매수체결 %s원 -> 매도등록 %s원 %d주",
                                        f"{avg:,}", f"{sell_price:,}", qty)
                            _fill_idle(grids, ticker, avg, krw_per_grid, bwc, max_active)
                        except Exception as e:
                            grid.update(state="idle")
                            logger.error("매도등록실패 %s원: %s", f"{level:,}", e)

        elif state == "sell_waiting" and order_no:
            if order_no not in pending:
                order_date = grid.get("order_date", "")
                if order_date and order_date < today_str:
                    # 전일 미체결 매도 → 장 시작 시 재등록 (보유 주식 재매도)
                    if market_open:
                        sell_price = int(grid["last_sell_price"])
                        qty = grid["qty"]
                        try:
                            time.sleep(0.25)
                            r = kis_api.place_order(ticker, "SELL", qty, sell_price, "limit")
                            grid.update(order_no=r["order_no"],
                                        org_no=r.get("org_no",""), order_date=today_str)
                            changed = True
                            _invalidate_pending()
                            logger.info("↻전일만료→재등록(매도) %s원 %d주", f"{sell_price:,}", qty)
                        except Exception as e:
                            grid.update(order_no="", org_no="")
                            logger.error("만료재등록실패(매도) %s원: %s", f"{level:,}", e)
                else:
                    # 당일 체결 (장 중에만 처리)
                    if market_open:
                        sell_exec = grid["last_sell_price"]
                        buy_exec  = grid["last_buy_price"]
                        qty       = grid["qty"]
                        pnl = (sell_exec * (1 - kis_api.SELL_FEE) - buy_exec * (1 + kis_api.BUY_FEE)) * qty
                        job["total_profit_krw"] = round(job.get("total_profit_krw", 0) + pnl, 0)
                        job["trade_count"]      = job.get("trade_count", 0) + 1
                        _now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                        hist = job.setdefault("trade_history", [])
                        hist.append({"date": _now[:10], "time": _now[11:],
                                     "buy_time": grid.get("buy_time", ""),
                                     "buy_price": round(buy_exec, 2),
                                     "sell_price": round(sell_exec, 2),
                                     "qty": qty,
                                     "profit": round(pnl, 2)})
                        if len(hist) > 500:
                            job["trade_history"] = hist[-500:]
                        grid.update(order_no="", org_no="")
                        changed = True
                        _invalidate_pending()
                        logger.info("★매도체결 %s원 수익 %+.0f원 (누적 %+.0f원 %d회)",
                                    f"{sell_exec:,}", pnl, job["total_profit_krw"], job["trade_count"])
                        # 동일 격자에 매수 주문 재등록 — 이 격자가 현재가와 너무
                        # 멀어지면(REBALANCE_GRID_STEPS 이상) 다음 사이클의
                        # _rebalance_grid가 알아서 취소 후 재배치하므로, 여기서는
                        # 항상 동일 레벨로만 재등록한다.
                        qty2 = _buy_qty(krw_per_grid, level)
                        try:
                            time.sleep(0.25)
                            r = kis_api.place_order(ticker, "BUY", qty2, level, "limit")
                            grid.update(state="buy_waiting", order_no=r["order_no"],
                                        org_no=r.get("org_no",""), qty=qty2,
                                        last_buy_price=level, order_date=today_str)
                            _invalidate_pending()
                            logger.info("  재매수등록 %s원 %d주", f"{level:,}", qty2)
                        except Exception as e:
                            grid.update(state="idle")
                            logger.error("재매수실패 %s원: %s", f"{level:,}", e)

        elif state == "sell_waiting" and not order_no:
            # 이월물량 매도등록 실패분 재시도 (재초기화 시 보유물량 이월 과정에서
            # 최초 등록이 실패했을 경우의 자동 복구)
            sell_price = int(grid.get("last_sell_price", 0))
            qty        = grid.get("qty", 0)
            if market_open and sell_price > 0 and qty > 0:
                try:
                    time.sleep(0.25)
                    r = kis_api.place_order(ticker, "SELL", qty, sell_price, "limit")
                    grid.update(order_no=r["order_no"], org_no=r.get("org_no", ""),
                                order_date=today_str)
                    changed = True
                    _invalidate_pending()
                    logger.info("↻이월물량 매도재시도등록 %s원 %d주", f"{sell_price:,}", qty)
                except Exception as e:
                    logger.error("이월물량 매도재시도실패 %s원: %s", f"{sell_price:,}", e)

        elif state == "idle" and grid.get("qty", 0) > 0:
            # 매도등록실패/재매수실패 등으로 idle 상태에 수량만 남아 방치된
            # "고아물량" 자동 복구 — 원래 재초기화(_extract_held_inventory)
            # 때만 회수됐는데 그 함수가 idle 상태를 아예 안 봐서 재초기화가
            # 없으면 영구 방치되는 버그였음(2026-08-28 사용자 리포트로 발견,
            # 034020 두산에너빌리티 1주가 실제로 이렇게 방치돼 있었음).
            # 재초기화를 기다리지 않고 매 사이클 재시도해서 즉시 복구한다.
            qty        = grid.get("qty", 0)
            buy_price  = grid.get("last_buy_price") or level
            sell_price = grid.get("last_sell_price") or kis_api.round_price(buy_price * (1 + grid_pct / 100))
            # 2026-08-27 코인 그리드 실사고와 동일 위험 방지 — 이미 3격자
            # 이상 하락한 채로 재등록하면 다음 사이클에 _rebalance_grid가
            # 바로 시장가 손절할 수 있어, 그 경우 한 격자 아래 근사가로 대체
            if cur_price:
                step_ratio = (1 + grid_pct / 100) ** REBALANCE_GRID_STEPS
                if cur_price <= buy_price / step_ratio:
                    buy_price  = kis_api.round_price(cur_price)
                    sell_price = kis_api.round_price(cur_price * (1 + grid_pct / 100))
            if market_open and qty > 0 and sell_price > 0:
                try:
                    time.sleep(0.25)
                    r = kis_api.place_order(ticker, "SELL", qty, sell_price, "limit")
                    grid.update(state="sell_waiting", order_no=r["order_no"], org_no=r.get("org_no", ""),
                                last_buy_price=buy_price, last_sell_price=sell_price, order_date=today_str)
                    changed = True
                    _invalidate_pending()
                    logger.info("↻고아물량 매도재등록 %s원 %d주 (매수가 %s원)",
                                f"{sell_price:,}", qty, f"{buy_price:,}")
                except Exception as e:
                    logger.error("고아물량 매도재등록실패 %s원: %s", f"{sell_price:,}", e)

    # 드리프트로 격자 level이 바뀌었을 수 있어 배열을 다시 가격순 정렬 —
    # 매수체결 시 "상위 격자" 판단이 grids[i+1] 인덱스 인접성에 의존하므로
    # (위 buy_waiting 분기의 sell_price 클램프, L323-324) 다음 사이클 전에 반드시
    # 맞춰둔다. 루프 도중이 아니라 끝난 뒤 1회만 정렬.
    grids.sort(key=lambda g: float(g["level"]))

    # idle 격자 주기적 보충 — 만료재등록/재매수 실패로 idle에 빠진 격자가
    # 다음 사이클에서도 방치되지 않도록 매 사이클 재시도 (NXT 프리마켓 초반
    # 주문거부 등 일시적 실패 이후 자동 복구되게 함).
    if market_open and cur_price:
        before = {id(g): g.get("state") for g in grids}
        _fill_idle(grids, ticker, cur_price, krw_per_grid, bwc, max_active)
        if any(g.get("state") != before[id(g)] for g in grids):
            changed = True

    return changed


def _stop_loss_top_grid(job: dict) -> bool:
    """하단 이탈 시 예수금 < krw_per_grid(격자 1개 매수 가능 금액)인 경우에만
    sell_waiting 격자 중 level이 가장 높은 것 1개를 시장가 손절.
    코인 그리드(job_coin_grid.py)의 동일 로직을 KIS 계좌 기준으로 이식
    (2026-08-13 사용자 요청 — 범위 이탈 시 손절 없이 계속 물량만 쌓이던 문제).
    Gist 저장이 필요한 변경 발생 시 True 반환."""
    ticker = job["ticker"]
    grids = job.get("grids", [])
    candidates = sorted(
        [g for g in grids if g.get("state") == "sell_waiting"],
        key=lambda g: -float(g["level"])
    )
    if not candidates:
        return False

    krw_per_grid = float(job.get("krw_per_grid", 0))
    try:
        bal = kis_api.get_balance()
        cash = bal.get("cash", 0)
        if cash >= krw_per_grid:
            logger.info("손절 스킵: 예수금 %s원 ≥ 격자당 %s원 (매수 가능)",
                        f"{cash:,.0f}", f"{krw_per_grid:,.0f}")
            return False
        logger.info("손절 진행: 예수금 %s원 < 격자당 %s원 (잔고 부족)",
                    f"{cash:,.0f}", f"{krw_per_grid:,.0f}")
    except Exception as e:
        logger.warning("손절 전 잔고 조회 실패 — 손절 보류: %s", e)
        return False

    grid     = candidates[0]
    level    = float(grid["level"])
    order_no = grid.get("order_no", "")
    org_no   = grid.get("org_no", "")
    qty      = int(grid.get("qty", 0))

    # 기존 매도 지정가 주문 취소 — 실패 시 이중 매도 방지를 위해 손절 보류
    if order_no:
        try:
            kis_api.cancel_order(order_no, org_no)
            grid["order_no"] = ""
            grid["org_no"]   = ""
        except Exception as e:
            logger.warning("손절: 기존 매도 취소 실패 → 손절 보류 (이중 매도 방지) %s원: %s",
                           f"{level:,.0f}", e)
            return False

    if qty <= 0:
        grid.update(state="idle", order_no="", org_no="", qty=0)
        return True

    try:
        kis_api.place_order(ticker, "SELL", qty, order_type="market")
        buy_price = float(grid.get("last_buy_price", 0) or 0)
        # 2026-08-24 — 예전엔 이 함수가 손익 계산·trade_history 기록을 아예 안 해서
        # 범위 이탈 손절이 거래내역에서 통째로 사라지는 버그가 있었음(코인 그리드와
        # 동일한 문제, job_coin_grid.py의 같은 수정 참고). KIS는 시장가 체결가를
        # 바로 재조회할 방법이 없어 이번 사이클의 현재가로 근사한다.
        try:
            sell_exec = float(kis_api.get_price(ticker)["stck_prpr"])
        except Exception:
            sell_exec = buy_price
        if buy_price > 0:
            pnl = (sell_exec * (1 - kis_api.SELL_FEE) - buy_price * (1 + kis_api.BUY_FEE)) * qty
            job["total_profit_krw"] = round(job.get("total_profit_krw", 0) + pnl, 0)
            _now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
            hist = job.setdefault("trade_history", [])
            hist.append({"date": _now[:10], "time": _now[11:],
                         "buy_time": grid.get("buy_time", ""),
                         "buy_price": round(buy_price, 2), "sell_price": round(sell_exec, 2),
                         "qty": qty, "profit": round(pnl, 2), "reason": "이탈손절"})
            if len(hist) > 500:
                job["trade_history"] = hist[-500:]
        else:
            pnl = 0
        job["trade_count"] = job.get("trade_count", 0) + 1
        grid.update(state="idle", order_no="", org_no="", qty=0, last_buy_price=0, last_sell_price=0)
        logger.info("▼ 손절 매도: %s %d주 @ %s원 손익 %+.0f원 (매수가 %s원 / 격자 %s원)",
                    ticker, qty, f"{sell_exec:,.0f}", pnl, f"{buy_price:,.0f}", f"{level:,.0f}")
        return True
    except Exception as e:
        logger.error("손절 매도 실패 %s원: %s", f"{level:,.0f}", e)
        grid.update(state="idle", order_no="", org_no="")
        return False


# auto_reinit_minutes를 안 쓰는 잡에서 stop_loss_on_escape가 손절 판단까지
# 기다리는 기본 대기 시간(분) — auto_reinit_minutes가 설정돼있으면 그 값을 대신 씀
# (job_coin_grid.py의 동일 상수/수정과 짝 — 2026-08-22).
STOP_LOSS_DEFAULT_WAIT_MIN = 15


def _check_out_of_range(job: dict, cur_price: int) -> bool:
    lower = float(job.get("lower_price", 0))
    upper = float(job.get("upper_price", float("inf")))
    name  = job.get("name", job.get("ticker", "?"))

    if lower <= cur_price <= upper:
        if job.get("out_of_range_since"):
            job.update(out_of_range_since=None, out_of_range_notified=False)
            return True
        return False

    direction = "하단 이탈" if cur_price < lower else "상단 돌파"
    modified = False

    if not job.get("out_of_range_since"):
        job.update(out_of_range_since=now_kst(), out_of_range_notified=False)
        modified = True

    if not job.get("out_of_range_notified"):
        job["out_of_range_notified"] = True
        modified = True
        auto_min = job.get("auto_reinit_minutes")
        hint = f"자동재초기화 {auto_min}분 후" if auto_min else "수동 reinit 필요"
        notify.send(
            f"⚠️ <b>그리드 범위 이탈 [{direction}]</b>  {name}\n"
            f"  현재가 {cur_price:,}원  범위 {lower:,.0f}~{upper:,.0f}원\n"
            f"  {hint}"
        )

    # 2026-08-22 — stop_loss_on_escape는 auto_reinit_minutes 설정 여부와 무관하게
    # 항상 평가함(job_coin_grid.py와 동일한 버그를 여기서도 발견해 같이 수정,
    # 사용자 요청). 예전엔 auto_reinit_minutes가 없으면 아래 elapsed 계산 블록
    # 전체가 `if auto_min:` 안에 있어서 손절이 영구히 발동 안 했음.
    since_str = job.get("out_of_range_since", "")
    is_below  = cur_price < lower
    auto_min  = job.get("auto_reinit_minutes")
    wait_min  = auto_min or STOP_LOSS_DEFAULT_WAIT_MIN
    try:
        since   = datetime.strptime(since_str, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        elapsed = (datetime.now(KST) - since).total_seconds() / 60
    except (ValueError, TypeError):
        elapsed = 0
    market_open = kis_api.is_any_market_open()

    # 하단 이탈 + 손절 옵션 ON(명시적으로 False가 아니면 기본 활성) + 대기시간 경과
    # + 장 열림 상태면 재초기화 전에 최상단 보유물량 1개 먼저 손절 — 예수금 없이
    # 계속 아래로만 물량이 쌓이는 것을 방지(2026-08-13 사용자 요청)
    if (is_below and job.get("stop_loss_on_escape") is not False
            and elapsed >= wait_min and market_open):
        if _stop_loss_top_grid(job):
            logger.info("그리드 손절 후 다음 사이클에 재초기화 시도: %s", name)
            return True  # 이번 사이클은 손절만, 다음 사이클에 reinit

    # 자동 재초기화(범위 이동)는 기존처럼 auto_reinit_minutes를 명시적으로
    # 설정한 잡에만 적용 — 범위 자체를 바꾸는 더 큰 변화라 opt-in 유지.
    if auto_min and elapsed >= auto_min and market_open:
        n_each = 5
        step = 1 + float(job.get("grid_pct", 1.5)) / 100
        nl = kis_api.round_price(cur_price / step ** n_each)
        nu = kis_api.round_price(cur_price * step ** n_each)
        old_lower, old_upper = job.get("lower_price"), job.get("upper_price")
        job.update(lower_price=nl, upper_price=nu, status="reinit",
                   out_of_range_since=None, out_of_range_notified=False)
        modified = True
        # 2026-08-26 — 이탈 자동재설정이 알림(notify.send)만 남기고 job 자체엔
        # 아무 기록도 안 남아서, 대시보드에서 "몇 시에 왜 재설정됐는지"를 나중에
        # 다시 볼 방법이 없었음(사용자 요청, coin 그리드와 동일하게) — 최근 20건만 보관.
        hist = job.setdefault("reinit_history", [])
        hist.append({
            "ts": now_kst(), "trigger_price": cur_price,
            "old_range": f"{old_lower:,.0f}~{old_upper:,.0f}",
            "new_range": f"{nl:,}~{nu:,}",
        })
        job["reinit_history"] = hist[-20:]
        notify.send(f"🔄 <b>그리드 자동재초기화</b>  {name}\n  새 범위 {nl:,}~{nu:,}원")

    return modified


def stop_grid(job: dict) -> int:
    cancelled = 0
    for grid in job.get("grids", []):
        order_no = grid.get("order_no", "")
        if order_no:
            try:
                if kis_api.cancel_order(order_no, grid.get("org_no", "")):
                    cancelled += 1
            except Exception as e:
                logger.warning("취소실패 %s: %s", order_no, e)
        grid.update(state="idle", order_no="", org_no="")
    job["status"] = "stopped"
    _invalidate_pending()
    logger.info("그리드 중단 %s: %d건 취소", job.get("ticker", ""), cancelled)
    return cancelled


def main():
    logger.info("주식 그리드 잡 체크 시작")
    jobs = gist_writer._read_gist_file("stock_grid_jobs.json")
    if jobs is None:
        logger.error("주식 그리드 잡 읽기 실패")
        return
    if not jobs:
        logger.info("등록된 주식 그리드 잡 없음")
        return

    changed = False
    for job in jobs:
        status = job.get("status", "active")
        name   = job.get("name", job.get("ticker", "?"))

        if status == "stopping":
            logger.info("중단처리: %s", name)
            stop_grid(job)
            changed = True

        elif status == "reinit":
            if kis_api._is_krx_call_auction():
                logger.info("동시호가(08:30~09:00) — 재초기화 보류: %s", name)
            else:
                logger.info("재초기화: %s", name)
                carried = _extract_held_inventory(job)
                stop_grid(job)
                job["status"] = "init"
                initialize_grid(job, carried=carried)
                changed = True

        elif status == "init" or (status == "active" and not job.get("grids")):
            if kis_api._is_krx_call_auction():
                logger.info("동시호가(08:30~09:00) — 초기화 보류: %s", name)
            else:
                logger.info("초기화: %s", name)
                initialize_grid(job)
                changed = True

        elif status == "active":
            if kis_api._is_krx_call_auction():
                logger.info("동시호가(08:30~09:00) — 매매판단 보류: %s", name)
            else:
                cur_price = None
                try:
                    info = kis_api.get_price(job["ticker"])
                    cur_price = int(info["stck_prpr"])
                    if _check_out_of_range(job, cur_price):
                        changed = True
                except Exception as e:
                    logger.warning("현재가조회실패 %s: %s", name, e)

                if job.get("status") == "reinit":
                    carried = _extract_held_inventory(job)
                    stop_grid(job)
                    job["status"] = "init"
                    initialize_grid(job, carried=carried)
                    changed = True
                else:
                    if process_grid(job, cur_price):
                        changed = True

    if changed:
        gist_writer._write_gist({"stock_grid_jobs.json": jobs})
    logger.info("주식 그리드 잡 체크 완료")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
