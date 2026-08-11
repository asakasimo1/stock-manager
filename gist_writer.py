"""
GitHub Gist에 거래 내역을 기록하는 모듈
stock_analyzer 대시보드의 '거래 내역' 탭에 표시됩니다.

필요한 환경변수 (.env):
  GIST_ID   — 기록할 Gist ID (stock_analyzer와 동일한 Gist 사용)
  GH_TOKEN  — GitHub Personal Access Token (gist 권한 필요)
"""
import json
import os
import sys
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
# Gist 쓰기 한도(403) 소진 중에 데몬이 재시작되면 메모리에만 있던 버퍼가
# 통째로 날아가는 문제(2026-08-11 실측)가 있어서, append할 때마다 디스크에도
# 같이 써서 재시작해도 복구되게 함. 데몬(stock/coin/scalp)마다 별도 프로세스라
# 파일명에 실행 스크립트 이름을 넣어 서로 덮어쓰지 않게 분리한다.
# ─────────────────────────────────────────
_pending_trades: list = []

def _pending_buffer_file() -> Path:
    try:
        stem = Path(sys.argv[0]).stem or "unknown"
    except Exception:
        stem = "unknown"
    return Path(f"/tmp/.gist_pending_trades_{stem}.json")

_PENDING_TRADES_FILE = _pending_buffer_file()


def _save_pending_trades():
    try:
        _PENDING_TRADES_FILE.write_text(
            json.dumps(_pending_trades, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("[Gist] 대기 버퍼 파일 저장 실패: %s", e)


def _load_pending_trades():
    """프로세스 시작 시, 이전 실행에서 Gist 저장에 끝내 실패해 남아있던 버퍼를 복구."""
    if not _PENDING_TRADES_FILE.exists():
        return
    try:
        data = json.loads(_PENDING_TRADES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            _pending_trades.extend(data)
            logger.info("[Gist] 이전 세션에서 저장 못한 거래 버퍼 %d건 복구", len(data))
    except Exception as e:
        logger.warning("[Gist] 대기 버퍼 파일 읽기 실패: %s", e)


_load_pending_trades()


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
    # _write_gist()의 409(동시쓰기 충돌) 재시도 로직을 그대로 재사용 — 예전엔 이 함수가
    # 별도로 PATCH를 직접 호출해서 재시도가 없었고, 그 때문에 스캘핑/그리드 등 여러
    # 데몬이 동시에 Gist에 쓰는 순간 매도 체결 기록(trader_trades.json)이 조용히
    # 유실돼 대시보드 "시스템 트레이딩 내역"에서 이미 판 종목이 계속 "보유중"으로
    # 잘못 나오는 원인이 됐음(2026-08-11 다수 종목 실측).
    ok = _write_gist({FILENAME: records})
    if ok:
        logger.info("[Gist] 거래 내역 저장 완료 (총 %d건)", len(records))
    return ok


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
    """임의 파일을 Gist에 저장. files_dict = {filename: python_object}
    stock/coin/scalp 데몬 3개가 같은 Gist에 동시에 쓰다 보면 GitHub이 "Gist cannot
    be updated"(HTTP 409, 동시쓰기 충돌)로 거절하는 경우가 있음 — 이때 재시도 없이
    그냥 실패 처리하면, 매도 체결 등으로 이미 바뀐 잡 상태(phase/buy_qty 등)가
    Gist엔 저장되지 않은 채 유실돼서 "매도됐는데 화면엔 계속 보유중"인 유령
    포지션이 생김(2026-08-10 대동스틸 048470 실측). 409는 대개 순간적인 충돌이라
    짧게 대기 후 재시도하면 대부분 성공하므로, 여기서 최대 3회 재시도한다."""
    if not GIST_ID or not GH_TOKEN:
        logger.warning("[Gist] GIST_ID 또는 GH_TOKEN 미설정 — 저장 건너뜀")
        return False
    payload = {
        "files": {
            name: {"content": json.dumps(data, ensure_ascii=False, indent=2)}
            for name, data in files_dict.items()
        }
    }
    for attempt in range(3):
        try:
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
            if r.status_code == 409 and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
        except Exception as e:
            logger.warning("[Gist] 저장 예외: %s", e)
        break
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
    trade_date: str = None,   # "YYYY-MM-DD" — 미지정 시 현재시각(기존 동작)
    trade_time: str = None,   # "HH:MM" — 미지정 시 현재시각(기존 동작)
):
    """거래 내역을 버퍼에 추가. flush_trades()를 호출해야 Gist에 저장됩니다.
    trade_date/trade_time: 체결 통합기록(reconcile) 등 "지금 막 알게 됐지만 실제
    체결은 과거"인 경우, 실제 체결시각을 넘겨야 함 — 안 넘기면 기록 시점(현재시각)이
    찍혀서 나중에 조회할 때 실제와 다른 시각으로 보임(2026-08-10 실측: 09시대 체결이
    12시대 거래로 잘못 표시됨)."""
    if not GIST_ID or not GH_TOKEN:
        return

    now = _now_kst()
    _pending_trades.append({
        "id":       int(now.timestamp() * 1000),
        "date":     trade_date or now.strftime("%Y-%m-%d"),
        "time":     trade_time or now.strftime("%H:%M"),
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
    _save_pending_trades()
    logger.info("[Gist] 거래 버퍼에 추가: %s %s %d주 (버퍼 %d건)", trade_type, ticker, qty, len(_pending_trades))


def flush_trades():
    """버퍼에 쌓인 거래 내역을 Gist에 일괄 저장 (GET 1회 + PATCH 1회).
    거래가 없으면 아무것도 하지 않습니다.
    저장이 끝내 실패하면(재시도 3회 소진) 버퍼를 비우지 않고 남겨둬서 다음
    flush_trades() 호출(다음 폴링 사이클) 때 다시 시도함 — 여기서 무조건
    clear()하면 매도 체결 기록 자체가 통째로 유실돼 버림."""
    if not _pending_trades:
        return
    count = len(_pending_trades)
    logger.info("[Gist] 거래 내역 %d건 일괄 저장 시작", count)
    records = _read_trades()
    for trade in reversed(_pending_trades):   # 최신 순 유지
        records.insert(0, trade)
    ok = _write_trades(records[:MAX_HISTORY])
    if ok:
        _pending_trades.clear()
        _save_pending_trades()  # 디스크 버퍼 파일도 비움 — 다음 시작 시 잘못 복구되지 않도록
        logger.info("[Gist] flush_trades 완료 (%d건)", count)
    else:
        logger.error("[Gist] 거래 내역 저장 실패 — 버퍼 유지(디스크에도 보존됨), 다음 사이클에 재시도 (%d건)", count)
