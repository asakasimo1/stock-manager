"""
코인 매수 잡 — GitHub Actions에서 5분마다 24/7 실행
Gist coin_buy_jobs.json 에서 활성 잡 읽기 → 조건 달성 시 즉시 매수 → 상태 업데이트

조건 유형:
  market_krw : 등록 즉시 시장가 매수 (KRW 금액 기준, 다음 실행 시 실행)
  limit      : 현재가 <= target_price 일 때 지정가/시장가 매수
"""
import logging
from datetime import datetime, timezone, timedelta

import upbit_api
import gist_writer

KST = timezone(timedelta(hours=9))
logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
_fmt = logging.Formatter("%(asctime)s KST %(levelname)s %(message)s")
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("coin_buy_cloud.log", encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def main():
    logger.info("코인 매수 잡 체크 시작")

    raw = gist_writer._read_gist_file("coin_buy_jobs.json")
    if raw is None:
        logger.error("Gist 읽기 실패 — GH_TOKEN 권한(gist scope) 또는 GIST_ID 확인 필요")
        return
    jobs = raw if isinstance(raw, list) else []
    logger.info("Gist에서 총 %d개 잡 로드", len(jobs))

    active = [j for j in jobs if j.get("status") == "active"]
    if not active:
        logger.info("활성 매수 잡 없음 — 종료")
        return

    logger.info("활성 잡 %d개 처리", len(active))
    changed = False

    # 현재가 일괄 조회
    active_tickers = list({j["ticker"] for j in active})
    price_cache: dict = {}
    try:
        price_cache = upbit_api.get_prices(active_tickers)
    except Exception as e:
        logger.error("현재가 일괄 조회 실패: %s", e)

    for job in jobs:
        if job.get("status") != "active":
            continue

        ticker = job["ticker"]
        name   = job.get("name", ticker)
        cond   = job.get("condition_type", "market_krw")

        price_info = price_cache.get(ticker)
        if not price_info:
            try:
                price_info = upbit_api.get_price(ticker)
            except Exception as e:
                logger.error("현재가 조회 실패 %s: %s", ticker, e)
                continue

        cur_price = price_info["price"]

        # ── 시장가 즉시 매수 ──────────────────────────────────────
        if cond == "market_krw":
            krw_amount = float(job.get("krw_amount", 0))
            coin_qty   = float(job.get("coin_qty", 0))
            if krw_amount <= 0 and coin_qty > 0:
                krw_amount = round(coin_qty * cur_price)
            if krw_amount <= 0:
                logger.warning("%s(%s) KRW 금액 미설정 — 건너뜀", name, ticker)
                continue

            logger.info("★ 시장가 매수 실행: %s(%s) %s원", name, ticker, f"{int(krw_amount):,}")
            try:
                result = upbit_api.place_order(
                    market=ticker,
                    side="bid",
                    ord_type="price",
                    price=krw_amount,
                )
                job["status"]      = "done"
                job["executed_at"] = now_kst()
                job["order_uuid"]  = result.get("uuid", "")
                job["exec_price"]  = cur_price
                job["exec_qty"]    = round(krw_amount / cur_price, 8)
                changed = True
                logger.info("시장가 매수 완료 %s @ %s원  UUID: %s",
                            ticker, f"{cur_price:,.0f}", result.get("uuid", ""))
            except Exception as e:
                logger.error("%s 시장가 매수 실패: %s", ticker, e)

        # ── 지정가 조건부 매수 ────────────────────────────────────
        elif cond == "limit":
            target_price = float(job.get("target_price", 0))
            if target_price <= 0:
                logger.warning("%s(%s) 목표가 미설정 — 건너뜀", name, ticker)
                continue

            logger.info("  %s(%s) 현재가 %s / 목표가 %s",
                        name, ticker, f"{cur_price:,.0f}", f"{target_price:,.0f}")

            if cur_price > target_price:
                logger.info("  → 조건 미달 (현재가 > 목표가)")
                continue

            # 목표가 이하 달성 → 지정가로 매수
            krw_amount = float(job.get("krw_amount", 0))
            coin_qty   = float(job.get("coin_qty", 0))

            if krw_amount > 0 and coin_qty <= 0:
                coin_qty = round(krw_amount / target_price, 8)

            if coin_qty <= 0:
                logger.warning("%s(%s) 수량 계산 불가 — 건너뜀", name, ticker)
                continue

            logger.info("★ 지정가 매수 실행: %s(%s) %.8f개 @ %s원",
                        name, ticker, coin_qty, f"{target_price:,.0f}")
            try:
                result = upbit_api.place_order(
                    market=ticker,
                    side="bid",
                    ord_type="limit",
                    volume=coin_qty,
                    price=target_price,
                )
                job["status"]      = "done"
                job["executed_at"] = now_kst()
                job["order_uuid"]  = result.get("uuid", "")
                job["exec_price"]  = target_price
                job["exec_qty"]    = coin_qty
                changed = True
                logger.info("지정가 매수 완료 %s %.8f개 @ %s원  UUID: %s",
                            ticker, coin_qty, f"{target_price:,.0f}", result.get("uuid", ""))
            except Exception as e:
                logger.error("%s 지정가 매수 실패: %s", ticker, e)

    if changed:
        ok = gist_writer._write_gist({"coin_buy_jobs.json": jobs})
        logger.info("Gist 저장 %s", "완료" if ok else "실패")
    else:
        logger.info("변경 없음 — Gist 저장 건너뜀")


if __name__ == "__main__":
    main()
