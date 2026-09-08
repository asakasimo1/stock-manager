"""1회성 마이그레이션: 기존 trader_trades.json(코인/주식 혼합, 100건 상한)을
trader_trades_coin.json / trader_trades_stock.json으로 분리 저장.
실행 후 다시 실행할 필요 없음 — gist_writer.py는 이제 이 두 파일만 사용함.
"""
import gist_writer

def main():
    raw = gist_writer._fetch_gist_raw()
    files = raw.get("files", {})
    if gist_writer.FILENAME not in files:
        print(f"'{gist_writer.FILENAME}' 파일이 Gist에 없음 — 마이그레이션할 데이터 없음")
        return

    import json
    legacy = json.loads(files[gist_writer.FILENAME].get("content", "[]"))
    print(f"기존 {gist_writer.FILENAME}: 총 {len(legacy)}건")

    by_market = {"coin": [], "stock": []}
    for t in legacy:
        by_market[gist_writer._market_of(t.get("ticker"))].append(t)

    for market, legacy_records in by_market.items():
        current = gist_writer._read_trades(market)
        known_ids = {r.get("id") for r in current if r.get("id")}
        known_order_nos = {r.get("order_no") for r in current if r.get("order_no")}
        added = 0
        for rec in legacy_records:
            if rec.get("id") in known_ids:
                continue
            if rec.get("order_no") and rec.get("order_no") in known_order_nos:
                continue
            current.append(rec)
            added += 1
        current.sort(key=lambda r: (r.get("date", ""), r.get("time", "")), reverse=True)
        current = current[:gist_writer.MAX_HISTORY]
        ok = gist_writer._write_trades(current, market)
        print(f"{market}: 기존파일 {len(legacy_records)}건 중 {added}건 신규 병합 → "
              f"{gist_writer._trades_filename(market)} 총 {len(current)}건 저장 {'성공' if ok else '실패'}")


if __name__ == "__main__":
    main()
