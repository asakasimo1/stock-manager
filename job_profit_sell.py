"""
수익 목표 도달 시 즉시 매도 — 범용 모니터

사용법:
  python job_profit_sell.py --ticker 036030 --amount 50
  python job_profit_sell.py --ticker 036030 --pct 1.5
  python job_profit_sell.py --ticker 036030 --amount 50 --qty 2 --buy-price 4960

옵션:
  --ticker      종목코드 (필수)
  --amount      목표 순이익 금액 (원, 보유수량 전체 기준)
  --pct         목표 수익률 (%, 수수료 차감 전)
  --qty         수량 (미지정 시 잔고에서 자동 조회)
  --buy-price   평균매수단가 (미지정 시 잔고에서 자동 조회)
  --interval    가격 체크 간격 (초, 기본 30)
  --close-time  장 마감 포기 시각 KST HH:MM (기본 15:35)
"""

import argparse
import logging
import time
from datetime import datetime, timezone, timedelta

import kis_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("profit_sell.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# 수수료 상수
BUY_FEE_RATE  = 0.00015            # 매수 수수료 0.015%
SELL_FEE_RATE = 0.00015 + 0.0018   # 매도 수수료 + 증권거래세 = 0.195%
KST = timezone(timedelta(hours=9))


def get_holding(ticker: str) -> dict | None:
    """잔고에서 ticker 보유 정보 반환"""
    bal = kis_api.get_balance()
    for h in bal["holdings"]:
        if h["ticker"] == ticker:
            return h
    return None


def calc_target_price(buy_price: int, qty: int,
                      target_amount: float | None,
                      target_pct: float | None) -> int:
    """
    목표 매도단가 계산
    - amount 기준: (buy_price * qty + target_amount) / qty / (1 - SELL_FEE_RATE)
    - pct 기준:    buy_price * (1 + target_pct/100) / (1 - SELL_FEE_RATE)  [수수료 포함]
    """
    if target_amount is not None:
        # 전체 보유분 수익 기준
        break_even_per_share = buy_price * (1 + BUY_FEE_RATE)  # 매수비용 포함 단가
        needed_proceeds = (break_even_per_share * qty + target_amount) / qty
        return int(needed_proceeds / (1 - SELL_FEE_RATE)) + 1

    else:
        # 수익률 기준 (수수료 차감 전 목표 → 차감 후 실질 수익률은 약간 낮음)
        target_sell = buy_price * (1 + target_pct / 100)
        return int(target_sell / (1 - SELL_FEE_RATE)) + 1


def calc_net_pnl(buy_price: int, cur_price: int, qty: int) -> float:
    """수수료 차감 후 예상 순손익"""
    buy_cost    = buy_price * qty * (1 + BUY_FEE_RATE)
    sell_income = cur_price * qty * (1 - SELL_FEE_RATE)
    return sell_income - buy_cost


def parse_close_time(s: str):
    """'15:35' → (15, 35)"""
    h, m = s.split(":")
    return int(h), int(m)


def run(ticker: str, qty: int, buy_price: int,
        target_amount: float | None, target_pct: float | None,
        interval: int, close_hour: int, close_min: int):

    target_price = calc_target_price(buy_price, qty, target_amount, target_pct)
    label = f"+{target_amount:.0f}원" if target_amount is not None else f"+{target_pct:.2f}%"

    # 종목명 조회
    try:
        info = kis_api.get_price(ticker)
        name = ticker
    except Exception:
        info = {}
        name = ticker

    logger.info("=" * 55)
    logger.info("  모니터링 시작: %s(%s)", name, ticker)
    logger.info("  평균매수단가: %d원  수량: %d주", buy_price, qty)
    logger.info("  목표: 순이익 %s  →  매도단가 %d원 이상", label, target_price)
    logger.info("=" * 55)

    while True:
        try:
            now_kst = datetime.now(KST)
            if (now_kst.hour > close_hour or
                    (now_kst.hour == close_hour and now_kst.minute >= close_min)):
                logger.info("장 마감(%02d:%02d) — 오늘 목표 미달성으로 종료",
                            close_hour, close_min)
                break

            info = kis_api.get_price(ticker)
            cur_price = int(info["stck_prpr"])
            net_pnl   = calc_net_pnl(buy_price, cur_price, qty)
            net_pct   = net_pnl / (buy_price * qty) * 100

            logger.info("[%s] 현재가 %d원  예상 순손익 %+.0f원 (%+.2f%%)  목표 %d원",
                        now_kst.strftime("%H:%M:%S"), cur_price,
                        net_pnl, net_pct, target_price)

            if cur_price >= target_price:
                logger.info("★ 목표 도달! 시장가 매도 실행 (%d주)", qty)
                result = kis_api.place_order(ticker, "SELL", qty, order_type="market")
                logger.info("매도 주문 완료  주문번호: %s  메시지: %s",
                            result["order_no"], result["message"])
                break

            time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("수동 중단")
            break
        except Exception as e:
            logger.error("오류: %s", e)
            time.sleep(interval * 2)


def main():
    parser = argparse.ArgumentParser(description="수익 목표 도달 시 자동 매도")
    parser.add_argument("--ticker",      required=True,  help="종목코드 (예: 036030)")
    parser.add_argument("--amount",      type=float,     help="목표 순이익 금액(원) — 전체 수량 기준")
    parser.add_argument("--pct",         type=float,     help="목표 수익률(%)")
    parser.add_argument("--qty",         type=int,       help="매도 수량 (미지정 시 잔고 자동 조회)")
    parser.add_argument("--buy-price",   type=int,       dest="buy_price",
                                                         help="평균매수단가(원) (미지정 시 잔고 자동 조회)")
    parser.add_argument("--interval",    type=int, default=30, help="체크 간격(초, 기본 30)")
    parser.add_argument("--close-time",  default="15:35", dest="close_time",
                                                         help="마감 시각 KST HH:MM (기본 15:35)")
    args = parser.parse_args()

    # amount / pct 둘 다 없거나 둘 다 있으면 오류
    if args.amount is None and args.pct is None:
        parser.error("--amount 또는 --pct 중 하나를 지정하세요.")
    if args.amount is not None and args.pct is not None:
        parser.error("--amount 와 --pct 중 하나만 지정하세요.")

    # 잔고에서 qty / buy_price 자동 조회
    qty       = args.qty
    buy_price = args.buy_price

    if qty is None or buy_price is None:
        logger.info("잔고 조회 중...")
        holding = get_holding(args.ticker)
        if holding is None:
            logger.error("보유 종목에 %s 없음 — 잔고를 확인하세요.", args.ticker)
            return
        if qty is None:
            qty = holding["qty"]
        if buy_price is None:
            buy_price = holding["avg_price"]
        logger.info("잔고 조회 완료: %s %d주 @ %d원",
                    holding["name"], qty, buy_price)

    close_hour, close_min = parse_close_time(args.close_time)

    run(ticker=args.ticker, qty=qty, buy_price=buy_price,
        target_amount=args.amount, target_pct=args.pct,
        interval=args.interval,
        close_hour=close_hour, close_min=close_min)


if __name__ == "__main__":
    main()
