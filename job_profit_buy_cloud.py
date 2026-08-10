"""
매수 조건 잡 — GitHub Actions에서 5분마다 실행
Gist profit_buy_jobs.json 에서 활성 잡 읽기 → 조건 달성 시 즉시 매수 → 상태 업데이트

조건 유형:
  market : 등록 즉시 시장가 매수
  limit  : 현재가 <= target_price 일 때 매수
"""
import logging
import time as _time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import kis_api
import gist_writer

KST = timezone(timedelta(hours=9))

logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
_fmt = logging.Formatter("%(asctime)s KST %(levelname)s %(message)s")
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("profit_buy_cloud.log", encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)

BUY_FEE  = 0.00015
SELL_FEE = 0.00195

# 당일 이미 이 수치 이상 급등한 종목은 market 매수 안 함 (고점 추격 방지)
MAX_SURGE_PCT = 3.0

# 최근 분당 거래량 / 전일 평균 분당 거래량 이 비율 미만이면 수급 소멸로 간주
MIN_VOL_FACTOR = 0.8

# 거래량 이력 (프로세스 메모리 — 재시작 시 리셋됨)
_vol_hist: dict = {}  # ticker → [(monotonic_time, acml_vol)]


def _vol_surge_factor(ticker: str, acml_vol: int, prdy_vol: int):
    """최근 분당 거래량 속도 / 전일 평균 분당 속도 비율.
    > 1.5 : 수급 급증 / < 0.8 : 수급 소멸 / None : 데이터 부족(초기 2회 미만)
    """
    if acml_vol <= 0 or prdy_vol <= 0:
        return None
    now = _time_mod.monotonic()
    hist = _vol_hist.setdefault(ticker, [])
    hist.append((now, acml_vol))
    cutoff = now - 300  # 최근 5분만 보관
    _vol_hist[ticker] = [(t, v) for t, v in hist if t > cutoff]
    if len(_vol_hist[ticker]) < 2:
        return None
    oldest_t, oldest_v = _vol_hist[ticker][0]
    elapsed_sec = max(1.0, now - oldest_t)
    if elapsed_sec < 25:  # 30초 폴링 기준 최소 1 사이클 경과
        return None
    recent_per_min = (acml_vol - oldest_v) / (elapsed_sec / 60.0)
    prdy_per_min   = prdy_vol / 390.0   # 09:00~15:30 = 390분
    return round(recent_per_min / prdy_per_min, 2)



def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


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
        logger.info("활성 매수 잡 없음")
        return

    logger.info("활성 잡 %d개 처리", len(active))
    changed = False

    # ── 활성 잡 현재가 일괄 병렬 조회 ──────────────────────────────
    active_tickers = list({j["ticker"] for j in active})

    def _fetch_price(ticker: str):
        try:
            return ticker, kis_api.get_price(ticker)
        except Exception as e:
            logger.error("현재가 조회 실패 %s: %s", ticker, e)
            return ticker, None

    price_cache: dict = {}
    with ThreadPoolExecutor(max_workers=min(len(active_tickers), 5)) as ex:
        futures = {ex.submit(_fetch_price, t): t for t in active_tickers}
        for fut in as_completed(futures):
            ticker_key, result = fut.result()
            if result is not None:
                price_cache[ticker_key] = result

    logger.info("현재가 병렬 조회 완료 (%d/%d)", len(price_cache), len(active_tickers))

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
            info = price_cache.get(ticker)
            if info is None:
                logger.warning("%s 현재가 캐시 없음 — 건너뜀", ticker)
                continue
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
                prdy_ctrt = float(info.get("prdy_ctrt", 0))
                if prdy_ctrt > MAX_SURGE_PCT:
                    logger.warning(
                        "⛔ 급등 매수 차단: %s 전일대비 +%.1f%% (임계 +%.0f%%) — 잡 취소",
                        name, prdy_ctrt, MAX_SURGE_PCT)
                    job["status"]      = "skipped"
                    job["skip_reason"] = f"급등 차단 (전일대비 +{prdy_ctrt:.1f}%)"
                    job["executed_at"] = now_kst()
                    changed = True
                    continue
                # 거래량 수급 체크: 최근 분당 거래량이 전일 대비 급감 중이면 스킵
                acml_vol = int(info.get("acml_vol", 0))
                prdy_vol = int(info.get("prdy_vol", 0))
                vol_factor = _vol_surge_factor(ticker, acml_vol, prdy_vol)
                if vol_factor is not None and vol_factor < MIN_VOL_FACTOR:
                    logger.info(
                        "📉 수급 소멸 — %s 분당거래량 전일비 %.2fx (임계 %.1fx) "
                        "— 30초 후 재체크",
                        name, vol_factor, MIN_VOL_FACTOR)
                    continue  # active 유지, 다음 사이클에서 재평가
                if vol_factor is not None:
                    logger.info("📊 수급 확인 — %s %.2fx", name, vol_factor)
                should_buy = True
                logger.info("%s(%s) 즉시 시장가 매수 예정 — %d주 (전일대비 +%.1f%%)",
                            name, ticker, order_qty, prdy_ctrt)
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
