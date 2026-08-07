"""
코인 초단타(스캘핑) 잡 — daemon_scalp.py 에서 5초 주기로 실행
Gist scalp_coin_jobs.json 에서 잡 읽기 → 모멘텀 진입/청산 판단 → Upbit 시장가 주문 → 상태 갱신
Gist scalp_control.json 의 coin_enabled=false 이면 신규 진입 중단 + 보유 포지션 즉시 청산 (전체 정지 킬스위치)

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
    if not jobs:
        return

    coin_enabled = _is_coin_enabled()
    now_epoch = time.time()
    changed = False

    tickers = list({j["ticker"] for j in jobs if j.get("status") != "stopped"})
    if not tickers:
        return
    try:
        price_cache = upbit_api.get_prices(tickers)
    except Exception as e:
        logger.error("현재가 일괄 조회 실패: %s", e)
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
            coin_qty = math.floor(krw_amount / cur_price * (1 - BUY_FEE) * 1e8) / 1e8
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

    if changed:
        gist_writer._write_gist({"scalp_coin_jobs.json": jobs})
    gist_writer.flush_trades()


if __name__ == "__main__":
    main()
