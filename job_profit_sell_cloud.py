"""
수익매도 클라우드 잡 — Oracle VM stock-daemon이 30초 폴링(5분 1회 제한)으로 상시 실행
1) Gist profit_sell_jobs.json 에서 활성 잡 읽기 → 목표 달성 시 즉시 매도
2) 계좌 보유 종목 자동매도 규칙 (매 5분 체크)
   - 수익률 +FORCE_TAKE_PROFIT% 이상 → 전량 즉시 매도 (익절, 시간대별 10%/6%)
   - 수익률 -FORCE_STOP_LOSS% 이하  → 전량 즉시 매도 (손절, 기본 4%)
   - 단계별 손절 래칫: 보유 중 도달한 최고 수익률(peak)에 따라 손절 하한을 올려
     "올랐다가 손절"을 방지 (+2%→0%, +4%→+1.5%, +6%→+3.5% 아래로 못 내려가면 청산)
   - 환경변수 FORCE_STOP_LOSS 로 손절 기준 조정 (빈 값이면 손절 비활성). 익절은 시간대별 고정.
3) 그리드 연동: 손절/익절 대상 종목이 그리드 잡에서 관리 중이면
   매도 전 그리드 미체결 주문을 모두 취소하고 잡을 중단시켜 재매수 오작동 방지.
"""
from __future__ import annotations
import logging
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta, time as dt_time

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

# 손절 — 환경변수로 조정 가능, 빈 값이면 비활성
_env_sl = os.getenv("FORCE_STOP_LOSS", "-4.0").strip()
AUTO_LOSS_PCT: float | None = float(_env_sl) if _env_sl else None
# 익절은 시간 기반 (auto_sell_by_rule 내부에서 결정):
#   09:00~11:00: +10% (장 첫 2시간 급등 대응)
#   11:00 이후:  +6%  (완만한 수익 실현)

# KRX 정규시간(09:00~15:30)에만, 5분 1회 제한
_AUTO_SELL_INTERVAL = 300
_last_auto_sell     = 0.0

# 티커별 이번 보유기간 중 관찰된 최고 수익률(%) — 단계별 손절 래칫에 사용.
# daemon_stock.py 하나의 프로세스가 하루 종일 떠있으므로 메모리 상태로 충분하며,
# 매도 완료/미보유 시 아래에서 자동으로 정리된다.
_peak_pnl: dict[str, float] = {}

# (peak 도달 기준 %, 그 시점 손절 하한 %) — peak이 낮은 순서로 정렬
# +2% 한 번이라도 찍으면 최악의 경우도 본전, +6% 찍으면 +3.5%는 지키고 나온다
_RATCHET_STEPS = [(2.0, 0.0), (4.0, 1.5), (6.0, 3.5)]


def _cancel_grid_if_managed(ticker: str) -> None:
    """손절/익절 전 그리드 미체결 주문 취소 + 잡 중단.
    auto_sell_by_rule()이 시장가 전량 매도 후 그리드 사이클이 sell_waiting 주문을
    "체결됨"으로 오판해 재매수 주문을 내는 것을 막는다.
    """
    try:
        import job_stock_grid
        jobs = gist_writer._read_gist_file("stock_grid_jobs.json") or []
        changed = False
        for j in jobs:
            if j.get("ticker") == ticker and j.get("status") not in ("stopped", "stopping"):
                cancelled = job_stock_grid.stop_grid(j)
                logger.info("그리드 잡 강제 중단 (매도 연동) %s: 미체결 %d건 취소", ticker, cancelled)
                changed = True
        if changed:
            gist_writer._write_gist({"stock_grid_jobs.json": jobs})
    except Exception as e:
        logger.warning("그리드 잡 중단 실패 %s: %s", ticker, e)



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


def _own_managed_tickers() -> set:
    """저희 자동매매(조건부 매수잡/그리드/초단타)가 현재 사서 관리 중인 종목만 추림.
    2026-08-11 실측: KIS 계좌 전체를 대상으로 자동손절을 걸었더니, 수동으로(또는
    출처를 알 수 없는 경로로) 산 종목(파인엠텍 등)까지 저희 시스템이 임의로
    손절해버리는 문제가 있었음 — 사용자 요청으로 auto_sell_by_rule()의 대상을
    "저희가 직접 산 종목"으로만 한정한다."""
    tickers: set = set()
    try:
        for j in gist_writer._read_gist_file("profit_buy_jobs.json") or []:
            if j.get("status") == "done" and j.get("ticker"):
                tickers.add(j["ticker"])
    except Exception as e:
        logger.warning("profit_buy_jobs.json 조회 실패(자동손절 대상 판단): %s", e)
    try:
        for j in gist_writer._read_gist_file("stock_grid_jobs.json") or []:
            if j.get("status") in ("active", "stopping") and j.get("ticker"):
                tickers.add(j["ticker"])
    except Exception as e:
        logger.warning("stock_grid_jobs.json 조회 실패(자동손절 대상 판단): %s", e)
    try:
        for j in gist_writer._read_gist_file("scalp_stock_jobs.json") or []:
            if j.get("phase") == "holding" and j.get("ticker"):
                tickers.add(j["ticker"])
    except Exception as e:
        logger.warning("scalp_stock_jobs.json 조회 실패(자동손절 대상 판단): %s", e)
    return tickers


def _notify_untracked_holdings(holdings: list, own_tickers: set):
    """계좌에는 있는데 우리 잡 시스템(조건부매수/그리드/초단타) 어디에도 없는 종목 —
    즉 시스템이 아닌 경로로(수동 MTS 주문 등) 매수된 종목이 새로 나타나면 텔레그램 알림.
    2026-08-12 사용자 요청 — 예전에 파인엠텍/한솔아이원스/신영증권처럼 출처를 알 수
    없는 매수가 반복돼서, 이런 종목은 자동손절 대상도 아니니(own_tickers 스코프 밖)
    조용히 방치되지 않도록 발견 즉시 알림. 같은 종목 계속 보유 중이면 재알림 안 하고,
    한 번 없어졌다가 다시 생기면 다시 알림."""
    untracked = {h["ticker"]: h for h in holdings if h["ticker"] not in own_tickers}
    if not untracked:
        gist_writer._write_gist({"untracked_holdings_notified.json": []})
        return
    already = set(gist_writer._read_gist_file("untracked_holdings_notified.json") or [])
    new_tickers = set(untracked) - already
    if new_tickers:
        lines = ["⚠️ <b>시스템이 아닌 경로로 매수된 종목 발견</b>"]
        for t in sorted(new_tickers):
            h = untracked[t]
            lines.append(f"  • {h['name']}({t}) {h['qty']}주 @ {h['avg_price']:,}원")
        lines.append("\n조건부매수/그리드/초단타 어디에도 등록 안 된 종목이라 자동손절·익절 대상이 아닙니다.")
        notify.send("\n".join(lines))
        logger.warning("추적 안 되는 보유종목 알림 발송: %s", new_tickers)
    gist_writer._write_gist({"untracked_holdings_notified.json": sorted(untracked.keys())})


def auto_sell_by_rule():
    """보유 종목 자동매도 규칙 체크 (KRX 정규 09:00~15:30, 5분 1회 제한)
    저희 자동매매 시스템이 직접 매수한 종목만 대상으로 한다(수동/외부 매수 종목 제외).
    시간 기반 익절:
      09:00~11:00 (장 첫 2시간): +10% — 급등 시 빠르게 실현
      11:00 이후:               +6%  — 완만한 수익 실현
    손절: FORCE_STOP_LOSS% (기본 -4%)
    """
    global _last_auto_sell

    t = datetime.now(KST).time()
    # 시간 기반 익절 기준 결정
    if dt_time(9, 0) <= t < dt_time(11, 0):
        take_profit = 10.0   # 장 첫 2시간: 급등 익절
    else:
        take_profit = 3.0    # 11:00 이후: 완만한 익절

    if AUTO_LOSS_PCT is None:
        logger.info("손절 조건 비활성화 (FORCE_STOP_LOSS 미설정)")

    if not (dt_time(9, 0) <= t < dt_time(15, 30)):
        return
    if _time.time() - _last_auto_sell < _AUTO_SELL_INTERVAL:
        return
    _last_auto_sell = _time.time()

    sl_label = f"{AUTO_LOSS_PCT:.0f}%" if AUTO_LOSS_PCT is not None else "비활성"
    logger.info("강제 익절 +%.0f%% / 강제 손절 %s", take_profit, sl_label)

    try:
        bal = kis_api.get_balance()
    except Exception as e:
        logger.error("잔고 조회 실패: %s", e)
        return

    # 매도 완료·미보유 종목의 래칫 상태 정리 (다음에 재매수하면 0부터 새로 시작)
    held_tickers = {h["ticker"] for h in bal["holdings"]}
    for stale in list(_peak_pnl):
        if stale not in held_tickers:
            _peak_pnl.pop(stale, None)

    own_tickers = _own_managed_tickers()

    try:
        _notify_untracked_holdings(bal["holdings"], own_tickers)
    except Exception as e:
        logger.warning("추적 안 되는 보유종목 알림 실패: %s", e)

    for h in bal["holdings"]:
        ticker  = h["ticker"]
        if ticker not in own_tickers:
            continue  # 저희 시스템이 산 종목이 아니면 자동손절/익절 대상에서 제외
        pnl_pct = h["pnl_pct"]
        peak    = max(_peak_pnl.get(ticker, pnl_pct), pnl_pct)
        _peak_pnl[ticker] = peak

        # 단계별 손절 래칫 — peak이 도달한 가장 높은 단계의 하한선 아래로 내려오면 청산
        ratchet_floor = None
        for trig, floor in _RATCHET_STEPS:
            if peak >= trig:
                ratchet_floor = floor

        is_profit  = pnl_pct >= take_profit
        is_loss    = AUTO_LOSS_PCT is not None and pnl_pct <= AUTO_LOSS_PCT
        is_ratchet = ratchet_floor is not None and pnl_pct <= ratchet_floor
        if not is_profit and not is_loss and not is_ratchet:
            continue

        name      = h["name"]
        qty       = h["qty"]
        cur_price = h["eval_price"]
        if is_profit:
            reason, emoji = f"익절 (+{take_profit:.0f}% 달성)", "🚀"
        elif is_ratchet:
            reason, emoji = f"수익보호 (최고 {peak:+.1f}% → {ratchet_floor:+.1f}% 하회)", "📉"
        else:
            reason, emoji = f"손절 ({AUTO_LOSS_PCT:.0f}% 도달)", "🔴"

        logger.info("★ 자동매도 [%s] — %s(%s) 수익률 %.2f%% (peak %.2f%%)",
                    reason, name, ticker, pnl_pct, peak)
        _cancel_grid_if_managed(ticker)  # 그리드 관리 종목이면 먼저 주문 취소
        try:
            # 그리드 취소 직후 매도가능수량을 다시 확인 — 취소 전 스냅샷의
            # hldg_qty(총보유)만 믿고 그대로 주문하면 그리드 미체결 주문이 일부
            # 수량을 여전히 잠그고 있을 때 "주문 가능한 수량을 초과했습니다"
            # 오류가 남 (2026-08-07 실측: 12주 보유인데 반복 실패).
            fresh = kis_api.get_balance()
            fresh_h = next((x for x in fresh["holdings"] if x["ticker"] == ticker), None)
            sell_qty = fresh_h.get("sellable_qty", qty) if fresh_h else 0
            if sell_qty <= 0:
                logger.warning("%s 매도가능수량 0 — 이번 사이클 건너뜀(다음 사이클 재시도)", ticker)
                continue
            if sell_qty < qty:
                logger.warning("%s 매도가능수량(%d) < 보유수량(%d) — 가능한 만큼만 매도",
                                ticker, sell_qty, qty)

            result = kis_api.place_order(ticker, "SELL", sell_qty, order_type="market")
            pnl = (cur_price * sell_qty * (1 - SELL_FEE_RATE)
                   - h["avg_price"] * sell_qty * (1 + BUY_FEE_RATE))
            notify.send(
                f"{emoji} <b>자동매도 [{reason}]</b>  {name} ({ticker})\n"
                f"  {sell_qty}주 @ {cur_price:,}원  손익: <b>{pnl:+,}원 ({pnl_pct:+.2f}%)</b>\n"
                f"  주문번호: {result.get('order_no', '')}"
            )
            logger.info("자동매도 완료 %s %d주 @ %d원  주문번호: %s",
                        ticker, sell_qty, cur_price, result.get("order_no", ""))
            if sell_qty >= qty:
                _peak_pnl.pop(ticker, None)
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
                    # 2026-08-18 추가 — 여기서 sell_price를 안 채워서 대시보드
                    # 일별 수익현황(tab-autotrade.js atCalcDayProfit)이
                    # sell_price||exec_price||target_price||0 순으로 읽다가
                    # 전부 비어 있으면 0원으로 떨어져, 매도가 0원인 것처럼 계산돼
                    # "손절 -전액" 으로 잘못 표시되는 문제가 있었음(실측: 코리아써키트
                    # 007810 — 실제 67,400원 익절인데 -59,109원 손절로 표시).
                    # 실제 평균체결가를 최우선으로 쓰고, 조회 실패 시에만 지정가로 근사.
                    try:
                        fills = kis_api.get_daily_executions()
                        fill = next((f for f in fills if f.get("order_no") == order_no), None)
                        job["sell_price"] = fill["price"] if fill and fill.get("price") else target_price
                    except Exception as e:
                        logger.warning("%s(%s) 체결가 조회 실패, 지정가로 근사: %s", name, ticker, e)
                        job["sell_price"] = target_price
                    job["status"]      = "done"
                    job["executed_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                    job["force_done"]  = False
                    changed = True
                    logger.info("✅ %s(%s) 지정가 주문 체결 완료 처리 [%s]  체결가 %s원  주문번호: %s",
                                name, ticker, reason_label, job["sell_price"], order_no)
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
