"""
Supabase 기반 트레이딩 상태 관리

positions  : 현재 보유 포지션
watchlist  : 오늘의 매수 후보
trading_meta: daily_pnl, initial_cash, bot_active
"""

import os
from datetime import date
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_client: Client = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if not url or not key:
            raise RuntimeError(".env에 SUPABASE_URL, SUPABASE_KEY를 설정하세요.")
        _client = create_client(url, key)
    return _client


# ─────────────────────────────────────────
# 포지션
# ─────────────────────────────────────────
def get_positions() -> dict:
    """현재 보유 포지션 반환 {ticker: {name, buy_price, qty, tp, sl, buy_date}}"""
    rows = get_client().table("positions").select("*").execute().data
    result = {}
    for r in rows:
        result[r["ticker"]] = {
            "name":      r["name"],
            "buy_price": float(r["buy_price"]),
            "qty":       int(r["qty"]),
            "tp":        float(r["tp"]),
            "sl":        float(r["sl"]),
            "buy_date":  date.fromisoformat(r["buy_date"]),
        }
    return result


def upsert_position(ticker: str, pos: dict):
    get_client().table("positions").upsert({
        "ticker":    ticker,
        "name":      pos.get("name", ""),
        "buy_price": pos["buy_price"],
        "qty":       pos["qty"],
        "tp":        pos["tp"],
        "sl":        pos["sl"],
        "buy_date":  str(pos["buy_date"]),
    }).execute()


def delete_position(ticker: str):
    get_client().table("positions").delete().eq("ticker", ticker).execute()


# ─────────────────────────────────────────
# 관심종목 (watchlist)
# ─────────────────────────────────────────
def get_watchlist() -> list:
    """오늘 날짜 매수 후보 반환"""
    today = str(date.today())
    rows = (get_client().table("watchlist")
            .select("*").eq("signal_date", today).execute().data)
    return [{"ticker":     r["ticker"],
             "name":       r["name"],
             "vol_ratio":  r.get("vol_ratio", 0),
             "day_return": r.get("day_return", 0)} for r in rows]


def set_watchlist(candidates: list):
    """오늘 watchlist 교체"""
    today = str(date.today())
    get_client().table("watchlist").delete().eq("signal_date", today).execute()
    if candidates:
        rows = [{"ticker":     c["ticker"],
                 "name":       c.get("name", ""),
                 "vol_ratio":  c.get("vol_ratio", 0),
                 "day_return": c.get("day_return", 0),
                 "signal_date": today} for c in candidates]
        get_client().table("watchlist").insert(rows).execute()


# ─────────────────────────────────────────
# 메타 (daily_pnl, initial_cash, bot_active)
# ─────────────────────────────────────────
def get_meta(key: str, default=None):
    rows = get_client().table("trading_meta").select("value").eq("key", key).execute().data
    return rows[0]["value"] if rows else default


def set_meta(key: str, value):
    get_client().table("trading_meta").upsert(
        {"key": key, "value": value}
    ).execute()
