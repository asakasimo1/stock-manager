"""
수익매도 클라우드 잡 — GitHub Actions에서 5분마다 실행
1) Gist profit_sell_jobs.json 에서 활성 잡 읽기 → 목표 달성 시 즉시 매도
2) 계좌 보유 종목 자동매도 규칙 (매 5분 체크)
   - 수익률 +FORCE_TAKE_PROFIT% 이상 → 전량 즉시 매도 (익절, 기본 20%)
   - 수익률 -FORCE_STOP_LOSS% 이하  → 전량 즉시 매도 (손절, 기본 4%)
   - 환경변수 FORCE_TAKE_PROFIT / FORCE_STOP_LOSS 로 조정 (빈 값이면 해당 조건 비활성)
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

import kis_api
import gist_writer
import notify

KST = timezone(timedelta(hours=9))

logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
_fmt = logging.Formatter("%(asctime)s KST %(levelname)s %(message)s")
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("profit_sell_cloud.log", encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)
BUY_FEE_RATE  = 0.00015            # 매수 수수료 0.015%
SELL_FEE_RATE = 0.00015 + 0.0018   # 매도 수수료 + 증권거래세 0.195%

# 강제 익절/손절 — 환경변수로 조정 가능, 빈 값이면 해당 조건 비활성화
_env_tp = os.getenv("FORCE_TAKE_PROFIT", "20.0").strip()
_env_sl = os.getenv("FORCE_STOP_LOSS",   "-4.0").strip()
AUTO_PROFIT_PCT: float | None = float(_env_tp) if _env_tp else None
AUTO_LOSS_PCT:   float | None = float(_env_sl) if _env_sl else None


def calc_target_price(buy_price: int, qty: int,
                      target_type: str, target_value: float) -> int:
    """목표 매도단가 계산 (수수료 포함)"""
    if target_type == "price":
        return int(target_value)          # 지정가: 입력값 그대로 사용
    elif target_type == "amount":
        break_even = buy_price * (1 + BUY_FEE_RATE)
        needed_per_share = (break_even * qty + target_value) / qty
        return int(needed_per_share / (1 - SELL_FEE_RATE)) + 1
    else:  # pct
        target_sell = buy_price * (1 + target_value / 100)
        return int(target_sell / (1 - SELL_FEE_RATE)) + 1


def auto_sell_by_rule():
    """보유 종목 자동매도 규칙 체크
    - 수익률 >= FORCE_TAKE_PROFIT% : 익절 매도 (None이면 비활성)
    - 수익률 <= FORCE_STOP_LOSS%   : 손절 매도 (None이면 비활성)
    """
    if AUTO_PROFIT_PCT is None and AUTO_LOSS_PCT is None:
        logger.info("강제 익절/손절 조건 비활성화 (FORCE_TAKE_PROFIT, FORCE_STOP_LOSS 미설정)")
        return

    tp_label = f"+{AUTO_PROFIT_PCT:.0f}%" if AUTO_PROFIT_PCT is not None else "비활성"
    sl_label = f"{AUTO_LOSS_PCT:.0f}%"    if AUTO_LOSS_PCT   is not None else "비활성"
    logger.info("강제 익절 %s / 강제 손절 %s", tp_label, sl_label)

    try:
        bal = kis_api.get_balance()
    except Exception as e:
        logger.error("잔고 조회 실패: %s", e)
        return

    for h in bal["holdings"]:
        pnl_pct   = h["pnl_pct"]
        is_profit = AUTO_PROFIT_PCT is not None and pnl_pct >= AUTO_PROFIT_PCT
        is_loss   = AUTO_LOSS_PCT   is not None and pnl_pct <= AUTO_LOSS_PCT
        if not is_profit and not is_loss:
            continue

        ticker    = h["ticker"]
        name      = h["name"]
        qty       = h["qty"]
        cur_price = h["eval_price"]
        reason    = f"익절 (+{AUTO_PROFIT_PCT:.0f}% 달성)" if is_profit else f"손절 ({AUTO_LOSS_PCT:.0f}% 도달)"
        emoji     = "🚀" if is_profit else "🔴"

        logger.info("★ 자동매도 [%s] — %s(%s) 수익률 %.2f%%", reason, name, ticker, pnl_pct)
        try:
            result = kis_api.place_order(ticker, "SELL", qty, order_type="market")
            pnl = (cur_price - h["avg_price"]) * qty
            notify.send(
                f"{emoji} <b>자동매도 [{reason}]</b>  {name} ({ticker})\n"
                f"  {qty}주 @ {cur_price:,}원  손익: <b>{pnl:+,}원 ({pnl_pct:+.2f}%)</b>\n"
                f"  주문번호: {result.get('order_no', '')}"
            )
            logger.info("자동매도 완료 %s %d주 @ %d원  주문번호: %s",
                        ticker, qty, cur_price, result.get("order_no", ""))
        except Exception as e:
            logger.error("%s 자동매도 실패: %s", ticker, e)
            notify.send(f"❌ 자동매도 실패: {name}({ticker}) — {e}")


def main():
    if not kis_api.is_any_market_open():
        logger.info("거래 시간 외 (KRX/NXT 모두 닫힘) — 종료")
        return

    nxt_mode = kis_api._is_nxt_time()
    logger.info("수익매도 체크 시작 [%s]", "NXT 시간대" if nxt_mode else "KRX 정규")

    # ── 자동매도 규칙: 익절 +20% / 손절 -4% ──
    auto_sell_by_rule()

    jobs = gist_writer._read_gist_file("profit_sell_jobs.json") or []
    active = [j for j in jobs if j.get("status") in ("active", "submitted")]

    if not active:
        logger.info("활성 잡 없음 — 종료")
        return

    logger.info("활성 잡 %d개 처리", len(active))
    changed = False

    # ── submitted 잡이 있으면 미체결 주문 목록 1회 조회 → 체결 자동 감지 ──
    has_submitted = any(j.get("status") == "submitted" for j in active)
    pending_order_nos: set = set()
    pending_fetched = False          # 조회 성공 여부를 명시적으로 추적
    if has_submitted:
        try:
            pending_order_nos = {o["order_no"] for o in kis_api.get_pending_orders()}
            pending_fetched = True
            logger.info("미체결 주문 %d건 조회 완료", len(pending_order_nos))
        except Exception as e:
            logger.warning("미체결 주문 조회 실패 (체결 자동 감지 건너뜀): %s", e)

    # ── amount/pct 타입 잡만 현재가 병렬 조회 (price 타입은 주문만 냄) ──
    poll_tickers = list({
        j["ticker"] for j in active
        if j.get("target_type") != "price" or j.get("status") == "active"
    })

    def _fetch_price(ticker: str):
        try:
            return ticker, kis_api.get_price(ticker)
        except Exception as e:
            logger.error("현재가 조회 실패 %s: %s", ticker, e)
            return ticker, None

    price_cache: dict = {}
    if poll_tickers:
        with ThreadPoolExecutor(max_workers=min(len(poll_tickers), 5)) as ex:
            futures = {ex.submit(_fetch_price, t): t for t in poll_tickers}
            for fut in as_completed(futures):
                ticker_key, result = fut.result()
                if result is not None:
                    price_cache[ticker_key] = result
        logger.info("현재가 병렬 조회 완료 (%d/%d)", len(price_cache), len(poll_tickers))

    for job in jobs:
        if job.get("status") not in ("active", "submitted"):
            continue

        ticker       = job["ticker"]
        name         = job.get("name", ticker)
        buy_price    = int(job.get("buy_price", 0))
        qty          = int(job["qty"])
        target_type  = job["target_type"]   # "amount" | "pct" | "price"
        target_value = float(job["target_value"])
        target_price = calc_target_price(buy_price, qty, target_type, target_value)

        try:
            # ── 지정가 타입: 등록 즉시 지정가 주문 → 거래소가 보관 (MTS에 노출) ──
            if target_type == "price" and job.get("status") == "active":
                logger.info("%s(%s) 지정가 매도 주문 — %d원 × %d주",
                            name, ticker, target_price, qty)
                result = kis_api.place_order(ticker, "SELL", qty,
                                             price=target_price, order_type="limit")
                job["status"]       = "submitted"          # 거래소에 주문 제출됨
                job["order_no"]     = result.get("order_no", "")
                job["submitted_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                changed = True
                logger.info("지정가 매도 주문 완료  주문번호: %s", job["order_no"])
                notify.send(
                    f"📋 <b>지정가 매도 주문 접수</b>  {name} ({ticker})\n"
                    f"  {qty}주 × {target_price:,}원 (지정가)\n"
                    f"  주문번호: {job['order_no']} — MTS에서 확인 가능"
                )
                continue   # 이번 사이클은 여기까지

            # ── submitted 상태(지정가): 미체결 목록 확인 → 없으면 체결 완료 처리 ──
            if target_type == "price" and job.get("status") == "submitted":
                order_no  = job.get("order_no", "")
                force_done = job.get("force_done", False)

                # 미체결 목록에 없거나 force_done 플래그 → 체결 완료로 처리
                # pending_fetched=True 일 때만 자동 감지 (조회 실패 시 오판 방지)
                order_filled = force_done or (
                    pending_fetched and order_no not in pending_order_nos
                )

                if order_filled:
                    reason_label = "수동 완료 처리" if force_done else "미체결 목록 미존재 → 체결 확인"
                    job["status"]      = "done"
                    job["executed_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                    job["force_done"]  = False
                    changed = True
                    logger.info("✅ %s(%s) 지정가 주문 체결 완료 처리 [%s]  주문번호: %s",
                                name, ticker, reason_label, order_no)
                    notify.send(
                        f"✅ <b>지정가 매도 체결 확인</b>  {name} ({ticker})\n"
                        f"  주문번호: {order_no}\n"
                        f"  처리: {reason_label}"
                    )
                else:
                    logger.info("%s(%s) 지정가 주문 대기 중 (주문번호: %s)",
                                name, ticker, order_no)
                continue

            # ── amount / pct 타입: 캐시된 현재가 사용 ───────────────────────────
            info = price_cache.get(ticker)
            if info is None:
                logger.warning("%s 현재가 캐시 없음 — 건너뜀", ticker)
                continue
            cur_price = int(info["stck_prpr"])

            if target_type == "amount":
                net_pnl = cur_price * qty * (1 - SELL_FEE_RATE) - buy_price * qty * (1 + BUY_FEE_RATE)
                label = f"{net_pnl:+.0f}원 / 목표 {target_value:+.0f}원"
            else:
                net_pct = (cur_price * (1 - SELL_FEE_RATE) / buy_price - 1) * 100
                label = f"{net_pct:+.2f}% / 목표 {target_value:+.2f}%"

            force_sell = job.get("force_sell", False)
            if force_sell:
                logger.info("%s(%s) 즉시 매도 요청 감지 — 현재가 %d원", name, ticker, cur_price)
            else:
                logger.info("%s(%s) 현재가 %d원  %s  [목표단가 %d원]",
                            name, ticker, cur_price, label, target_price)

            if force_sell or cur_price >= target_price:
                reason = "즉시매도(수동)" if force_sell else "목표달성"
                logger.info("★ %s! 매도 실행 — %s %d주 @ %d원", reason, ticker, qty, cur_price)
                result = kis_api.place_order(ticker, "SELL", qty, order_type="market")
                job["status"]      = "done"
                job["sell_price"]  = cur_price
                job["executed_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                job["order_no"]    = result.get("order_no", "")
                job["force_sell"]  = False
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
