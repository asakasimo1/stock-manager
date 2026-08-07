"""
국내주식 초단타(스캘핑) 잡 — daemon_scalp.py 에서 10초 주기(장중에만)로 실행
Gist scalp_stock_jobs.json 에서 잡 읽기 → 모멘텀 진입/청산 판단 → KIS 시장가 주문 → 상태 갱신
Gist scalp_control.json 의 stock_enabled=false 이면 신규 진입 중단 + 보유 포지션 즉시 청산
Gist scalp_auto_config.json 의 stock.enabled=true 이면 KIS 등락률 순위에서 급등 후보를 watching 잡으로 자동 생성
  (source="auto" 잡은 1회 진입/청산 후 status=done 으로 종료 — 장마감까지 미체결이면 그날로 종료 처리)

잡 스키마는 job_scalp_coin.py의 scalp_coin_jobs.json 과 동일 구조 (qty는 정수 주 단위).
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import kis_api
import gist_writer
import scalp_engine

KST = timezone(timedelta(hours=9))
logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("scalp_stock_cloud.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s KST %(levelname)s %(message)s"))
logger.addHandler(_fh)

BUY_FEE  = 0.00015
SELL_FEE = 0.00195

MAX_CONCURRENT_POSITIONS = 3  # 주식 스캘핑 동시 보유 종목 상한 (하드 리밋)

DISCOVERY_INTERVAL_SEC = 30  # 자동발굴 스캔 주기 하한 — 등락률 순위 조회·Gist 쓰기 빈도 제한
_last_discover_at = 0.0


def _should_run_discovery(now_epoch: float) -> bool:
    global _last_discover_at
    if now_epoch - _last_discover_at < DISCOVERY_INTERVAL_SEC:
        return False
    _last_discover_at = now_epoch
    return True


def _today_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def _reset_daily_stats_if_needed(job: dict) -> bool:
    today = _today_str()
    if job.get("stats_date") != today:
        job["stats_date"] = today
        job["trades_today"] = 0
        job["realized_pnl_today"] = 0
        return True
    return False


def _net_buy_cost(price: float) -> float:
    return price * (1 + BUY_FEE)


def _net_sell_value(price: float) -> float:
    return price * (1 - SELL_FEE)


def _is_stock_enabled() -> bool:
    ctrl = gist_writer._read_gist_file("scalp_control.json")
    if not isinstance(ctrl, dict):
        return True
    return ctrl.get("stock_enabled", True)


def _load_auto_config() -> dict:
    cfg = gist_writer._read_gist_file("scalp_auto_config.json")
    if not isinstance(cfg, dict):
        return {}
    return cfg.get("stock") or {}


def _auto_discover(jobs: list, auto_cfg: dict, stock_enabled: bool, now_epoch: float) -> bool:
    """scalp_auto_config.json의 stock 설정에 따라 KIS 등락률 순위에서 급등 후보를 찾아
    watching 잡을 자동 생성. 반환: jobs 리스트가 변경되었는지 여부"""
    if not auto_cfg.get("enabled") or not stock_enabled:
        return False
    if not _should_run_discovery(now_epoch):
        return False

    today = _today_str()
    max_loss = float(auto_cfg.get("max_daily_loss_krw", -30000))
    auto_pnl_today = sum(
        j.get("realized_pnl_today", 0) for j in jobs
        if j.get("source") == "auto" and j.get("stats_date") == today
    )
    if auto_pnl_today <= max_loss:
        return False

    active_auto = [j for j in jobs if j.get("source") == "auto" and j.get("status") not in ("done", "stopped")]
    slots = int(auto_cfg.get("max_concurrent", 2)) - len(active_auto)
    if slots <= 0:
        return False

    try:
        ranking = kis_api.get_fluctuation_ranking(top_n=30)
    except Exception as e:
        logger.error("등락률 순위 조회 실패: %s", e)
        return False

    candidates = [
        {"ticker": r["ticker"], "name": r["name"], "chg_pct": r["chg_pct"], "liquidity": r["acml_vol"] * r["price"]}
        for r in ranking
    ]

    # 진행중인 티커 + 오늘 이미 한 번 시도한(성공/실패 무관) 자동발굴 티커는 재시도하지 않음
    existing_tickers = {
        j["ticker"] for j in jobs
        if j.get("status") not in ("done", "stopped")
        or (j.get("source") == "auto" and j.get("stats_date") == today)
    }
    max_day_chg = float(auto_cfg.get("max_day_chg_pct", 5.0))
    min_liquidity = float(auto_cfg.get("min_liquidity", 100_000_000))
    picked = scalp_engine.select_auto_candidates(candidates, existing_tickers, max_day_chg, min_liquidity, slots)

    for c in picked:
        jobs.append({
            "id":                 f"auto-{c['ticker']}-{int(now_epoch)}",
            "ticker":             c["ticker"],
            "name":               c["name"],
            "status":             "active",
            "phase":              "watching",
            "source":             "auto",
            "entry_momentum_pct": auto_cfg.get("entry_momentum_pct", 0.4),
            "lookback_sec":       auto_cfg.get("lookback_sec", 30),
            "max_day_chg_pct":    max_day_chg,
            "take_profit_pct":    auto_cfg.get("take_profit_pct", 0.6),
            "stop_loss_pct":      auto_cfg.get("stop_loss_pct", 0.4),
            "time_stop_sec":      auto_cfg.get("time_stop_sec", 180),
            "amount":             auto_cfg.get("amount", 0),
            "qty":                0,
            "max_daily_loss_krw": max_loss,
            "watch_timeout_sec":  auto_cfg.get("watch_timeout_sec", 300),
            "discovered_at":      now_epoch,
            "buy_price": 0, "buy_qty": 0, "entered_at": 0,
            "trades_today": 0, "realized_pnl_today": 0, "stats_date": today,
            "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        })
        logger.info("🔍 [자동포착] %s(%s) 등락률 %+.2f%% — watching 잡 생성", c["name"], c["ticker"], c["chg_pct"])

    return bool(picked)


def _fetch_prices(tickers: list) -> dict:
    result = {}

    def _fetch(t):
        try:
            return t, kis_api.get_price(t)
        except Exception as e:
            logger.error("현재가 조회 실패 %s: %s", t, e)
            return t, None

    with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as ex:
        for t, info in ex.map(_fetch, tickers):
            if info:
                result[t] = info
    return result


def _force_close(job: dict, cur_price: int, reason: str) -> None:
    ticker = job["ticker"]
    name   = job.get("name", ticker)
    qty    = int(job.get("buy_qty", 0))
    if qty <= 0:
        job["phase"] = "watching"
        return
    try:
        result = kis_api.place_order(ticker, "SELL", qty, order_type="market")
        buy_price = float(job.get("buy_price", 0))
        pnl = (_net_sell_value(cur_price) - _net_buy_cost(buy_price)) * qty
        job["realized_pnl_today"] = job.get("realized_pnl_today", 0) + pnl
        job["trades_today"] = job.get("trades_today", 0) + 1
        job["phase"] = "watching"
        job["buy_price"] = 0
        job["buy_qty"] = 0
        job["entered_at"] = 0
        if job.get("source") == "auto":
            job["status"] = "done"  # 자동발굴 잡은 1회성 — 청산 후 슬롯 반환
        gist_writer.log_trade(ticker, name, "sell", cur_price, qty, pnl=pnl,
                               pnl_pct=(pnl / (buy_price * qty) * 100) if buy_price > 0 else None,
                               reason=reason, order_no=result.get("order_no", ""))
        logger.info("★ [%s] 강제청산 %s %d주 @ %d원  손익 %+,.0f원", reason, ticker, qty, cur_price, pnl)
    except Exception as e:
        logger.error("%s 강제청산 실패: %s", ticker, e)


def _close_stale_auto_watching(jobs: list) -> bool:
    """장마감 시 그날 미체결로 끝난 자동발굴 watching 잡을 종료 처리.
    (당일 급등 후보였을 뿐이라 다음날까지 들고 있을 이유가 없고, 방치하면
    'watching=active'가 '실행중'으로 표시돼 이미 끝난 매매처럼 보이는 혼란을 줌)
    반환: jobs가 변경되었는지 여부"""
    changed = False
    for job in jobs:
        if job.get("source") == "auto" and job.get("phase") == "watching" and job.get("status") == "active":
            job["status"] = "done"
            job["stop_reason"] = "장마감 — 미체결 종료"
            changed = True
    return changed


def main():
    jobs = gist_writer._read_gist_file("scalp_stock_jobs.json")
    if jobs is None:
        logger.error("scalp_stock_jobs.json Gist 읽기 실패")
        return
    jobs = jobs if isinstance(jobs, list) else []

    if not kis_api.is_any_market_open():
        if _close_stale_auto_watching(jobs):
            gist_writer._write_gist({"scalp_stock_jobs.json": jobs})
        return

    stock_enabled = _is_stock_enabled()
    auto_cfg = _load_auto_config()
    now_epoch = time.time()
    changed = False

    if _auto_discover(jobs, auto_cfg, stock_enabled, now_epoch):
        changed = True

    tickers = list({j["ticker"] for j in jobs if j.get("status") not in ("stopped", "done")})
    if not tickers:
        if changed:
            gist_writer._write_gist({"scalp_stock_jobs.json": jobs})
        return
    price_cache = _fetch_prices(tickers)

    holding_count = sum(1 for j in jobs if j.get("phase") == "holding" and j.get("status") != "stopped")

    for job in jobs:
        if job.get("status") == "stopped":
            continue

        ticker = job["ticker"]
        name   = job.get("name", ticker)
        info   = price_cache.get(ticker)
        if not info:
            continue
        cur_price = int(info["stck_prpr"])
        today_chg = float(info.get("prdy_ctrt", 0))

        if _reset_daily_stats_if_needed(job):
            changed = True

        scalp_engine.record_price(ticker, cur_price, now=now_epoch)

        if not stock_enabled:
            if job.get("phase") == "holding":
                _force_close(job, cur_price, "전체정지 킬스위치")
                changed = True
            continue

        max_loss = float(job.get("max_daily_loss_krw", -20000))
        if job.get("realized_pnl_today", 0) <= max_loss and job.get("phase") != "holding":
            if job.get("status") != "stopped":
                job["status"] = "stopped"
                job["stop_reason"] = f"일일 손실 한도 도달 ({job['realized_pnl_today']:+,.0f}원)"
                changed = True
                logger.warning("⛔ %s 일일 손실 한도 도달 — 잡 정지", name)
            continue

        if job.get("status") != "active":
            continue

        # ── holding: 청산 판단 ──────────────────────────────────
        if job.get("phase") == "holding":
            buy_price  = float(job.get("buy_price", 0))
            entered_at = float(job.get("entered_at", 0))
            net_entry  = _net_buy_cost(buy_price)
            net_cur    = _net_sell_value(cur_price)
            should, reason = scalp_engine.should_exit(net_entry, net_cur, entered_at, now_epoch, job)
            if should:
                qty = int(job.get("buy_qty", 0))
                try:
                    result = kis_api.place_order(ticker, "SELL", qty, order_type="market")
                    pnl = (net_cur - net_entry) * qty
                    pnl_pct = (net_cur - net_entry) / net_entry * 100 if net_entry > 0 else 0
                    job["realized_pnl_today"] = job.get("realized_pnl_today", 0) + pnl
                    job["trades_today"] = job.get("trades_today", 0) + 1
                    job["phase"] = "watching"
                    job["buy_price"] = 0
                    job["buy_qty"] = 0
                    job["entered_at"] = 0
                    if job.get("source") == "auto":
                        job["status"] = "done"  # 자동발굴 잡은 1회성 — 청산 후 슬롯 반환
                    changed = True
                    gist_writer.log_trade(ticker, name, "sell", cur_price, qty, pnl=pnl,
                                           pnl_pct=pnl_pct, reason=reason, order_no=result.get("order_no", ""))
                    logger.info("★ [%s] %s %d주 @ %d원  손익 %+,.0f원(%.2f%%)",
                                reason, ticker, qty, cur_price, pnl, pnl_pct)
                except Exception as e:
                    logger.error("%s 청산 실패: %s", ticker, e)
            else:
                logger.info("  %s(%s) 보유중 @ %d원 (진입 %d원)", name, ticker, cur_price, int(buy_price))
            continue

        # ── watching: 진입 판단 ─────────────────────────────────
        if job.get("source") == "auto":
            timeout = float(job.get("watch_timeout_sec", 300))
            if scalp_engine.should_give_up_watching(job.get("discovered_at", 0), now_epoch, timeout):
                job["status"] = "done"
                job["stop_reason"] = f"{int(timeout)}초 내 진입 조건 미충족 — 포기"
                changed = True
                logger.info("⌛ %s(%s) 관찰 시간초과 — 포기, 슬롯 반환", name, ticker)
                continue

        if holding_count >= MAX_CONCURRENT_POSITIONS:
            continue

        lookback = float(job.get("lookback_sec", 30))
        momentum = scalp_engine.momentum_pct(ticker, lookback, now=now_epoch)
        should, reason = scalp_engine.should_enter(momentum, today_chg, job)
        if not should:
            logger.info("  %s(%s) 대기 — %s", name, ticker, reason)
            continue

        qty = int(job.get("qty", 0))
        amount = float(job.get("amount", 0))
        if qty <= 0 and amount > 0:
            qty = int(amount // cur_price)
        if qty < 1:
            logger.warning("%s(%s) 수량/금액 미설정 — 건너뜀", name, ticker)
            continue

        logger.info("★ [진입] %s(%s) @ %d원 — %s", name, ticker, cur_price, reason)
        try:
            result = kis_api.place_order(ticker, "BUY", qty, order_type="market")
            job["phase"]      = "holding"
            job["buy_price"]  = cur_price
            job["buy_qty"]    = qty
            job["entered_at"] = now_epoch
            changed = True
            holding_count += 1
            gist_writer.log_trade(ticker, name, "buy", cur_price, qty, order_no=result.get("order_no", ""))
            logger.info("매수 체결 %s %d주 @ %d원", ticker, qty, cur_price)
        except Exception as e:
            logger.error("%s 진입 실패: %s", ticker, e)

    jobs, pruned = scalp_engine.prune_stale_auto_jobs(jobs, _today_str())
    if pruned:
        changed = True
        logger.info("지난 완료 자동발굴 잡 %d건 정리", pruned)

    if changed:
        gist_writer._write_gist({"scalp_stock_jobs.json": jobs})
    gist_writer.flush_trades()


if __name__ == "__main__":
    main()
