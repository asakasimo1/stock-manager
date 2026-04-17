"""
GitHub Gist에 거래 내역을 기록하는 모듈
stock_analyzer 대시보드의 '거래 내역' 탭에 표시됩니다.

필요한 환경변수 (.env):
  GIST_ID   — 기록할 Gist ID (stock_analyzer와 동일한 Gist 사용)
  GH_TOKEN  — GitHub Personal Access Token (gist 권한 필요)
"""
import json
import os
import time
import logging
import requests
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

GIST_ID  = os.environ.get("GIST_ID", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")

FILENAME     = "trader_trades.json"
MAX_HISTORY  = 100   # 최대 보관 건수

# ─────────────────────────────────────────
# 공유 HTTP 세션 (TLS 연결 재활용)
# ─────────────────────────────────────────
_session = requests.Session()
_retry   = Retry(total=3, backoff_factor=0.3, status_forcelist=(500, 502, 503, 504))
_session.mount("https://", HTTPAdapter(max_retries=_retry, pool_connections=2, pool_maxsize=4))

# ─────────────────────────────────────────
# Gist 전체 응답 파일 캐시 (30초 TTL)
# 같은 Actions 러너 내 여러 python 프로세스가 공유
# ─────────────────────────────────────────
_GIST_CACHE_FILE = Path("/tmp/.gist_response_cache.json")
_GIST_CACHE_TTL  = 30  # seconds

# ─────────────────────────────────────────
# 거래 내역 버퍼 (프로세스 내 누적 후 flush_trades()로 일괄 저장)
# ─────────────────────────────────────────
_pending_trades: list = []


def _headers():
    return {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "stock-trader",
    }


def _now_kst() -> datetime:
    return datetime.now(_KST)


def _fetch_gist_raw() -> dict:
    """GitHub Gist 전체를 가져옴. 파일 캐시 우선 (30초 TTL)."""
    if _GIST_CACHE_FILE.exists():
        try:
            cached = json.loads(_GIST_CACHE_FILE.read_text())
            if time.time() - cached.get("_ts", 0) < _GIST_CACHE_TTL:
                logger.info("⏱  Gist 캐시 히트 (%.0fs 경과)", time.time() - cached["_ts"])
                return cached.get("data", {})
        except Exception:
            pass

    t0 = time.monotonic()
    r = _session.get(f"https://api.github.com/gists/{GIST_ID}",
                     headers=_headers(), timeout=10)
    ms = (time.monotonic() - t0) * 1000
    logger.info("⏱  GET  Gist %-40s %5.0fms  HTTP%s", GIST_ID[:12], ms, r.status_code)
    if not r.ok:
        logger.warning("Gist GET 실패 %s: %s", r.status_code, r.text[:200])
        return {}
    data = r.json()
    try:
        _GIST_CACHE_FILE.write_text(json.dumps({"_ts": time.time(), "data": data}))
    except Exception:
        pass
    return data


def _invalidate_gist_cache():
    """Gist 쓰기 후 캐시 무효화"""
    try:
        if _GIST_CACHE_FILE.exists():
            _GIST_CACHE_FILE.unlink()
    except Exception:
        pass


def _read_trades() -> list:
    if not GIST_ID or not GH_TOKEN:
        return []
    try:
        files = _fetch_gist_raw().get("files", {})
        if FILENAME in files:
            return json.loads(files[FILENAME].get("content", "[]"))
    except Exception as e:
        logger.warning("[Gist] 거래 내역 읽기 실패: %s", e)
    return []


def _write_trades(records: list):
    if not GIST_ID or not GH_TOKEN:
        logger.warning("[Gist] GIST_ID 또는 GH_TOKEN 미설정 — 거래 내역 저장 건너뜀")
        return False
    try:
        payload = {
            "files": {
                FILENAME: {"content": json.dumps(records, ensure_ascii=False, indent=2)}
            }
        }
        t0 = time.monotonic()
        r = _session.patch(f"https://api.github.com/gists/{GIST_ID}",
                           headers=_headers(), json=payload, timeout=15)
        ms = (time.monotonic() - t0) * 1000
        logger.info("⏱  PATCH Gist %-40s %5.0fms  HTTP%s", GIST_ID[:12], ms, r.status_code)
        if r.ok:
            logger.info("[Gist] 거래 내역 저장 완료 (총 %d건)", len(records))
            _invalidate_gist_cache()
            return True
        logger.warning("[Gist] 저장 실패 %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("[Gist] 거래 내역 저장 예외: %s", e)
    return False


def _read_gist_file(filename: str):
    """Gist에서 특정 파일 읽기 → 파싱된 JSON 반환
    - 파일 없음(미생성) → [] 반환
    - 인증 오류 / 네트워크 오류 → None 반환
    """
    if not GIST_ID or not GH_TOKEN:
        logger.warning("[Gist] GIST_ID 또는 GH_TOKEN 미설정")
        return None
    try:
        raw = _fetch_gist_raw()
        if not raw:
            return None
        files = raw.get("files", {})
        if filename not in files:
            logger.info("[Gist] '%s' 파일이 Gist에 없음 — 빈 목록 반환", filename)
            return []
        return json.loads(files[filename].get("content", "[]"))
    except Exception as e:
        logger.warning("[Gist] 파일 읽기 예외 (%s): %s", filename, e)
    return None


def _write_gist(files_dict: dict) -> bool:
    """임의 파일을 Gist에 저장. files_dict = {filename: python_object}"""
    if not GIST_ID or not GH_TOKEN:
        logger.warning("[Gist] GIST_ID 또는 GH_TOKEN 미설정 — 저장 건너뜀")
        return False
    try:
        payload = {
            "files": {
                name: {"content": json.dumps(data, ensure_ascii=False, indent=2)}
                for name, data in files_dict.items()
            }
        }
        t0 = time.monotonic()
        r = _session.patch(f"https://api.github.com/gists/{GIST_ID}",
                           headers=_headers(), json=payload, timeout=15)
        ms = (time.monotonic() - t0) * 1000
        logger.info("⏱  PATCH Gist %-40s %5.0fms  HTTP%s", list(files_dict.keys()), ms, r.status_code)
        if r.ok:
            logger.info("[Gist] 저장 완료: %s", list(files_dict.keys()))
            _invalidate_gist_cache()
            return True
        logger.warning("[Gist] 저장 실패 %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("[Gist] 저장 예외: %s", e)
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
    """거래 내역을 버퍼에 추가. flush_trades()를 호출해야 Gist에 저장됩니다."""
    if not GIST_ID or not GH_TOKEN:
        return

    now = _now_kst()
    _pending_trades.append({
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
    })
    logger.info("[Gist] 거래 버퍼에 추가: %s %s %d주 (버퍼 %d건)", trade_type, ticker, qty, len(_pending_trades))


def flush_trades():
    """버퍼에 쌓인 거래 내역을 Gist에 일괄 저장 (GET 1회 + PATCH 1회).
    거래가 없으면 아무것도 하지 않습니다."""
    if not _pending_trades:
        return
    count = len(_pending_trades)
    logger.info("[Gist] 거래 내역 %d건 일괄 저장 시작", count)
    records = _read_trades()
    for trade in reversed(_pending_trades):   # 최신 순 유지
        records.insert(0, trade)
    _write_trades(records[:MAX_HISTORY])
    _pending_trades.clear()
    logger.info("[Gist] flush_trades 완료 (%d건)", count)
