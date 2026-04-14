"""
매수 조건 잡 — GitHub Actions에서 5분마다 실행
Gist profit_buy_jobs.json 에서 활성 잡 읽기 → 조건 달성 시 즉시 매수 → 상태 업데이트

조건 유형:
  market : 등록 즉시 시장가 매수 (다음 실행 시 바로 실행)
  limit  : 현재가 <= target_price 일 때 매수
"""
import logging
from datetime import datetime, timezone, timedelta

import kis_api
import gist_writer

KST = timezone(timedelta(hours=9))

logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    if not kis_api.is_any_market_open():
        logger.info("거래 시간 외 (KRX/NXT 모두 닫힘) — 종료")
        return

    nxt_mode = kis_api._is_nxt_time()
    logger.info("매수 잡 체크 시작 [%s]", "NXT 시간대" if nxt_mode else "KRX 정규")

    raw = gist_writer._read_gist_file("profit_buy_jobs.json")
    if raw is None:
        logger.error("Gist 읽기 실패 — GH_TOKEN 권한(gist scope) 또는 GIST_ID 확인 필요")
        return
    jobs = raw if isinstance(raw, list) else []
    logger.info("Gist에서 총 %d개 잡 로드", len(jobs))
    for j in jobs:
        logger.info("  · %s(%s) status=%s phase=%s",
                    j.get("name", "?"), j.get("ticker", "?"),
                    j.get("status", "?"), j.get("phase", "-"))

    active = [j for j in jobs if j.get("status") == "active"]

    if not active:
        logger.info("활성 매수 잡 없음 — 종료")
        return

    logger.info("활성 잡 %d개 처리", len(active))
    changed = False

    for job in jobs:
        if job.get("status") != "active":
            continue

        ticker         = job["ticker"]
        name           = job.get("name", ticker)
        condition_type = job.get("condition_type", "limit")  # "market" | "limit"
        target_price   = int(job.get("target_price", 0))
        qty            = int(job.get("qty", 0))
        amount         = int(job.get("amount", 0))           # 금액 기준일 때

        try:
            info = kis_api.get_price(ticker)
            cur_price = int(info["stck_prpr"])

            # 매수 수량 결정
            if qty > 0:
                order_qty = qty
            elif amount > 0:
                order_qty = amount // cur_price
                if order_qty < 1:
                    logger.warning("%s 금액 %d원으로 현재가 %d원 1주 매수 불가", name, amount, cur_price)
                    continue
            else:
                logger.warning("%s 수량/금액 미설정", name)
                continue

            # 조건 판단
            if condition_type == "market":
                should_buy = True
                logger.info("%s(%s) 즉시 시장가 매수 예정 — %d주", name, ticker, order_qty)
            else:  # limit
                should_buy = cur_price <= target_price
                logger.info("%s(%s) 현재가 %d원 / 목표매수가 %d원 — %s",
                            name, ticker, cur_price, target_price,
                            "★ 매수 조건 달성" if should_buy else "대기 중")

            if should_buy:
                result = kis_api.place_order(ticker, "BUY", order_qty, order_type="market")
                job["status"]      = "done"
                job["buy_price"]   = cur_price
                job["buy_qty"]     = order_qty
                job["executed_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                job["order_no"]    = result.get("order_no", "")
                changed = True
                logger.info("✅ 매수 완료: %s %d주 @ %d원  주문번호: %s",
                            ticker, order_qty, cur_price, job["order_no"])

        except Exception as e:
            logger.error("%s(%s) 처리 실패: %s", name, ticker, e)

    if changed:
        ok = gist_writer._write_gist({"profit_buy_jobs.json": jobs})
        logger.info("Gist 업데이트 %s", "완료" if ok else "실패")

    logger.info("매수 잡 체크 완료")


if __name__ == "__main__":
    main()
