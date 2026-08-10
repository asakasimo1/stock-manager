"""
코인 매도 잡 — GitHub Actions에서 5분마다 24/7 실행

1) 보유 코인 자동매도 규칙 (매 5분 체크)
   - 수익률 +20% 이상 → 전량 즉시 매도 (익절)
   - 수익률 -4%  이하 → 전량 즉시 매도 (손절)

2) coin_sell_jobs.json 잡 처리
   - 목표 수익률/금액/가격 달성 시 매도
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
            err = str(e)
            if "insufficient_funds_ask" in err:
                # 코인이 미체결 지정가 주문에 잠겨있는 경우 — 매도 주문 취소 후 재시도 필요
                logger.warning("%s 자동매도 건너뜀 — 코인이 미체결 주문에 잠겨있음 (Upbit 미체결 주문 확인 필요)", ticker)
            else:
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
            err = str(e)
            if "insufficient_funds" in err or "volume" in err.lower():
                job["status"]       = "cancelled"
                job["cancelled_at"] = now_kst()
                changed = True
                logger.warning("%s 잔고 부족 → 자동 취소 (이미 다른 잡에서 매도됨)", ticker)
            else:
                logger.error("%s 매도 실패: %s", ticker, e)

    if changed:
        gist_writer._write_gist({"coin_sell_jobs.json": jobs})


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────
def main():
    logger.info("코인 매도 잡 체크 시작")

    auto_sell_by_rule()
    process_sell_jobs()

    logger.info("코인 매도 잡 체크 완료")


if __name__ == "__main__":
    main()
