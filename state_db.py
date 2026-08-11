"""
GitHub Gist 기반 트레이딩 상태 관리 (2026-08-11 Supabase → Gist 전환)

positions       : 현재 보유 포지션
watchlist       : 오늘의 매수 후보
trading_meta    : daily_pnl, initial_cash, bot_active
factor_positions/factor_watchlist : 월간 팩터 리밸런싱 전용

기존에는 Supabase(PostgreSQL)를 썼으나 프로젝트 자체가 DNS에서 사라져(완전 소멸)
report.yml 등이 매일 크래시했음. 이 시스템의 다른 모든 상태(계좌 잔액/거래내역/매매
잡)가 이미 같은 GitHub Gist를 공유 저장소로 쓰고 있어서, 별도 인프라 없이 gist_writer의
기존 재시도/캐시 로직을 그대로 재사용할 수 있는 Gist로 옮김. 함수 시그니처는 기존과
동일하게 유지해 호출부(job_balance.py, job_buy.py, job_close.py, job_signals.py,
job_factor_rebalance.py, job_report.py)는 수정 불필요.
"""

from datetime import date, datetime, timezone, timedelta

import gist_writer

KST = timezone(timedelta(hours=9))

_POSITIONS_FILE        = "state_positions.json"
_WATCHLIST_FILE         = "state_watchlist.json"
_META_FILE              = "state_meta.json"
_FACTOR_POSITIONS_FILE  = "state_factor_positions.json"
_FACTOR_WATCHLIST_FILE  = "state_factor_watchlist.json"


def _today_kst() -> str:
    """KST 기준 오늘 날짜 (YYYY-MM-DD)"""
    return datetime.now(KST).strftime("%Y-%m-%d")


# ─────────────────────────────────────────
# 포지션
# ─────────────────────────────────────────
def get_positions() -> dict:
    """현재 보유 포지션 반환 {ticker: {name, buy_price, qty, tp, sl, buy_date}}"""
    rows = gist_writer._read_gist_file(_POSITIONS_FILE) or []
    result = {}
    for r in rows:
        result[r["ticker"]] = {
            "name":      r.get("name", ""),
            "buy_price": float(r["buy_price"]),
            "qty":       int(r["qty"]),
            "tp":        float(r["tp"]),
            "sl":        float(r["sl"]),
            "buy_date":  date.fromisoformat(r["buy_date"]),
        }
    return result


def upsert_position(ticker: str, pos: dict):
    rows = gist_writer._read_gist_file(_POSITIONS_FILE) or []
    rows = [r for r in rows if r.get("ticker") != ticker]
    rows.append({
        "ticker":    ticker,
        "name":      pos.get("name", ""),
        "buy_price": pos["buy_price"],
        "qty":       pos["qty"],
        "tp":        pos["tp"],
        "sl":        pos["sl"],
        "buy_date":  str(pos["buy_date"]),
    })
    gist_writer._write_gist({_POSITIONS_FILE: rows})


def delete_position(ticker: str):
    rows = gist_writer._read_gist_file(_POSITIONS_FILE) or []
    rows = [r for r in rows if r.get("ticker") != ticker]
    gist_writer._write_gist({_POSITIONS_FILE: rows})


# ─────────────────────────────────────────
# 관심종목 (watchlist)
# ─────────────────────────────────────────
def get_watchlist() -> list:
    """오늘 날짜 매수 후보 반환"""
    today = _today_kst()
    rows = gist_writer._read_gist_file(_WATCHLIST_FILE) or []
    return [{"ticker":     r["ticker"],
             "name":       r["name"],
             "vol_ratio":  r.get("vol_ratio", 0),
             "day_return": r.get("day_return", 0)}
            for r in rows if r.get("signal_date") == today]


def set_watchlist(candidates: list):
    """오늘 watchlist 교체 + 7일 이전 오래된 데이터 정리"""
    today = _today_kst()
    cutoff = (datetime.now(KST) - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = gist_writer._read_gist_file(_WATCHLIST_FILE) or []
    rows = [r for r in rows if r.get("signal_date", "") >= cutoff and r.get("signal_date") != today]
    for c in candidates:
        rows.append({
            "ticker":      c["ticker"],
            "name":        c.get("name", ""),
            "vol_ratio":   c.get("vol_ratio", 0),
            "day_return":  c.get("day_return", 0),
            "signal_date": today,
        })
    gist_writer._write_gist({_WATCHLIST_FILE: rows})


# ─────────────────────────────────────────
# 메타 (daily_pnl, initial_cash, bot_active)
# ─────────────────────────────────────────
def get_meta(key: str, default=None):
    data = gist_writer._read_gist_file(_META_FILE)
    if not isinstance(data, dict):
        return default
    return data.get(key, default)


def get_meta_multi(keys: list, defaults: dict = None) -> dict:
    """여러 키를 한 번의 조회로 반환 — get_meta() 다중 호출 대체"""
    data = gist_writer._read_gist_file(_META_FILE)
    if not isinstance(data, dict):
        data = {}
    result = {k: data[k] for k in keys if k in data}
    if defaults:
        for k, v in defaults.items():
            result.setdefault(k, v)
    return result


def set_meta(key: str, value):
    data = gist_writer._read_gist_file(_META_FILE)
    if not isinstance(data, dict):
        data = {}
    data[key] = value
    gist_writer._write_gist({_META_FILE: data})


def set_meta_multi(data_in: dict):
    """여러 키를 한 번의 저장으로 반영 — set_meta() 다중 호출 대체"""
    data = gist_writer._read_gist_file(_META_FILE)
    if not isinstance(data, dict):
        data = {}
    data.update(data_in)
    gist_writer._write_gist({_META_FILE: data})


# ─────────────────────────────────────────
# 팩터 포지션 (월간 리밸런싱 장기 보유)
# ─────────────────────────────────────────
def get_factor_positions() -> dict:
    rows = gist_writer._read_gist_file(_FACTOR_POSITIONS_FILE) or []
    result = {}
    for r in rows:
        result[r["ticker"]] = {
            "name":         r.get("name", ""),
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
    rows = gist_writer._read_gist_file(_FACTOR_POSITIONS_FILE) or []
    rows = [r for r in rows if r.get("ticker") != ticker]
    rows.append({
        "ticker":       ticker,
        "name":         pos.get("name", ""),
        "buy_price":    pos["buy_price"],
        "qty":          pos["qty"],
        "buy_date":     str(pos["buy_date"]),
        "score":        pos.get("score"),
        "pbr":          pos.get("pbr"),
        "per":          pos.get("per"),
        "roe":          pos.get("roe"),
        "momentum_12m": pos.get("momentum_12m"),
    })
    gist_writer._write_gist({_FACTOR_POSITIONS_FILE: rows})


def delete_factor_position(ticker: str):
    delete_factor_positions([ticker])


def delete_factor_positions(tickers: list):
    """여러 팩터 포지션을 한 번의 저장으로 제거"""
    if not tickers:
        return
    rows = gist_writer._read_gist_file(_FACTOR_POSITIONS_FILE) or []
    drop = set(tickers)
    rows = [r for r in rows if r.get("ticker") not in drop]
    gist_writer._write_gist({_FACTOR_POSITIONS_FILE: rows})


def upsert_factor_positions(positions_data: list):
    """여러 팩터 포지션을 한 번의 저장으로 반영 (positions_data 각 항목에 ticker 포함)"""
    if not positions_data:
        return
    rows = gist_writer._read_gist_file(_FACTOR_POSITIONS_FILE) or []
    by_ticker = {r["ticker"]: r for r in rows if r.get("ticker")}
    for p in positions_data:
        row = dict(p)
        row["buy_date"] = str(row.get("buy_date", ""))
        by_ticker[row["ticker"]] = row
    gist_writer._write_gist({_FACTOR_POSITIONS_FILE: list(by_ticker.values())})


# ─────────────────────────────────────────
# 팩터 월간 선정 종목
# ─────────────────────────────────────────
def set_factor_watchlist(candidates: list):
    today = _today_kst()
    rows = gist_writer._read_gist_file(_FACTOR_WATCHLIST_FILE) or []
    rows = [r for r in rows if r.get("rebalance_date") != today]
    for c in candidates:
        rows.append({
            "ticker":         c["ticker"],
            "name":           c.get("name", ""),
            "score":          float(c.get("score", 0)),
            "pbr":            float(c.get("pbr", 0)),
            "per":            float(c.get("per", 0)),
            "roe":            float(c.get("roe", 0)),
            "momentum_12m":   float(c.get("momentum_12m", 0)),
            "rebalance_date": today,
        })
    gist_writer._write_gist({_FACTOR_WATCHLIST_FILE: rows})
