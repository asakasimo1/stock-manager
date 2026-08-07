"""
코인 초단타(스캘핑) 잡 — daemon_scalp.py 에서 5초 주기로 실행
Gist scalp_coin_jobs.json 에서 잡 읽기 → 모멘텀 진입/청산 판단 → Upbit 시장가 주문 → 상태 갱신
Gist scalp_control.json 의 coin_enabled=false 이면 신규 진입 중단 + 보유 포지션 즉시 청산 (전체 정지 킬스위치)
Gist scalp_auto_config.json 의 coin.enabled=true 이면 전체 KRW 마켓을 스캔해 급등 후보를 watching 잡으로 자동 생성
  (source="auto" 잡은 1회 진입/청산 후 status=done 으로 종료 — 슬롯을 비우고 다음 사이클에 새 후보 재탐색)

잡 스키마 (scalp_coin_jobs.json, 리스트):
  status  : active | paused | stopped
  phase   : watching | holding
  entry_momentum_pct, lookback_sec, max_day_chg_pct  — 진입 조건
  take_profit_pct, stop_loss_pct, time_stop_sec       — 청산 조건
  krw_amount           — 1회 진입 금액
  max_daily_loss_krw   — 당일 실현손실 한도(음수) 도달 시 자동 stopped
  buy_price/buy_qty/entered_at/buy_uuid — holding 상태 필드
  trades_today/realized_pnl_today/stats_date — 당일 통계 (자정 리셋)
"""
import logging
import math
import time
from datetime import datetime, timezone, timedelta

import upbit_api
import gist_writer
import scalp_engine

KST = timezone(timedelta(hours=9))
logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("scalp_coin_cloud.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s KST %(levelname)s %(message)s"))
logger.addHandler(_fh)

BUY_FEE  = upbit_api.BUY_FEE
SELL_FEE = upbit_api.SELL_FEE

MAX_CONCURRENT_POSITIONS = 3  # 코인 스캘핑 동시 보유 종목 상한 (하드 리밋)

DISCOVERY_INTERVAL_SEC = 30  # 자동발굴 스캔 주기 하한 — 5초마다 전체 마켓 스캔·Gist 쓰기하면
                              # GitHub Gist API 쓰기 레이트리밋(secondary rate limit)에 걸림
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


def _is_coin_enabled() -> bool:
    ctrl = gist_writer._read_gist_file("scalp_control.json")
    if not isinstance(ctrl, dict):
        return True  # 컨트롤 파일 없음 → 기본 허용
    return ctrl.get("coin_enabled", True)


def _load_auto_config() -> dict:
    cfg = gist_writer._read_gist_file("scalp_auto_config.json")
    if not isinstance(cfg, dict):
        return {}
    return cfg.get("coin") or {}


def _auto_discover(jobs: list, auto_cfg: dict, price_cache: dict, coin_enabled: bool, now_epoch: float) -> bool:
    """scalp_auto_config.json의 coin 설정에 따라 급등 후보를 찾아 watching 잡을 자동 생성.
    반환: jobs 리스트가 변경되었는지 여부 (호출부에서 append 된 내용을 그대로 사용)"""
    if not auto_cfg.get("enabled") or not coin_enabled:
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

    candidates = [
        {
            "ticker": ticker,
            "name": upbit_api.COIN_NAMES.get(ticker, ticker),
            "chg_pct": info.get("chg_pct", 0),
            "liquidity": info.get("volume", 0) * info.get("price", 0),
        }
        for ticker, info in price_cache.items()
    ]
    candidates.sort(key=lambda c: c["chg_pct"], reverse=True)

    # 진행중인 티커 + 오늘 이미 한 번 시도한(성공/실패 무관) 자동발굴 티커는 재시도하지 않음
    # (당일 재시도 없이 하루 1회 — 같은 코인에 반복 진입해 수수료만 깎아먹는 것 방지)
    existing_tickers = {
        j["ticker"] for j in jobs
        if j.get("status") not in ("done", "stopped")
        or (j.get("source") == "auto" and j.get("stats_date") == today)
    }
    max_day_chg = float(auto_cfg.get("max_day_chg_pct", 5.0))
    min_liquidity = float(auto_cfg.get("min_liquidity", 50_000_000))
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
            "krw_amount":         auto_cfg.get("krw_amount", 0),
            "max_daily_loss_krw": max_loss,
            "watch_timeout_sec":  auto_cfg.get("watch_timeout_sec", 300),
            "discovered_at":      now_epoch,
            "buy_price": 0, "buy_qty": 0, "entered_at": 0, "buy_uuid": "",
            "trades_today": 0, "realized_pnl_today": 0, "stats_date": today,
            "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        })
        logger.info("🔍 [자동포착] %s(%s) 등락률 %+.2f%% — watching 잡 생성", c["name"], c["ticker"], c["chg_pct"])

    return bool(picked)


def _poll_executed_volume(order_uuid: str, fallback: float, tries: int = 6, delay: float = 0.5) -> float:
    """시장가 매수 직후 실제 체결수량을 조회. 추정치(fallback)와 실제 체결량이 달라 매도 시
    잔량(dust)이 남는 문제를 막기 위함 — 반드시 Upbit이 확정한 executed_volume을 사용."""
    if not order_uuid:
        return fallback
    for _ in range(tries):
        try:
            order = upbit_api.get_order(order_uuid)
            if order["state"] == "done" and order["executed_volume"] > 0:
                return order["executed_volume"]
        except Exception as e:
            logger.warning("주문 체결 조회 실패 %s: %s", order_uuid, e)
            break
        time.sleep(delay)
    logger.warning("주문 %s 체결수량 확인 실패 — 추정치(%.8f) 사용", order_uuid, fallback)
    return fallback


def _force_close(job: dict, cur_price: float, reason: str) -> None:
    ticker = job["ticker"]
    name   = job.get("name", ticker)
    qty    = float(job.get("buy_qty", 0))
    if qty <= 0:
        job["phase"] = "watching"
        return
    try:
        result = upbit_api.place_order(market=ticker, side="ask", ord_type="market", volume=qty)
        buy_price = float(job.get("buy_price", 0))
        pnl = (_net_sell_value(cur_price) - _net_buy_cost(buy_price)) * qty
        job["realized_pnl_today"] = job.get("realized_pnl_today", 0) + pnl
        job["trades_today"] = job.get("trades_today", 0) + 1
        job["phase"] = "watching"
        job["buy_price"] = 0
        job["buy_qty"] = 0
        job["entered_at"] = 0
        job["buy_uuid"] = ""
        if job.get("source") == "auto":
            job["status"] = "done"  # 자동발굴 잡은 1회성 — 청산 후 슬롯 반환
        gist_writer.log_trade(ticker, name, "sell", cur_price, qty, pnl=pnl,
                               pnl_pct=(pnl / (buy_price * qty) * 100) if buy_price > 0 else None,
                               reason=reason, order_no=result.get("uuid", ""))
        logger.info("★ [%s] 강제청산 %s %.8f개 @ %s원  손익 %+,.0f원  UUID:%s",
                    reason, ticker, qty, f"{cur_price:,.0f}", pnl, result.get("uuid", ""))
    except Exception as e:
        logger.error("%s 강제청산 실패: %s", ticker, e)


def main():
    jobs = gist_writer._read_gist_file("scalp_coin_jobs.json")
    if jobs is None:
        logger.error("scalp_coin_jobs.json Gist 읽기 실패")
        return
    jobs = jobs if isinstance(jobs, list) else []

    coin_enabled = _is_coin_enabled()
    auto_cfg = _load_auto_config()
    now_epoch = time.time()
    changed = False

    # 자동발굴은 30초 주기로만 스캔 (전체 마켓 조회 + Gist 쓰기를 5초마다 하면 GitHub 쓰기 레이트리밋에 걸림)
    # 이미 watching/holding 중인 티커는 이 주기와 무관하게 매 사이클(5초) 그대로 처리됨
    run_discovery = bool(auto_cfg.get("enabled")) and coin_enabled and _should_run_discovery(now_epoch)

    if run_discovery:
        try:
            tickers = upbit_api.get_all_krw_markets()
        except Exception as e:
            logger.error("전체 마켓 조회 실패: %s", e)
            tickers = list({j["ticker"] for j in jobs if j.get("status") not in ("stopped", "done")})
    else:
        tickers = list({j["ticker"] for j in jobs if j.get("status") not in ("stopped", "done")})

    if not tickers:
        return
    try:
        price_cache = upbit_api.get_prices(tickers)
    except Exception as e:
        logger.error("현재가 일괄 조회 실패: %s", e)
        return

    if run_discovery and _auto_discover(jobs, auto_cfg, price_cache, coin_enabled, now_epoch):
        changed = True

    if not jobs:
        gist_writer.flush_trades()
        return

    holding_count = sum(1 for j in jobs if j.get("phase") == "holding" and j.get("status") != "stopped")

    for job in jobs:
        if job.get("status") == "stopped":
            continue

        ticker = job["ticker"]
        name   = job.get("name", ticker)
        info   = price_cache.get(ticker)
        if not info:
            continue
        cur_price = info["price"]
        today_chg = info.get("chg_pct")

        if _reset_daily_stats_if_needed(job):
            changed = True

        scalp_engine.record_price(ticker, cur_price, now=now_epoch)

        # ── 전체 정지 킬스위치: 신규진입 중단 + 보유 포지션 즉시 청산 ──
        if not coin_enabled:
            if job.get("phase") == "holding":
                _force_close(job, cur_price, "전체정지 킬스위치")
                changed = True
            continue

        # ── 일일 손실 한도 체크 (holding 중이 아니면 즉시 정지) ──
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
                qty = float(job.get("buy_qty", 0))
                try:
                    result = upbit_api.place_order(market=ticker, side="ask", ord_type="market", volume=qty)
                    pnl = (net_cur - net_entry) * qty
                    pnl_pct = (net_cur - net_entry) / net_entry * 100 if net_entry > 0 else 0
                    job["realized_pnl_today"] = job.get("realized_pnl_today", 0) + pnl
                    job["trades_today"] = job.get("trades_today", 0) + 1
                    job["phase"] = "watching"
                    job["buy_price"] = 0
                    job["buy_qty"] = 0
                    job["entered_at"] = 0
                    job["buy_uuid"] = ""
                    if job.get("source") == "auto":
                        job["status"] = "done"  # 자동발굴 잡은 1회성 — 청산 후 슬롯 반환
                    changed = True
                    gist_writer.log_trade(ticker, name, "sell", cur_price, qty, pnl=pnl,
                                           pnl_pct=pnl_pct, reason=reason, order_no=result.get("uuid", ""))
                    logger.info("★ [%s] %s %.8f개 @ %s원  손익 %+,.0f원(%.2f%%)  UUID:%s",
                                reason, ticker, qty, f"{cur_price:,.0f}", pnl, pnl_pct, result.get("uuid", ""))
                except Exception as e:
                    logger.error("%s 청산 실패: %s", ticker, e)
            else:
                logger.info("  %s(%s) 보유중 @ %s원 (진입 %s원)", name, ticker,
                            f"{cur_price:,.0f}", f"{buy_price:,.0f}")
            continue

        # ── watching: 진입 판단 ─────────────────────────────────
        # 자동발굴 잡이 오래 지켜봐도 진입 조건을 못 채우면 포기하고 슬롯 반환
        # (안 그러면 조용한 코인 2개가 max_concurrent 슬롯을 계속 차지해 새 후보를 못 찾음)
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

        krw_amount = float(job.get("krw_amount", 0))
        if krw_amount <= 0:
            logger.warning("%s(%s) krw_amount 미설정 — 건너뜀", name, ticker)
            continue

        logger.info("★ [진입] %s(%s) @ %s원 — %s", name, ticker, f"{cur_price:,.0f}", reason)
        try:
            result = upbit_api.place_order(market=ticker, side="bid", ord_type="price", price=krw_amount)
            estimated_qty = math.floor(krw_amount / cur_price * (1 - BUY_FEE) * 1e8) / 1e8
            coin_qty = _poll_executed_volume(result.get("uuid", ""), fallback=estimated_qty)
            job["phase"]      = "holding"
            job["buy_price"]  = cur_price
            job["buy_qty"]    = coin_qty
            job["entered_at"] = now_epoch
            job["buy_uuid"]   = result.get("uuid", "")
            changed = True
            holding_count += 1
            gist_writer.log_trade(ticker, name, "buy", cur_price, coin_qty, order_no=result.get("uuid", ""))
            logger.info("매수 체결 %s %.8f개 @ %s원", ticker, coin_qty, f"{cur_price:,.0f}")
        except Exception as e:
            logger.error("%s 진입 실패: %s", ticker, e)

    jobs, pruned = scalp_engine.prune_stale_auto_jobs(jobs, _today_str())
    if pruned:
        changed = True
        logger.info("지난 완료 자동발굴 잡 %d건 정리", pruned)

    if changed:
        gist_writer._write_gist({"scalp_coin_jobs.json": jobs})
    gist_writer.flush_trades()


if __name__ == "__main__":
    main()
