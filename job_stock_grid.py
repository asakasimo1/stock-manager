"""
주식 그리드 트레이딩 잡

전략:
  - 지정 가격 범위를 grid_pct% 간격으로 나눠 지정가 매수 주문 일괄 등록
  - 매수 체결(미체결 목록 소멸) -> 상위 격자에 지정가 매도 주문 등록
  - 매도 체결 -> 동일 격자에 매수 주문 재등록 (사이클 반복)

상태: job.status = init -> active -> stopping -> stopped
      grid.state = idle | buy_waiting | sell_waiting

설정 파일 (Gist): stock_grid_jobs.json
"""
import logging
import time
from datetime import datetime, timezone, timedelta

import kis_api
import gist_writer
import notify

KST = timezone(timedelta(hours=9))
MAX_ACTIVE_BUYS = 8
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


def initialize_grid(job: dict) -> bool:
    ticker       = job["ticker"]
    grid_pct     = float(job.get("grid_pct", 1.5))
    lower        = float(job.get("lower_price", 0))
    upper        = float(job.get("upper_price", 0))
    krw_per_grid = float(job.get("krw_per_grid", 100000))

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
    buy_candidates = [l for l in levels if l < cur_price]
    active_buy_set = set(buy_candidates[-MAX_ACTIVE_BUYS:])

    logger.info("그리드 초기화 %s: 격자 %d개 현재가 %s원", ticker, len(levels), f"{cur_price:,}")

    grids = []
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
    job.update(grids=grids, init_price=cur_price, status="active", initialized_at=now_kst())
    notify.send(
        f"📊 <b>주식 그리드 초기화</b>  {job.get('name', ticker)}\n"
        f"  범위 {lower:,.0f}~{upper:,.0f}원  격자 {len(levels)}개  간격 {grid_pct}%\n"
        f"  현재가 {cur_price:,}원  매수주문 {buy_cnt}개"
    )
    return True


def _fill_idle(grids, ticker, cur_price, krw_per_grid, bwc_fn):
    if bwc_fn() >= MAX_ACTIVE_BUYS:
        return
    candidates = sorted(
        [g for g in grids if g.get("state") == "idle" and int(g["level"]) < cur_price],
        key=lambda g: -int(g["level"])
    )
    for grid in candidates:
        if bwc_fn() >= MAX_ACTIVE_BUYS:
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
    grids        = job.get("grids", [])
    changed      = False
    pending      = _get_pending_set()

    _now_kst    = datetime.now(KST)
    today_str   = _now_kst.strftime("%Y-%m-%d")
    # KRX 정규장 + NXT 프리마켓/애프터마켓 전부 포함.
    # (과거 "정규시장(112) 시간 주문불가" 오류의 원인은 구버전 TR_ID(TTTC0802U/0801U)와
    #  존재하지 않는 ORD_SVR_DVSN_CD 필드 사용 때문이었음 — place_order()/cancel_order()를
    #  신TR_ID(TTTC0012U/0011U) + EXCG_ID_DVSN_CD로 수정하여 근본 해결, 실거래로 검증됨)
    market_open = kis_api.is_any_market_open()

    def bwc():
        return sum(1 for g in grids if g.get("state") == "buy_waiting")

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
                            _fill_idle(grids, ticker, avg, krw_per_grid, bwc)
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
                        pnl = (sell_exec * 0.998 - buy_exec * 1.00015) * qty
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

    # idle 격자 주기적 보충 — 만료재등록/재매수 실패로 idle에 빠진 격자가
    # 다음 사이클에서도 방치되지 않도록 매 사이클 재시도 (NXT 프리마켓 초반
    # 주문거부 등 일시적 실패 이후 자동 복구되게 함).
    if market_open and cur_price:
        before = {id(g): g.get("state") for g in grids}
        _fill_idle(grids, ticker, cur_price, krw_per_grid, bwc)
        if any(g.get("state") != before[id(g)] for g in grids):
            changed = True

    return changed


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

    auto_min = job.get("auto_reinit_minutes")
    if auto_min:
        since_str = job.get("out_of_range_since", "")
        try:
            since = datetime.strptime(since_str, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
            elapsed = (datetime.now(KST) - since).total_seconds() / 60
        except (ValueError, TypeError):
            elapsed = 0
        market_open = kis_api.is_any_market_open()
        if elapsed >= auto_min and market_open:
            n_each = 5
            step = 1 + float(job.get("grid_pct", 1.5)) / 100
            nl = kis_api.round_price(cur_price / step ** n_each)
            nu = kis_api.round_price(cur_price * step ** n_each)
            job.update(lower_price=nl, upper_price=nu, status="reinit",
                       out_of_range_since=None, out_of_range_notified=False)
            modified = True
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
            logger.info("재초기화: %s", name)
            stop_grid(job)
            job["status"] = "init"
            initialize_grid(job)
            changed = True

        elif status == "init" or (status == "active" and not job.get("grids")):
            logger.info("초기화: %s", name)
            initialize_grid(job)
            changed = True

        elif status == "active":
            cur_price = None
            try:
                info = kis_api.get_price(job["ticker"])
                cur_price = int(info["stck_prpr"])
                if _check_out_of_range(job, cur_price):
                    changed = True
            except Exception as e:
                logger.warning("현재가조회실패 %s: %s", name, e)

            if job.get("status") == "reinit":
                stop_grid(job)
                job["status"] = "init"
                initialize_grid(job)
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
