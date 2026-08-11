"""
Supabase 기반 트레이딩 상태 관리

positions  : 현재 보유 포지션
watchlist  : 오늘의 매수 후보
trading_meta: daily_pnl, initial_cash, bot_active
"""

import os
import logging
from datetime import date, datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
logger = logging.getLogger(__name__)

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
# Supabase 프로젝트가 죽어있거나(DNS 소멸 등) 미설정이어도 리포트 등 메타 조회/저장이
# 실패하면 안 되므로(2026-08-11 report.yml 크래시 실측 — Supabase 프로젝트 자체가
# 없어짐), 메타는 best-effort로 처리하고 실패 시 기본값 반환/조용히 무시한다.
# positions/watchlist/factor_positions는 실제 매매 판단에 쓰이므로 여기 대상에서
# 제외 — 잘못 비어있는 값을 반환하면 팩터 리밸런싱 등이 중복매수할 위험이 있음.
# ─────────────────────────────────────────
def get_meta(key: str, default=None):
    try:
        rows = get_client().table("trading_meta").select("value").eq("key", key).execute().data
        return rows[0]["value"] if rows else default
    except Exception as e:
        logger.warning("[state_db] get_meta(%s) 실패 — 기본값 사용: %s", key, e)
        return default


def get_meta_multi(keys: list, defaults: dict = None) -> dict:
    """여러 키를 한 번의 쿼리로 조회 — get_meta() 다중 호출 대체"""
    try:
        rows = (get_client().table("trading_meta")
                .select("key,value").in_("key", keys).execute().data)
        result = {r["key"]: r["value"] for r in rows}
    except Exception as e:
        logger.warning("[state_db] get_meta_multi(%s) 실패 — 기본값 사용: %s", keys, e)
        result = {}
    if defaults:
        for k, v in defaults.items():
            result.setdefault(k, v)
    return result


def set_meta(key: str, value):
    try:
        get_client().table("trading_meta").upsert(
            {"key": key, "value": value}
        ).execute()
    except Exception as e:
        logger.warning("[state_db] set_meta(%s) 실패 — 무시: %s", key, e)


def set_meta_multi(data: dict):
    """여러 키를 한 번의 upsert로 저장 — set_meta() 다중 호출 대체"""
    try:
        rows = [{"key": k, "value": v} for k, v in data.items()]
        get_client().table("trading_meta").upsert(rows).execute()
    except Exception as e:
        logger.warning("[state_db] set_meta_multi(%s) 실패 — 무시: %s", list(data.keys()), e)


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
