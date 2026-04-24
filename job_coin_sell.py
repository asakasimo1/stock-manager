"""
코인 매도·사이클 잡 — GitHub Actions에서 5분마다 24/7 실행

1) 보유 코인 자동매도 규칙 (매 5분 체크)
   - 수익률 +20% 이상 → 전량 즉시 매도 (익절)
   - 수익률 -4%  이하 → 전량 즉시 매도 (손절)

2) coin_sell_jobs.json 잡 처리
   - 목표 수익률/금액/가격 달성 시 매도

3) coin_cycle_jobs.json 사이클 처리
   사이클 흐름:
   [waiting_buy]  → 현재가 <= buy_price  → 매수 → [holding]
   [holding]      → 현재가 >= sell_price → 매도 → [waiting_rebuy]
   [waiting_rebuy]→ 현재가 <= rebuy_price→ 매수 → [holding] (반복)
"""
import logging
import math
from datetime import datetime, timezone, timedelta

import upbit_api
import gist_writer

KST = timezone(timedelta(hours=9))
logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
_fmt = logging.Formatter("%(asctime)s KST %(levelname)s %(message)s")
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("coin_sell_cloud.log", encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)

BUY_FEE  = upbit_api.BUY_FEE   # 0.05%
SELL_FEE = upbit_api.SELL_FEE  # 0.05%

AUTO_PROFIT_PCT = 20.0   # 자동 익절 기준 (%)
AUTO_LOSS_PCT   = -4.0   # 자동 손절 기준 (%)


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def calc_net_pnl_pct(buy_price: float, cur_price: float) -> float:
    """수수료 포함 순손익률 계산"""
    cost     = buy_price * (1 + BUY_FEE)
    net_sell = cur_price * (1 - SELL_FEE)
    return (net_sell - cost) / cost * 100


def calc_sell_price(buy_price: float, take_pct: float) -> float:
    """take_pct 순이익률 달성을 위한 최소 매도 단가 (원 단위 올림)"""
    cost_per = buy_price * (1 + BUY_FEE)
    target_net_per = cost_per * (1 + take_pct / 100)
    return math.ceil(target_net_per / (1 - SELL_FEE))


# ─────────────────────────────────────────
# 1) 자동매도 규칙 (+20% / -4%)
# ─────────────────────────────────────────
def auto_sell_by_rule():
    try:
        bal = upbit_api.get_balance()
    except Exception as e:
        logger.error("잔고 조회 실패: %s", e)
        return

    for h in bal["holdings"]:
        pnl_pct = h["pnl_pct"]
        if pnl_pct >= AUTO_PROFIT_PCT:
            reason = f"익절 (+{AUTO_PROFIT_PCT:.0f}% 달성)"
            emoji  = "🚀"
        elif pnl_pct <= AUTO_LOSS_PCT:
            reason = f"손절 ({AUTO_LOSS_PCT:.0f}% 도달)"
            emoji  = "🔴"
        else:
            continue

        ticker = h["ticker"]
        name   = h["name"]
        qty    = h["qty"]

        logger.info("★ 자동매도 [%s] — %s(%s) 수익률 %.2f%%", reason, name, ticker, pnl_pct)
        try:
            result = upbit_api.place_order(
                market=ticker, side="ask", ord_type="market", volume=qty
            )
            pnl = h["pnl"]
            logger.info(
                "%s 자동매도 완료 %s %.8f개 @ %s원  손익: %s원  UUID: %s",
                emoji, ticker, qty, f"{h['cur_price']:,.0f}",
                f"{pnl:+,.0f}", result.get("uuid", "")
            )
        except Exception as e:
            logger.error("%s 자동매도 실패: %s", ticker, e)


# ─────────────────────────────────────────
# 2) 매도 잡 처리 (coin_sell_jobs.json)
# ─────────────────────────────────────────
def process_sell_jobs():
    jobs = gist_writer._read_gist_file("coin_sell_jobs.json") or []
    active = [j for j in jobs if j.get("status") in ("active", "submitted")]
    if not active:
        logger.info("활성 매도 잡 없음")
        return

    tickers = list({j["ticker"] for j in active})
    try:
        prices = upbit_api.get_prices(tickers)
    except Exception as e:
        logger.error("현재가 조회 실패: %s", e)
        return

    changed = False
    for job in jobs:
        if job.get("status") not in ("active", "submitted"):
            continue

        ticker    = job["ticker"]
        name      = job.get("name", ticker)
        cur_price = prices.get(ticker, {}).get("price")
        if not cur_price:
            continue

        buy_price = float(job.get("buy_price", 0))
        qty       = float(job.get("qty", 0))
        target_type  = job.get("target_type", "pct")   # pct | amount | price
        target_value = float(job.get("target_value", 0))

        if target_type == "price":
            target_sell_price = target_value
        elif target_type == "amount":
            if buy_price > 0 and qty > 0:
                needed = (buy_price * qty * (1 + BUY_FEE) + target_value) / qty
                target_sell_price = needed / (1 - SELL_FEE)
            else:
                continue
        else:  # pct
            target_sell_price = calc_sell_price(buy_price, target_value) if buy_price > 0 else 0

        pnl_pct = calc_net_pnl_pct(buy_price, cur_price) if buy_price > 0 else 0.0
        logger.info("  %s(%s) 현재가 %s / 목표가 %s / 손익 %.2f%%",
                    name, ticker, f"{cur_price:,.0f}", f"{target_sell_price:,.0f}", pnl_pct)

        if cur_price < target_sell_price:
            continue

        logger.info("★ 목표가 달성 매도: %s(%s) %.8f개 @ %s원", name, ticker, qty, f"{cur_price:,.0f}")
        try:
            result = upbit_api.place_order(
                market=ticker, side="ask", ord_type="market", volume=qty
            )
            job["status"]      = "done"
            job["executed_at"] = now_kst()
            job["order_uuid"]  = result.get("uuid", "")
            job["exec_price"]  = cur_price
            job["pnl_pct"]     = round(pnl_pct, 2)
            changed = True
        except Exception as e:
            logger.error("%s 매도 실패: %s", ticker, e)

    if changed:
        gist_writer._write_gist({"coin_sell_jobs.json": jobs})


# ─────────────────────────────────────────
# 3) 사이클 잡 처리 (coin_cycle_jobs.json)
# ─────────────────────────────────────────
def process_cycle_jobs():
    jobs = gist_writer._read_gist_file("coin_cycle_jobs.json") or []
    active = [j for j in jobs if j.get("status") not in ("done", "cancelled", "stopped")]
    if not active:
        logger.info("활성 사이클 잡 없음")
        return

    tickers = list({j["ticker"] for j in active})
    try:
        prices = upbit_api.get_prices(tickers)
    except Exception as e:
        logger.error("현재가 조회 실패: %s", e)
        return

    # 실제 보유 잔고 조회 (매도 수량 보정용)
    actual_qty: dict = {}
    try:
        bal = upbit_api.get_balance()
        actual_qty = {h["ticker"]: h["qty"] for h in bal.get("holdings", [])}
    except Exception as e:
        logger.warning("잔고 조회 실패 (수량 보정 불가): %s", e)

    changed = False
    for job in jobs:
        if job.get("status") in ("done", "cancelled", "stopped"):
            continue

        ticker    = job["ticker"]
        name      = job.get("name", ticker)
        phase     = job.get("phase", "waiting_buy")
        cur_price = prices.get(ticker, {}).get("price")
        if not cur_price:
            continue

        take_pct     = float(job.get("take_pct",     3.0))
        rebuy_drop   = float(job.get("rebuy_drop",   2.0))
        repeat_take  = float(job.get("repeat_take",  2.0))
        max_cycles   = int(job.get("max_cycles", 0))
        cycle_count  = int(job.get("cycle_count", 0))
        buy_price    = float(job.get("buy_price", 0))
        cond_type    = job.get("condition_type", "market_krw")
        buy_target   = float(job.get("buy_target_price", 0))  # 최초 지정가 조건

        # ── [waiting_buy] 단계 ──────────────────────────────────
        if phase == "waiting_buy":
            if cond_type == "limit":
                buy_order_uuid = job.get("buy_order_uuid", "")
                if buy_order_uuid:
                    try:
                        order = upbit_api.get_order(buy_order_uuid)
                        state = order["state"]
                        if state == "done":
                            ev = order["executed_volume"]
                            ap = order["avg_price"] or buy_target
                            job["phase"]      = "holding"
                            job["buy_price"]  = ap
                            job["hold_qty"]   = ev
                            job["sell_price"] = calc_sell_price(ap, take_pct)
                            job["bought_at"]  = now_kst()
                            changed = True
                            logger.info("지정가 매수 체결 %s %.8f개 @ %s원 → 목표: %s원",
                                        ticker, ev, f"{ap:,.0f}", f"{job['sell_price']:,.0f}")
                        elif state == "cancel":
                            job["buy_order_uuid"] = ""
                            changed = True
                            logger.info("%s 지정가 매수 취소 — 재등록 예정", ticker)
                        else:
                            logger.info("  %s(%s) 지정가 매수 대기 @ %s원",
                                        name, ticker, f"{buy_target:,.0f}")
                    except Exception as e:
                        logger.error("%s 주문 조회 실패: %s", ticker, e)
                else:
                    if buy_target <= 0:
                        logger.warning("%s(%s) 지정가 미설정 — 건너뜀", name, ticker)
                        continue
                    krw_amount = float(job.get("krw_amount", 0))
                    coin_qty   = float(job.get("coin_qty", 0))
                    if krw_amount > 0 and coin_qty <= 0:
                        coin_qty = math.floor(krw_amount / buy_target * 1e8) / 1e8
                    if coin_qty <= 0:
                        logger.warning("%s(%s) 수량/금액 미설정 — 건너뜀", name, ticker)
                        continue
                    logger.info("★ [waiting_buy] 지정가 주문 등록: %s(%s) %.8f개 @ %s원",
                                name, ticker, coin_qty, f"{buy_target:,.0f}")
                    try:
                        result = upbit_api.place_order(
                            market=ticker, side="bid", ord_type="limit",
                            volume=coin_qty, price=buy_target
                        )
                        job["buy_order_uuid"] = result.get("uuid", "")
                        changed = True
                        logger.info("지정가 주문 등록 %s %.8f개 @ %s원  UUID: %s",
                                    ticker, coin_qty, f"{buy_target:,.0f}", result.get("uuid", ""))
                    except Exception as e:
                        logger.error("%s 지정가 매수 주문 실패: %s", ticker, e)
                continue

            # 시장가 즉시 매수
            logger.info("★ [waiting_buy] 시장가 매수 실행: %s(%s) @ %s원", name, ticker, f"{cur_price:,.0f}")
            try:
                krw_amount = float(job.get("krw_amount", 0))
                coin_qty   = float(job.get("coin_qty", 0))

                if krw_amount > 0 and coin_qty <= 0:
                    result = upbit_api.place_order(
                        market=ticker, side="bid", ord_type="price", price=krw_amount
                    )
                    coin_qty = math.floor(krw_amount / cur_price * (1 - BUY_FEE) * 1e8) / 1e8
                elif coin_qty > 0:
                    result = upbit_api.place_order(
                        market=ticker, side="bid", ord_type="price",
                        price=cur_price * coin_qty
                    )
                    coin_qty = math.floor(coin_qty * (1 - BUY_FEE) * 1e8) / 1e8
                else:
                    logger.warning("%s(%s) 수량/금액 미설정 — 건너뜀", name, ticker)
                    continue

                job["phase"]        = "holding"
                job["buy_price"]    = cur_price
                job["hold_qty"]     = coin_qty
                job["sell_price"]   = calc_sell_price(cur_price, take_pct)
                job["bought_at"]    = now_kst()
                job["buy_uuid"]     = result.get("uuid", "")
                changed = True
                logger.info("매수 완료 %s %.8f개 @ %s원 → 매도 목표: %s원",
                            ticker, coin_qty, f"{cur_price:,.0f}", f"{job['sell_price']:,.0f}")
            except Exception as e:
                logger.error("%s 사이클 매수 실패: %s", ticker, e)

        # ── [holding] 단계 ───────────────────────────────────────
        elif phase == "holding":
            sell_price = float(job.get("sell_price", 0))
            buy_price_val = float(job.get("buy_price", 0))
            hold_qty   = float(job.get("hold_qty", 0))
            net_pct    = calc_net_pnl_pct(buy_price_val or cur_price, cur_price)

            # 손익분기점 계산: 매수금액+수수료 < 매도금액 보장
            breakeven = buy_price_val * (1 + BUY_FEE) / (1 - SELL_FEE) if buy_price_val > 0 else 0
            if sell_price <= breakeven:
                # sell_price가 0이거나 손익분기점 이하이면 재계산
                if buy_price_val > 0:
                    sell_price = calc_sell_price(buy_price_val, take_pct)
                    job["sell_price"] = sell_price
                    changed = True
                    logger.info("  %s(%s) sell_price 복구: %.0f원 (매수가 %.0f 기준)", name, ticker, sell_price, buy_price_val)
                else:
                    logger.warning("  %s(%s) sell_price 미설정·매수가 없음 — 매도 건너뜀", name, ticker)
                    continue

            logger.info("  %s(%s) 보유 중 현재가 %s / 매도 목표 %s / 수익 %.2f%%",
                        name, ticker, f"{cur_price:,.0f}", f"{sell_price:,.0f}", net_pct)

            if cur_price < sell_price:
                continue

            logger.info("★ [holding] 매도 실행: %s(%s) @ %s원", name, ticker, f"{cur_price:,.0f}")
            try:
                real_qty = actual_qty.get(ticker, hold_qty)
                sell_qty = min(hold_qty, real_qty)
                if sell_qty <= 0:
                    logger.error("%s 매도 가능 수량 없음 (실제잔고: %s)", ticker, real_qty)
                    continue
                if real_qty < hold_qty:
                    logger.info("  수량 보정: Gist %.8f → 실제잔고 %.8f", hold_qty, real_qty)
                result = upbit_api.place_order(
                    market=ticker, side="ask", ord_type="market", volume=sell_qty
                )
                rebuy_price = math.floor(cur_price * (1 - rebuy_drop / 100))
                job["phase"]        = "waiting_rebuy"
                job["sell_price_exec"] = cur_price
                job["rebuy_price"]  = rebuy_price
                job["sold_at"]      = now_kst()
                job["sell_uuid"]    = result.get("uuid", "")
                cycle_count        += 1
                job["cycle_count"]  = cycle_count

                if max_cycles > 0 and cycle_count >= max_cycles:
                    job["phase"]  = "done"
                    job["status"] = "done"
                    logger.info("최대 사이클 %d회 완료 → 종료", max_cycles)
                else:
                    job["rebuy_order_uuid"] = ""
                    logger.info("매도 완료 %s @ %s원 → 재매수 목표: %s원",
                                ticker, f"{cur_price:,.0f}", f"{rebuy_price:,.0f}")
                changed = True
            except Exception as e:
                logger.error("%s 사이클 매도 실패: %s", ticker, e)

        # ── [waiting_rebuy] 단계 ────────────────────────────────
        elif phase == "waiting_rebuy":
            rebuy_price      = float(job.get("rebuy_price", 0))
            rebuy_order_uuid = job.get("rebuy_order_uuid", "")

            if rebuy_price <= 0:
                logger.warning("  %s(%s) rebuy_price 미설정 — 재매수 건너뜀", name, ticker)
                continue

            if rebuy_order_uuid:
                try:
                    order = upbit_api.get_order(rebuy_order_uuid)
                    state = order["state"]
                    if state == "done":
                        ev = order["executed_volume"]
                        ap = order["avg_price"] or rebuy_price
                        job["phase"]            = "holding"
                        job["buy_price"]        = ap
                        job["hold_qty"]         = ev
                        job["sell_price"]       = calc_sell_price(ap, repeat_take)
                        job["bought_at"]        = now_kst()
                        job["rebuy_order_uuid"] = ""
                        changed = True
                        logger.info("재매수 체결 %s %.8f개 @ %s원 → 목표: %s원",
                                    ticker, ev, f"{ap:,.0f}", f"{job['sell_price']:,.0f}")
                    elif state == "cancel":
                        job["rebuy_order_uuid"] = ""
                        changed = True
                        logger.info("%s 재매수 주문 취소 — 재등록 예정", ticker)
                    else:
                        logger.info("  %s(%s) 재매수 주문 대기 @ %s원",
                                    name, ticker, f"{rebuy_price:,.0f}")
                except Exception as e:
                    logger.error("%s 재매수 주문 조회 실패: %s", ticker, e)
            else:
                if rebuy_price <= 0:
                    logger.warning("%s(%s) 재매수가 미설정 — 건너뜀", name, ticker)
                    continue
                krw_amount = float(job.get("krw_amount", 0))
                coin_qty   = float(job.get("coin_qty", 0))
                if krw_amount > 0 and coin_qty <= 0:
                    coin_qty = math.floor(krw_amount / rebuy_price * 1e8) / 1e8
                elif coin_qty <= 0:
                    logger.warning("%s(%s) 수량/금액 미설정 — 건너뜀", name, ticker)
                    continue
                logger.info("★ [waiting_rebuy] 지정가 주문 등록: %s(%s) %.8f개 @ %s원",
                            name, ticker, coin_qty, f"{rebuy_price:,.0f}")
                try:
                    result = upbit_api.place_order(
                        market=ticker, side="bid", ord_type="limit",
                        volume=coin_qty, price=rebuy_price
                    )
                    job["rebuy_order_uuid"] = result.get("uuid", "")
                    changed = True
                    logger.info("재매수 주문 등록 %s %.8f개 @ %s원  UUID: %s",
                                ticker, coin_qty, f"{rebuy_price:,.0f}", result.get("uuid", ""))
                except Exception as e:
                    logger.error("%s 재매수 주문 실패: %s", ticker, e)

    if changed:
        gist_writer._write_gist({"coin_cycle_jobs.json": jobs})


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────
def main():
    logger.info("코인 매도·사이클 잡 체크 시작")

    auto_sell_by_rule()
    process_sell_jobs()
    process_cycle_jobs()

    logger.info("코인 매도·사이클 잡 체크 완료")


if __name__ == "__main__":
    main()
