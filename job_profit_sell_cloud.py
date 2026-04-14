"""
수익매도 클라우드 잡 — GitHub Actions에서 5분마다 실행
Gist profit_sell_jobs.json 에서 활성 잡 읽기 → 목표 달성 시 즉시 매도 → 상태 업데이트
"""
import logging
from datetime import datetime, timezone, timedelta

import kis_api
import gist_writer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST           = timezone(timedelta(hours=9))
BUY_FEE_RATE  = 0.00015            # 매수 수수료 0.015%
SELL_FEE_RATE = 0.00015 + 0.0018   # 매도 수수료 + 증권거래세 0.195%


def calc_target_price(buy_price: int, qty: int,
                      target_type: str, target_value: float) -> int:
    """목표 매도단가 계산 (수수료 포함)"""
    if target_type == "amount":
        break_even = buy_price * (1 + BUY_FEE_RATE)
        needed_per_share = (break_even * qty + target_value) / qty
        return int(needed_per_share / (1 - SELL_FEE_RATE)) + 1
    else:  # pct
        target_sell = buy_price * (1 + target_value / 100)
        return int(target_sell / (1 - SELL_FEE_RATE)) + 1


def main():
    logger.info("수익매도 체크 시작")

    jobs = gist_writer._read_gist_file("profit_sell_jobs.json") or []
    active = [j for j in jobs if j.get("status") == "active"]

    if not active:
        logger.info("활성 잡 없음 — 종료")
        return

    logger.info("활성 잡 %d개 처리", len(active))
    changed = False

    for job in jobs:
        if job.get("status") != "active":
            continue

        ticker       = job["ticker"]
        name         = job.get("name", ticker)
        buy_price    = int(job["buy_price"])
        qty          = int(job["qty"])
        target_type  = job["target_type"]   # "amount" | "pct"
        target_value = float(job["target_value"])
        target_price = calc_target_price(buy_price, qty, target_type, target_value)

        try:
            info = kis_api.get_price(ticker)
            cur_price = int(info["stck_prpr"])

            if target_type == "amount":
                net_pnl = cur_price * qty * (1 - SELL_FEE_RATE) - buy_price * qty * (1 + BUY_FEE_RATE)
                label = f"{net_pnl:+.0f}원 / 목표 {target_value:+.0f}원"
            else:
                net_pct = (cur_price * (1 - SELL_FEE_RATE) / buy_price - 1) * 100
                label = f"{net_pct:+.2f}% / 목표 {target_value:+.2f}%"

            logger.info("%s(%s) 현재가 %d원  %s  [목표단가 %d원]",
                        name, ticker, cur_price, label, target_price)

            if cur_price >= target_price:
                logger.info("★ 목표 달성! 매도 실행 — %s %d주 @ %d원", ticker, qty, cur_price)
                result = kis_api.place_order(ticker, "SELL", qty, order_type="market")
                job["status"]      = "done"
                job["sell_price"]  = cur_price
                job["executed_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                job["order_no"]    = result.get("order_no", "")
                changed = True
                logger.info("매도 주문 완료  주문번호: %s", job["order_no"])

        except Exception as e:
            logger.error("%s(%s) 처리 실패: %s", name, ticker, e)

    if changed:
        ok = gist_writer._write_gist({"profit_sell_jobs.json": jobs})
        logger.info("Gist 업데이트 %s", "완료" if ok else "실패")

    logger.info("수익매도 체크 완료")


if __name__ == "__main__":
    main()
