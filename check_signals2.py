from dotenv import load_dotenv
load_dotenv()
import gist_writer, json
from datetime import datetime, timezone, timedelta

for fname in ["signals.json", "picks.json", "briefing.json"]:
    data = gist_writer._read_gist_file(fname)
    if data is None:
        print(f"{fname}: 없음")
        continue
    if isinstance(data, list) and data:
        dates = sorted(set(str(d.get("date","")) for d in data), reverse=True)
        print(f"{fname}: 총 {len(data)}건, 최신 날짜들: {dates[:5]}")
    elif isinstance(data, dict):
        print(f"{fname}: dict keys={list(data.keys())[:10]}")

print()
print("=== profit_buy_jobs.json 오늘 활동 ===")
jobs = gist_writer._read_gist_file("profit_buy_jobs.json") or []
today = "2026-08-05"
for j in jobs:
    print(j.get("ticker"), j.get("name"), j.get("status"), j.get("created_at", j.get("date","")))
