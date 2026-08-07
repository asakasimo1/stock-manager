"""
초단타(스캘핑) 매매 판단 엔진 — 거래소 무관 공용 로직
job_scalp_coin.py / job_scalp_stock.py 에서 공용으로 사용.

가격 이력은 프로세스 메모리에만 보관한다 (데몬 재시작 시 리셋 — 상태 영속화 불필요).
포지션 상태(진입가/진입시각 등)는 호출하는 job 쪽에서 Gist에 저장한다.
"""
from __future__ import annotations

import time
from collections import deque

_price_hist: dict = {}  # ticker -> deque[(epoch_sec, price)]
_HIST_MAX_SEC = 180  # lookback_sec 상한보다 넉넉하게 보관


def record_price(ticker: str, price: float, now: float = None) -> None:
    now = now if now is not None else time.time()
    hist = _price_hist.setdefault(ticker, deque())
    hist.append((now, price))
    cutoff = now - _HIST_MAX_SEC
    while hist and hist[0][0] < cutoff:
        hist.popleft()


def momentum_pct(ticker: str, lookback_sec: float, now: float = None) -> float | None:
    """lookback_sec 이전 대비 현재까지의 가격 변화율(%). 관측 기간 부족 시 None."""
    now = now if now is not None else time.time()
    hist = _price_hist.get(ticker)
    if not hist or len(hist) < 2:
        return None

    cutoff = now - lookback_sec
    base_price = None
    for t, p in hist:
        if t <= cutoff:
            base_price = p
        else:
            break

    oldest_t = hist[0][0]
    if base_price is None:
        # lookback_sec 전 데이터가 없음 — 관측 기간이 너무 짧으면 판단 보류
        if now - oldest_t < lookback_sec * 0.5:
            return None
        base_price = hist[0][1]

    if base_price <= 0:
        return None
    cur_price = hist[-1][1]
    return (cur_price - base_price) / base_price * 100


def reset(ticker: str = None) -> None:
    if ticker:
        _price_hist.pop(ticker, None)
    else:
        _price_hist.clear()


def should_enter(momentum: float | None, today_chg_pct: float | None, params: dict) -> tuple[bool, str]:
    """
    params:
      entry_momentum_pct — 진입 모멘텀 임계 (기본 0.4%)
      max_day_chg_pct    — 당일 이미 이 이상 오른 종목은 추격 제외 (기본 5.0%)
    """
    entry_th = float(params.get("entry_momentum_pct", 0.4))
    max_day  = float(params.get("max_day_chg_pct", 5.0))

    if momentum is None:
        return False, "관측 데이터 부족"
    if today_chg_pct is not None and today_chg_pct > max_day:
        return False, f"당일 이미 +{today_chg_pct:.1f}% 과열 — 추격 제외"
    if momentum < entry_th:
        return False, f"모멘텀 {momentum:+.2f}% < 임계 {entry_th:.2f}%"
    return True, f"모멘텀 진입 {momentum:+.2f}%"


def select_auto_candidates(
    candidates: list,
    existing_tickers: set,
    max_day_chg_pct: float,
    min_liquidity: float,
    slots: int,
) -> list:
    """
    자동 종목 발굴 — 후보 목록에서 빈 슬롯 수만큼 선정 (거래소 무관 공용).
    candidates: [{"ticker", "name", "price", "chg_pct", "liquidity", ...}], 정렬 순서 그대로 우선순위로 사용
    existing_tickers: 이미 진행 중(watching/holding)인 티커 — 중복 진입 방지
    max_day_chg_pct: 이 값을 넘게 오른 종목은 이미 과열된 것으로 보고 제외 (should_enter와 동일 철학)
    min_liquidity: 이 값 미만인 종목은 슬리피지 우려로 제외 (코인: 24h 거래대금, 주식: 누적거래대금 근사치)
    slots: 이번에 새로 열 수 있는 최대 개수
    """
    if slots <= 0:
        return []
    picked = []
    for c in candidates:
        if len(picked) >= slots:
            break
        if c["ticker"] in existing_tickers:
            continue
        chg = c.get("chg_pct", 0)
        if chg <= 0 or chg > max_day_chg_pct:
            continue
        if c.get("liquidity", 0) < min_liquidity:
            continue
        picked.append(c)
    return picked


def should_give_up_watching(discovered_at: float, now: float, timeout_sec: float) -> bool:
    """자동발굴 watching 잡이 timeout_sec 동안 진입 조건을 못 채우면 포기 판단.
    discovered_at=0(구버전 잡 등 값 없음)이면 즉시 포기 — 슬롯이 무한정 묶이는 것을 방지."""
    if timeout_sec <= 0:
        return False
    return (now - discovered_at) >= timeout_sec


def prune_stale_auto_jobs(jobs: list, today: str) -> tuple:
    """전날 이전에 완료된 자동발굴 잡을 제거 (무한정 누적 방지). 오늘 완료된 건 UI 확인용으로 유지.
    반환: (정리된 jobs, 제거된 개수)"""
    kept = [
        j for j in jobs
        if not (j.get("source") == "auto" and j.get("status") == "done" and j.get("stats_date") != today)
    ]
    return kept, len(jobs) - len(kept)


def should_exit(
    entry_price: float,
    cur_price: float,
    entered_at: float,
    now: float,
    params: dict,
) -> tuple[bool, str]:
    """
    params:
      take_profit_pct — 익절 기준 (기본 0.6%)
      stop_loss_pct   — 손절 기준 (기본 0.4%, 양수로 입력)
      time_stop_sec   — 시간초과 강제 청산 (기본 180초) — 방치 후 손절 재발 방지 핵심 장치
    entry_price/cur_price는 이미 수수료를 반영한 값을 넘기는 것을 권장 (job 쪽에서 계산).
    """
    take_pct  = float(params.get("take_profit_pct", 0.6))
    stop_pct  = float(params.get("stop_loss_pct", 0.4))
    time_stop = float(params.get("time_stop_sec", 180))

    if entry_price <= 0:
        return False, ""

    chg_pct = (cur_price - entry_price) / entry_price * 100

    if chg_pct >= take_pct:
        return True, f"익절 (+{chg_pct:.2f}%)"
    if chg_pct <= -stop_pct:
        return True, f"손절 ({chg_pct:.2f}%)"
    if now - entered_at >= time_stop:
        return True, f"시간초과 청산 ({int(now - entered_at)}초 경과, {chg_pct:+.2f}%)"
    return False, ""
