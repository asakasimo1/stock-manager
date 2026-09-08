"""1회성 백필: job_balance.py의 _reconcile_trades()는 매 사이클 '오늘'
체결만 조회하기 때문에, 코인/주식 파일 분리(trader_trades_coin.json /
trader_trades_stock.json) 이전에 누락됐던 과거 날짜(예: 8/13, 8/14)의 주식
체결은 데몬 재시작만으로는 자동 채워지지 않는다. 지정한 날짜들의 KIS 실제
체결을 조회해서 trader_trades_stock.json에 order_no 기준 중복 없이 채운다.

사용법(VM에서): python3 backfill_stock_trades.py 20260813 20260814
"""
import sys
from datetime import datetime
import kis_api
import gist_writer

KST = gist_writer._KST


def main(dates):
    existing = gist_writer._read_trades("stock")
    known_order_nos = {t.get("order_no") for t in existing if t.get("order_no")}
    total_new = 0

    for d in dates:
        try:
            executions = kis_api.get_daily_executions(d)
        except Exception as e:
            print(f"{d}: 조회 실패 — {e}")
            continue
        if not executions:
            print(f"{d}: 체결 없음")
            continue

        date_str = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        new_count = 0
        for ex in executions:
            order_no = ex.get("order_no")
            if not order_no or order_no in known_order_nos:
                continue
            raw_time = ex.get("time", "")
            trade_time = f"{raw_time[0:2]}:{raw_time[2:4]}" if len(raw_time) >= 4 else None
            gist_writer.log_trade(
                ticker=ex["ticker"],
                name=ex["name"],
                trade_type="buy" if ex["side"] == "BUY" else "sell",
                price=ex["price"],
                qty=ex["qty"],
                order_no=order_no,
                trade_date=date_str,
                trade_time=trade_time,
            )
            known_order_nos.add(order_no)
            new_count += 1
        print(f"{d}: 체결 {len(executions)}건 중 신규 {new_count}건 버퍼에 추가")
        total_new += new_count

    if total_new:
        gist_writer.flush_trades()
        print(f"완료 — 총 {total_new}건 trader_trades_stock.json에 반영")
    else:
        print("신규 반영 건 없음")


if __name__ == "__main__":
    dates = sys.argv[1:]
    if not dates:
        print("사용법: python3 backfill_stock_trades.py YYYYMMDD [YYYYMMDD ...]")
        sys.exit(1)
    main(dates)
