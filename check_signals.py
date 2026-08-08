from dotenv import load_dotenv
load_dotenv()
import gist_writer, json
from datetime import datetime, timezone, timedelta
KST = timezone(timedelta(hours=9))
today = datetime.now(KST).strftime("%Y-%m-%d")
print("오늘 날짜:", today)

for fname in ["signals.json", "picks.json"]:
    data = gist_writer._read_gist_file(fname)
    if data is None:
        print(f"{fname}: 없음")
        continue
    if isinstance(data, list):
        today_items = [d for d in data if str(d.get("date","")).startswith(today)]
        print(f"{fname}: 총 {len(data)}건, 오늘({today}) {len(today_items)}건")
        for it in today_items[:10]:
            print("  ", it)
    else:
        print(f"{fname}: dict, keys={list(data.keys())[:10]}")
