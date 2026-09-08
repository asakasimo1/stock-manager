"""
케이티알파(036030) 수익 50원 도달 시 즉시 매도
수수료: 매수 0.015% + 매도 0.015% + 증권거래세 0.18% = 0.195%

실행 모드:
  로컬  : python monitor_036030.py  → 30초 간격 루프 (15:35까지)
  GH Actions : GITHUB_ACTIONS=true 환경 자동 감지 → 1회 체크 후 종료
"""

import os, time, logging
from datetime import datetime, timezone, timedelta
import kis_api

LOG_FILE = "monitor_036030.log"
_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 로컬/GH Actions 모두 monitor_036030.log에 동일 포맷으로 기록
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)

TICKER     = "036030"
NAME       = "케이티알파"
BUY_PRICE  = 4960          # 평균 매수단가
QTY        = 1
TARGET_NET = 50            # 목표 순이익 (원)

BUY_FEE_RATE  = 0.00015            # 매수수수료
SELL_FEE_RATE = 0.00015 + 0.0018   # 매도수수료 + 증권거래세
BUY_COST = BUY_PRICE * (1 + BUY_FEE_RATE)  # 매수수수료 포함 실매입단가

# 순이익 50원을 위한 최소 매도가
TARGET_PRICE = int((BUY_COST + TARGET_NET) / (1 - SELL_FEE_RATE)) + 1

KST            = timezone(timedelta(hours=9))
GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"

logger.info("모니터링 시작: %s(%s)  매수단가 %d원  목표 매도가 %d원 이상  [%s]",
            NAME, TICKER, BUY_PRICE, TARGET_PRICE,
            "GH Actions 단일체크" if GITHUB_ACTIONS else "로컬 루프")


def check_and_sell() -> bool:
    """현재가 확인 후 목표 도달 시 매도. 매도 성공 시 True 반환."""
    now_kst = datetime.now(KST)
    if now_kst.hour >= 15 and now_kst.minute >= 35:
        logger.info("장 마감 시간 — 종료")
        return False

    info      = kis_api.get_price(TICKER)
    cur_price = int(info["stck_prpr"])
    net_pnl   = cur_price * (1 - SELL_FEE_RATE) - BUY_COST

    logger.info("현재가 %d원  예상순이익 %+.0f원  (목표: +%d원)",
                cur_price, net_pnl, TARGET_NET)

    if cur_price >= TARGET_PRICE:
        logger.info("목표 도달! 즉시 매도 실행")
        result = kis_api.place_order(TICKER, "SELL", QTY, order_type="market")
        logger.info("매도 완료  주문번호: %s", result["order_no"])
        return True

    return False


if GITHUB_ACTIONS:
    # ── GitHub Actions: 1회 체크 후 종료 (5분마다 스케줄로 호출됨) ──
    try:
        check_and_sell()
    except Exception as e:
        logger.error("오류: %s", e)

else:
    # ── 로컬: 30초 간격 루프 (15:35까지) ──
    sold = False
    while not sold:
        try:
            now_kst = datetime.now(KST)
            if now_kst.hour >= 15 and now_kst.minute >= 35:
                logger.info("장 마감 — 모니터링 종료 (미체결)")
                break

            sold = check_and_sell()
            if not sold:
                time.sleep(30)

        except Exception as e:
            logger.error("오류: %s", e)
            time.sleep(60)

    if not sold:
        logger.info("오늘 목표 미달성")
