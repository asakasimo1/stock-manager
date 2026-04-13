"""
Job — 멀티팩터 월간 리밸런싱

매월 첫 번째 영업일 09:10 KST 실행
  1. 팩터 스코어 계산 → 상위 TOP_N 종목 선정
  2. 기존 보유 중 탈락 종목 매도
  3. 신규 편입 종목 매수
  4. 결과 텔레그램 알림

단기 봇(job_buy/monitor)과 완전 분리:
  - 별도 Supabase 테이블 (factor_positions, factor_watchlist)
  - 손절/익절 없음 — 월간 리밸런싱만
"""

import os, time, logging
from datetime import date
from dotenv import load_dotenv

import kis_api
import state_db
import notify
from factor_engine import get_kospi200_tickers, score_factors

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 설정 ─────────────────────────────────────
MAX_FACTOR_POSITIONS = int(os.getenv("MAX_FACTOR_POSITIONS") or "10")   # 최대 보유 종목
FACTOR_ORDER_AMOUNT  = int(os.getenv("FACTOR_ORDER_AMOUNT")  or "0")    # 0이면 균등 분배


def main():
    logger.info("=" * 55)
    logger.info("  멀티팩터 월간 리밸런싱 — %s", date.today())
    logger.info("=" * 55)

    # ── 1. 팩터 스코어링 ──────────────────────
    universe = get_kospi200_tickers()
    logger.info("유니버스: KOSPI200 %d종목", len(universe))

    top_df = score_factors(universe)
    if top_df.empty:
        notify.send("⚠️ 팩터 리밸런싱 실패: 후보 종목 없음")
        return

    new_picks = {row["ticker"]: row for _, row in top_df.iterrows()}

    # Supabase 저장
    state_db.set_factor_watchlist(top_df.to_dict("records"))

    # ── 2. 현재 팩터 포지션 조회 ─────────────
    cur_positions = state_db.get_factor_positions()
    cur_tickers   = set(cur_positions.keys())
    new_tickers   = set(new_picks.keys())

    to_sell = cur_tickers - new_tickers        # 탈락 종목
    to_buy  = new_tickers - cur_tickers        # 신규 편입
    to_hold = cur_tickers & new_tickers        # 유지

    logger.info("유지: %d  매도: %d  신규매수: %d",
                len(to_hold), len(to_sell), len(to_buy))

    # ── 3. 탈락 종목 매도 ─────────────────────
    pnl_total = 0
    for ticker in to_sell:
        pos = cur_positions[ticker]
        try:
            price_info = kis_api.get_price(ticker)
            cur_price  = int(price_info["stck_prpr"])
            result     = kis_api.place_order(ticker, "SELL", pos["qty"])
            pnl        = (cur_price - pos["buy_price"]) * pos["qty"]
            pnl_pct    = (cur_price - pos["buy_price"]) / pos["buy_price"] * 100
            pnl_total += pnl

            state_db.delete_factor_position(ticker)
            notify.send(
                f"📤 <b>[팩터] 리밸런싱 매도</b>  {pos.get('name','')} ({ticker})\n"
                f"  보유기간: {(date.today() - pos['buy_date']).days}일\n"
                f"  손익: <b>{pnl:+,}원 ({pnl_pct:+.2f}%)</b>"
            )
            logger.info("팩터 매도  %s  손익: %+d원  (%+.2f%%)", ticker, pnl, pnl_pct)
            time.sleep(0.5)
        except Exception as e:
            logger.error("%s 팩터 매도 실패: %s", ticker, e)
            notify.send(f"❌ [팩터] 매도 실패: {ticker} — {e}")

    # ── 4. 신규 종목 매수 ─────────────────────
    if to_buy:
        try:
            bal  = kis_api.get_balance()
            cash = bal["cash"]
            time.sleep(0.3)
        except Exception as e:
            logger.error("잔고 조회 실패: %s", e)
            notify.send(f"❌ [팩터] 잔고 조회 실패 — {e}")
            return

        # 남은 슬롯 수로 균등 분배
        remaining_slots = MAX_FACTOR_POSITIONS - len(to_hold)
        buy_list = sorted(to_buy,
                          key=lambda t: new_picks[t]["score"],
                          reverse=True)[:remaining_slots]

        alloc_per = cash // len(buy_list) if buy_list else 0
        if FACTOR_ORDER_AMOUNT > 0:
            alloc_per = min(alloc_per, FACTOR_ORDER_AMOUNT)

        for ticker in buy_list:
            sig = new_picks[ticker]
            try:
                price_info = kis_api.get_price(ticker)
                cur_price  = int(price_info["stck_prpr"])
                if cur_price <= 0:
                    continue

                qty = int(alloc_per / cur_price)
                if qty <= 0:
                    logger.warning("%s 수량 0 — 건너뜀 (가격: %d원)", ticker, cur_price)
                    continue

                result = kis_api.place_order(ticker, "BUY", qty)
                amount = qty * cur_price

                state_db.upsert_factor_position(ticker, {
                    "name":         sig.get("name", ""),
                    "buy_price":    cur_price,
                    "qty":          qty,
                    "buy_date":     date.today(),
                    "score":        float(sig.get("score", 0)),
                    "pbr":          float(sig.get("pbr", 0)),
                    "per":          float(sig.get("per", 0)),
                    "roe":          float(sig.get("roe", 0)),
                    "momentum_12m": float(sig.get("momentum_12m", 0)),
                })
                cash -= amount

                notify.send(
                    f"📥 <b>[팩터] 신규 편입</b>  {sig.get('name','')} ({ticker})\n"
                    f"  {qty}주 × {cur_price:,}원 = <b>{amount:,}원</b>\n"
                    f"  점수: {sig['score']:.3f}  "
                    f"PBR:{sig['pbr']:.2f}  PER:{sig['per']:.1f}  ROE:{sig['roe']:.1f}%  "
                    f"Mom:{sig['momentum_12m']*100:+.1f}%"
                )
                logger.info("팩터 매수  %s  %d주  %d원", ticker, qty, amount)
                time.sleep(0.5)

            except Exception as e:
                logger.error("%s 팩터 매수 실패: %s", ticker, e)
                notify.send(f"❌ [팩터] 매수 실패: {ticker} — {e}")

    # ── 5. 리밸런싱 요약 알림 ─────────────────
    factor_positions = state_db.get_factor_positions()
    hold_lines = "\n".join(
        f"  • {t} {p.get('name','')}  {p['qty']}주"
        for t, p in factor_positions.items()
    )
    notify.send(
        f"⚖️ <b>팩터 리밸런싱 완료 — {date.today()}</b>\n"
        f"  유지 {len(to_hold)}  매도 {len(to_sell)}  신규 {len(to_buy)}\n"
        f"  당월 실현손익: {pnl_total:+,}원\n"
        f"  현재 포트폴리오 ({len(factor_positions)}종목):\n"
        f"{hold_lines}"
    )


if __name__ == "__main__":
    main()
