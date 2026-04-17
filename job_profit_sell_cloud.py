"""
수익매도 클라우드 잡 — GitHub Actions에서 5분마다 실행
1) Gist profit_sell_jobs.json 에서 활성 잡 읽기 → 목표 달성 시 즉시 매도
2) 계좌 보유 종목 자동매도 규칙 (매 5분 체크)
   - 수익률 +20% 이상 → 전량 즉시 매도 (익절)
   - 수익률 -4% 이하  → 전량 즉시 매도 (손절)
"""
import logging
from datetime import datetime, timezone, timedelta

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
AUTO_PROFIT_PCT = 20.0             # 자동 익절 기준 (%)
AUTO_LOSS_PCT   = -4.0             # 자동 손절 기준 (%)


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
    - 수익률 >= +20% : 익절 매도
    - 수익률 <= -4%  : 손절 매도
    """
    try:
        bal = kis_api.get_balance()
    except Exception as e:
        logger.error("잔고 조회 실패: %s", e)
        return

    for h in bal["holdings"]:
        pnl_pct   = h["pnl_pct"]
        is_profit = pnl_pct >= AUTO_PROFIT_PCT
        is_loss   = pnl_pct <= AUTO_LOSS_PCT
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

            # ── submitted 상태(지정가): 체결 여부 별도 폴링 없이 MTS 확인 유도 ──
            if target_type == "price" and job.get("status") == "submitted":
                logger.info("%s(%s) 지정가 주문 제출 완료 (주문번호: %s) — MTS 체결 대기 중",
                            name, ticker, job.get("order_no", ""))
                continue

            # ── amount / pct 타입: 현재가 폴링 후 시장가 매도 ──────────────────
            info = kis_api.get_price(ticker)
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
