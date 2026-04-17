"""
Supabase 기반 트레이딩 상태 관리

positions  : 현재 보유 포지션
watchlist  : 오늘의 매수 후보
trading_meta: daily_pnl, initial_cash, bot_active
"""

import os
from datetime import date, datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_client: Client = None

KST = timezone(timedelta(hours=9))


def _today_kst() -> str:
    """KST 기준 오늘 날짜 (YYYY-MM-DD)"""
    return datetime.now(KST).strftime("%Y-%m-%d")


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
    rows = get_client().table("positions").select("ticker,name,buy_price,qty,tp,sl,buy_date").execute().data
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
    today = _today_kst()
    rows = (get_client().table("watchlist")
            .select("ticker,name,vol_ratio,day_return").eq("signal_date", today).execute().data)
    return [{"ticker":     r["ticker"],
             "name":       r["name"],
             "vol_ratio":  r.get("vol_ratio", 0),
             "day_return": r.get("day_return", 0)} for r in rows]


def set_watchlist(candidates: list):
    """오늘 watchlist 교체 + 7일 이전 오래된 데이터 정리"""
    today = _today_kst()
    cutoff = (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")
    client = get_client()
    client.table("watchlist").delete().lt("signal_date", cutoff).execute()  # 오래된 행 정리
    client.table("watchlist").delete().eq("signal_date", today).execute()   # 오늘 데이터 교체
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


def get_meta_multi(keys: list, defaults: dict = None) -> dict:
    """여러 키를 한 번의 쿼리로 조회 — get_meta() 다중 호출 대체"""
    rows = (get_client().table("trading_meta")
            .select("key,value").in_("key", keys).execute().data)
    result = {r["key"]: r["value"] for r in rows}
    if defaults:
        for k, v in defaults.items():
            result.setdefault(k, v)
    return result


def set_meta(key: str, value):
    get_client().table("trading_meta").upsert(
        {"key": key, "value": value}
    ).execute()


def set_meta_multi(data: dict):
    """여러 키를 한 번의 upsert로 저장 — set_meta() 다중 호출 대체"""
    rows = [{"key": k, "value": v} for k, v in data.items()]
    get_client().table("trading_meta").upsert(rows).execute()


# ─────────────────────────────────────────
# 팩터 포지션 (월간 리밸런싱 장기 보유)
# ─────────────────────────────────────────
def get_factor_positions() -> dict:
    rows = get_client().table("factor_positions").select(
        "ticker,name,buy_price,qty,buy_date,score,pbr,per,roe,momentum_12m"
    ).execute().data
    result = {}
    for r in rows:
        result[r["ticker"]] = {
            "name":         r["name"],
            "buy_price":    float(r["buy_price"]),
            "qty":          int(r["qty"]),
            "buy_date":     date.fromisoformat(r["buy_date"]),
            "score":        float(r.get("score") or 0),
            "pbr":          float(r.get("pbr") or 0),
            "per":          float(r.get("per") or 0),
            "roe":          float(r.get("roe") or 0),
            "momentum_12m": float(r.get("momentum_12m") or 0),
        }
    return result


def upsert_factor_position(ticker: str, pos: dict):
    get_client().table("factor_positions").upsert({
        "ticker":         ticker,
        "name":           pos.get("name", ""),
        "buy_price":      pos["buy_price"],
        "qty":            pos["qty"],
        "buy_date":       str(pos["buy_date"]),
        "score":          pos.get("score"),
        "pbr":            pos.get("pbr"),
        "per":            pos.get("per"),
        "roe":            pos.get("roe"),
        "momentum_12m":   pos.get("momentum_12m"),
    }).execute()


def delete_factor_position(ticker: str):
    get_client().table("factor_positions").delete().eq("ticker", ticker).execute()


def delete_factor_positions(tickers: list):
    """여러 팩터 포지션을 한 번의 DELETE로 제거"""
    if not tickers:
        return
    get_client().table("factor_positions").delete().in_("ticker", list(tickers)).execute()


def upsert_factor_positions(positions_data: list):
    """여러 팩터 포지션을 한 번의 upsert로 저장"""
    if not positions_data:
        return
    get_client().table("factor_positions").upsert(positions_data).execute()


# ─────────────────────────────────────────
# 팩터 월간 선정 종목
# ─────────────────────────────────────────
def set_factor_watchlist(candidates: list):
    today = _today_kst()
    get_client().table("factor_watchlist").delete().eq("rebalance_date", today).execute()
    if candidates:
        rows = [{
            "ticker":         c["ticker"],
            "name":           c.get("name", ""),
            "score":          float(c.get("score", 0)),
            "pbr":            float(c.get("pbr", 0)),
            "per":            float(c.get("per", 0)),
            "roe":            float(c.get("roe", 0)),
            "momentum_12m":   float(c.get("momentum_12m", 0)),
            "rebalance_date": today,
        } for c in candidates]
        get_client().table("factor_watchlist").insert(rows).execute()
