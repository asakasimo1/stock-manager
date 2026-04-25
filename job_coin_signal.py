"""
코인 시그널 매수/매도 잡 — 시장 조건 감지 기반 자동 실행

시그널 타입:
  btc_pump_lag   : BTC 24h 상승률 >= X% 이면서 대상 코인 24h 상승률 < Y% → 시장가 매수
  dip_buy        : 대상 코인 24h 고점 대비 X% 이상 하락 → 반등 매수
  price_breakout : 대상 코인 현재가 >= 지정 돌파 가격 → 즉시 매수
  btc_dump_sell  : BTC 24h 하락률 <= -X% → 대상 코인 강제 시장가 매도

매수 시그널 실행 후:
  - 시장가 매수 주문 실행
  - coin_cycle_jobs.json 에 holding 상태 사이클 잡 자동 생성 (이후 sell/rebuy 자동 사이클)

설정 파일 (Gist): coin_signal_jobs.json
"""
import logging
import math
import uuid
from datetime import datetime, timezone, timedelta

import upbit_api
import gist_writer

KST = timezone(timedelta(hours=9))
BUY_FEE  = upbit_api.BUY_FEE
SELL_FEE = upbit_api.SELL_FEE

logger = logging.getLogger(__name__)


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def calc_sell_price(buy_price: float, take_pct: float) -> float:
    cost = buy_price * (1 + BUY_FEE)
    return math.ceil(cost * (1 + take_pct / 100) / (1 - SELL_FEE))


def _get_extended_prices(markets: list) -> dict:
    """현재가 + 24h 고가 포함 일괄 조회 (단일 API 요청)"""
    r = upbit_api._session.get(
        f"{upbit_api.BASE_URL}/ticker",
        params={"markets": ",".join(markets)},
        timeout=10,
    )
    if not r.ok:
        return {}
    result = {}
    for d in r.json():
        result[d["market"]] = {
            "price":   d["trade_price"],
            "chg_pct": round(d["signed_change_rate"] * 100, 2),
            "high":    d["high_price"],
            "volume":  d["acc_trade_volume_24h"],
        }
    return result


def _cooldown_ok(job: dict) -> bool:
    last = job.get("last_triggered")
    if not last:
        return True
    cooldown_min = int(job.get("cooldown_min", 60))
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=KST)
        elapsed = (datetime.now(KST) - last_dt).total_seconds() / 60
        return elapsed >= cooldown_min
    except Exception:
        return True


# ─────────────────────────────────────────
# 시그널 조건 체커
# ─────────────────────────────────────────

def _check_btc_pump_lag(job: dict, prices: dict) -> tuple:
    """BTC 24h 급등 + 대상 코인 24h 지연"""
    params = job.get("params", {})
    btc_rise = float(params.get("btc_rise_pct", 2.0))
    coin_max  = float(params.get("coin_max_pct", 1.0))

    btc_chg  = prices.get("KRW-BTC", {}).get("chg_pct", 0)
    coin_chg = prices.get(job["ticker"], {}).get("chg_pct", 0)

    if btc_chg >= btc_rise and coin_chg < coin_max:
        return True, f"BTC {btc_chg:+.2f}% ≥ +{btc_rise}% / {job['ticker']} {coin_chg:+.2f}% < +{coin_max}%"
    return False, f"BTC {btc_chg:+.2f}% / {job['ticker']} {coin_chg:+.2f}%"


def _check_dip_buy(job: dict, prices: dict) -> tuple:
    """24h 고점 대비 급락 반등"""
    params = job.get("params", {})
    dip_pct = float(params.get("dip_pct", 5.0))

    d = prices.get(job["ticker"], {})
    high = d.get("high", 0)
    cur  = d.get("price", 0)
    if high <= 0 or cur <= 0:
        return False, "가격 정보 없음"

    drop = (high - cur) / high * 100
    if drop >= dip_pct:
        return True, f"24h 고점({high:,.0f}원) 대비 -{drop:.2f}% 하락 (기준 -{dip_pct}%)"
    return False, f"낙폭 -{drop:.2f}% (기준 -{dip_pct}%)"


def _check_price_breakout(job: dict, prices: dict) -> tuple:
    """지정 저항선 돌파"""
    params = job.get("params", {})
    breakout_price = float(params.get("breakout_price", 0))
    if breakout_price <= 0:
        return False, "돌파 기준가 미설정"

    cur = prices.get(job["ticker"], {}).get("price", 0)
    if cur >= breakout_price:
        return True, f"현재가 {cur:,.0f}원 ≥ 돌파 기준가 {breakout_price:,.0f}원"
    return False, f"현재가 {cur:,.0f}원 / 기준가 {breakout_price:,.0f}원"


def _check_btc_dump_sell(job: dict, prices: dict) -> tuple:
    """BTC 급락 → 강제 매도"""
    params = job.get("params", {})
    btc_drop = float(params.get("btc_drop_pct", 3.0))

    btc_chg = prices.get("KRW-BTC", {}).get("chg_pct", 0)
    if btc_chg <= -btc_drop:
        return True, f"BTC {btc_chg:+.2f}% ≤ -{btc_drop}% 급락"
    return False, f"BTC {btc_chg:+.2f}% (기준 -{btc_drop}%)"


SIGNAL_CHECKERS = {
    "btc_pump_lag":   _check_btc_pump_lag,
    "dip_buy":        _check_dip_buy,
    "price_breakout": _check_price_breakout,
    "btc_dump_sell":  _check_btc_dump_sell,
}


# ─────────────────────────────────────────
# 실행: 매수 / 강제 매도
# ─────────────────────────────────────────

def _do_buy(job: dict, cur_price: float, cycle_jobs: list) -> bool:
    """시장가 매수 + 사이클 잡 자동 생성"""
    ticker     = job["ticker"]
    name       = job.get("name", ticker)
    krw_amount = float(job.get("krw_amount", 0))

    if krw_amount <= 0:
        logger.warning("  %s(%s) krw_amount 미설정 — 매수 건너뜀", name, ticker)
        return False

    try:
        result   = upbit_api.place_order(market=ticker, side="bid", ord_type="price", price=krw_amount)
        coin_qty = math.floor(krw_amount / cur_price * (1 - BUY_FEE) * 1e8) / 1e8
        take_pct = float(job.get("take_pct", 2.0))
        sell_price_target = calc_sell_price(cur_price, take_pct)

        new_cycle = {
            "id":             str(uuid.uuid4())[:8],
            "name":           name,
            "ticker":         ticker,
            "status":         "active",
            "phase":          "holding",
            "condition_type": "market_krw",
            "krw_amount":     krw_amount,
            "buy_price":      cur_price,
            "hold_qty":       coin_qty,
            "sell_price":     sell_price_target,
            "take_pct":       take_pct,
            "rebuy_drop":     float(job.get("rebuy_drop", 2.0)),
            "repeat_take":    float(job.get("repeat_take", 2.0)),
            "max_cycles":     int(job.get("max_cycles", 0)),
            "cycle_count":    0,
            "buy_uuid":       result.get("uuid", ""),
            "bought_at":      now_kst(),
            "created_at":     now_kst(),
            "signal_id":      job.get("id", ""),
        }
        cycle_jobs.append(new_cycle)
        logger.info("★ 시그널 매수 완료 %s %.8f개 @ %s원 → 목표매도 %s원",
                    ticker, coin_qty, f"{cur_price:,.0f}", f"{sell_price_target:,.0f}")
        return True
    except Exception as e:
        logger.error("%s 시그널 매수 실패: %s", ticker, e)
        return False


def _do_sell(job: dict) -> bool:
    """강제 시장가 매도 (btc_dump_sell)"""
    ticker = job["ticker"]
    name   = job.get("name", ticker)

    try:
        bal = upbit_api.get_balance()
        holding = next((h for h in bal["holdings"] if h["ticker"] == ticker), None)
        if not holding or holding["qty"] <= 0:
            logger.info("  %s 보유 없음 — 강제 매도 건너뜀", ticker)
            return False

        qty = holding["qty"]
        cur = holding["cur_price"]
        upbit_api.place_order(market=ticker, side="ask", ord_type="market", volume=qty)
        logger.info("★ 시그널 강제매도 완료 %s(%s) %.8f개 @ %s원  손익: %+.2f%%",
                    name, ticker, qty, f"{cur:,.0f}", holding["pnl_pct"])
        return True
    except Exception as e:
        logger.error("%s 시그널 강제매도 실패: %s", ticker, e)
        return False


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

def main():
    logger.info("시그널 잡 체크 시작")

    jobs = gist_writer._read_gist_file("coin_signal_jobs.json")
    if jobs is None:
        logger.error("시그널 잡 읽기 실패")
        return
    if not jobs:
        logger.info("등록된 시그널 잡 없음")
        return

    active = [j for j in jobs if j.get("status") == "watching"]
    if not active:
        logger.info("활성 시그널 잡 없음")
        return

    # 필요한 티커 수집 (BTC는 항상 포함)
    tickers = list({j["ticker"] for j in active} | {"KRW-BTC"})
    try:
        prices = _get_extended_prices(tickers)
    except Exception as e:
        logger.error("현재가 조회 실패: %s", e)
        return

    cycle_jobs = gist_writer._read_gist_file("coin_cycle_jobs.json") or []

    changed_signals = False
    changed_cycles  = False

    for job in jobs:
        if job.get("status") != "watching":
            continue

        sig_type = job.get("signal_type", "")
        checker  = SIGNAL_CHECKERS.get(sig_type)
        if not checker:
            logger.warning("  알 수 없는 signal_type: %s", sig_type)
            continue

        name   = job.get("name", job.get("ticker", "?"))
        ticker = job["ticker"]

        if not _cooldown_ok(job):
            logger.info("  %s(%s) 쿨다운 중 (%d분)", name, ticker, job.get("cooldown_min", 60))
            continue

        triggered, reason = checker(job, prices)
        logger.info("  [%s] %s(%s) %s — %s",
                    sig_type, name, ticker,
                    "✓ 시그널 발동" if triggered else "대기",
                    reason)

        if not triggered:
            continue

        is_sell_signal = sig_type == "btc_dump_sell"
        cur_price = prices.get(ticker, {}).get("price", 0)

        if is_sell_signal:
            success = _do_sell(job)
        else:
            if cur_price <= 0:
                logger.warning("  %s 현재가 조회 실패 — 매수 건너뜀", ticker)
                continue
            success = _do_buy(job, cur_price, cycle_jobs)
            if success:
                changed_cycles = True

        if success:
            job["last_triggered"] = datetime.now(KST).isoformat()
            job["trigger_count"]  = int(job.get("trigger_count", 0)) + 1
            max_triggers = int(job.get("max_triggers", 0))
            if max_triggers > 0 and job["trigger_count"] >= max_triggers:
                job["status"] = "stopped"
                logger.info("  %s 최대 트리거 %d회 달성 → 중단", name, max_triggers)
            changed_signals = True

    if changed_signals:
        gist_writer._write_gist({"coin_signal_jobs.json": jobs})
    if changed_cycles:
        gist_writer._write_gist({"coin_cycle_jobs.json": cycle_jobs})

    logger.info("시그널 잡 체크 완료")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    main()
