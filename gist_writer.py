"""
GitHub Gist에 거래 내역을 기록하는 모듈
stock_analyzer 대시보드의 '거래 내역' 탭에 표시됩니다.

필요한 환경변수 (.env):
  GIST_ID   — 기록할 Gist ID (stock_analyzer와 동일한 Gist 사용)
  GH_TOKEN  — GitHub Personal Access Token (gist 권한 필요)
"""
import json
import os
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

_KST = timezone(timedelta(hours=9))

GIST_ID  = os.environ.get("GIST_ID", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")

FILENAME     = "trader_trades.json"
MAX_HISTORY  = 100   # 최대 보관 건수


def _headers():
    return {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "stock-trader",
    }


def _now_kst() -> datetime:
    return datetime.now(_KST)


def _read_trades() -> list:
    if not GIST_ID or not GH_TOKEN:
        return []
    try:
        r = requests.get(f"https://api.github.com/gists/{GIST_ID}",
                         headers=_headers(), timeout=10)
        if not r.ok:
            return []
        files = r.json().get("files", {})
        if FILENAME in files:
            return json.loads(files[FILENAME].get("content", "[]"))
    except Exception as e:
        print(f"[Gist] 거래 내역 읽기 실패: {e}")
    return []


def _write_trades(records: list):
    if not GIST_ID or not GH_TOKEN:
        print("[Gist] GIST_ID 또는 GH_TOKEN 미설정 — 거래 내역 저장 건너뜀")
        return False
    try:
        payload = {
            "files": {
                FILENAME: {"content": json.dumps(records, ensure_ascii=False, indent=2)}
            }
        }
        r = requests.patch(f"https://api.github.com/gists/{GIST_ID}",
                           headers=_headers(), json=payload, timeout=15)
        if r.ok:
            print(f"[Gist] 거래 내역 저장 완료 (총 {len(records)}건)")
            return True
        print(f"[Gist] 저장 실패 {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[Gist] 거래 내역 저장 예외: {e}")
    return False


def log_trade(
    ticker: str,
    name: str,
    trade_type: str,   # "buy" | "sell"
    price: int,
    qty: int,
    pnl: float = None,
    pnl_pct: float = None,
    reason: str = None,  # "손절" | "익절" | "기간청산" | None(buy)
    order_no: str = None,
):
    """거래 내역을 Gist에 추가합니다."""
    if not GIST_ID or not GH_TOKEN:
        return  # 미설정 시 조용히 건너뜀

    now = _now_kst()
    record = {
        "id":       int(now.timestamp() * 1000),
        "date":     now.strftime("%Y-%m-%d"),
        "time":     now.strftime("%H:%M"),
        "type":     trade_type,
        "ticker":   ticker,
        "name":     name,
        "price":    price,
        "qty":      qty,
        "amount":   price * qty,
        "pnl":      round(pnl, 0) if pnl is not None else None,
        "pnl_pct":  round(pnl_pct, 2) if pnl_pct is not None else None,
        "reason":   reason,
        "order_no": order_no,
    }

    records = _read_trades()
    records.insert(0, record)
    _write_trades(records[:MAX_HISTORY])
