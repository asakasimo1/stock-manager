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

_volume_hist: dict = {}  # ticker -> deque[(epoch_sec, cumulative_volume)]
_VOL_HIST_MAX_SEC = 300  # 거래량 증가율 baseline 계산에 필요한 만큼 넉넉히 보관


def record_price(ticker: str, price: float, now: float = None) -> None:
    now = now if now is not None else time.time()
    hist = _price_hist.setdefault(ticker, deque())
    hist.append((now, price))
    cutoff = now - _HIST_MAX_SEC
    while hist and hist[0][0] < cutoff:
        hist.popleft()


def record_volume(ticker: str, cumulative_volume: float, now: float = None) -> None:
    """누적거래량(코인: 24h 누적, 주식: 당일 누적) 이력 기록 — 거래량 증가율 계산용"""
    now = now if now is not None else time.time()
    hist = _volume_hist.setdefault(ticker, deque())
    hist.append((now, cumulative_volume))
    cutoff = now - _VOL_HIST_MAX_SEC
    while hist and hist[0][0] < cutoff:
        hist.popleft()


def _rate_since(hist: deque, since_t: float, now: float, latest_v: float) -> float | None:
    """since_t 시점 이후 구간의 초당 증가율. since_t 이전 관측치가 없으면 None."""
    base_v = None
    for t, v in hist:
        if t <= since_t:
            base_v = v
        else:
            break
    if base_v is None:
        return None
    elapsed = max(1.0, now - since_t)
    return (latest_v - base_v) / elapsed


def volume_surge_ratio(ticker: str, recent_sec: float = 20, baseline_sec: float = 120, now: float = None) -> float | None:
    """
    최근(recent_sec) 거래량 속도 ÷ 직전 baseline_sec 동안의 거래량 속도.
    1.0보다 크면 거래량이 평소보다 늘어나고 있다는 뜻. 관측 기간이 baseline_sec에
    못 미치면(막 추적을 시작한 티커 등) 아직 판단할 수 없으므로 None.
    """
    now = now if now is not None else time.time()
    hist = _volume_hist.get(ticker)
    if not hist or len(hist) < 2:
        return None
    oldest_t = hist[0][0]
    if now - oldest_t < baseline_sec:
        return None

    latest_v = hist[-1][1]
    recent_rate = _rate_since(hist, now - recent_sec, now, latest_v)
    baseline_rate = _rate_since(hist, now - baseline_sec, now, latest_v)
    if recent_rate is None or baseline_rate is None or baseline_rate <= 0:
        return None
    return recent_rate / baseline_rate


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


def fmt_opt(val: float | None, fmt: str = "{:+.2f}") -> str:
    """None-safe 로그 포맷팅 — momentum/volume_surge 등 관측 데이터 부족 시 None일 수 있는 값용"""
    return "N/A" if val is None else fmt.format(val)


def reset(ticker: str = None) -> None:
    if ticker:
        _price_hist.pop(ticker, None)
        _volume_hist.pop(ticker, None)
    else:
        _price_hist.clear()
        _volume_hist.clear()


def should_enter(
    momentum: float | None,
    today_chg_pct: float | None,
    params: dict,
    volume_surge: float | None = None,
) -> tuple[bool, str]:
    """
    params:
      entry_momentum_pct    — 진입 모멘텀 임계 (기본 0.4%)
      max_day_chg_pct       — 당일 이미 이 이상 오른 종목은 추격 제외 (기본 5.0%)
      min_volume_surge_ratio — 0보다 크면 거래량 증가 조건도 함께 검사 (기본 0=미검사, 수동 잡 하위호환)
    volume_surge: scalp_engine.volume_surge_ratio() 결과 — min_volume_surge_ratio 검사 시에만 사용
    """
    entry_th = float(params.get("entry_momentum_pct", 0.4))
    max_day  = float(params.get("max_day_chg_pct", 5.0))
    min_vol_surge = float(params.get("min_volume_surge_ratio", 0) or 0)

    if momentum is None:
        return False, "관측 데이터 부족"
    if today_chg_pct is not None and today_chg_pct > max_day:
        return False, f"당일 이미 +{today_chg_pct:.1f}% 과열 — 추격 제외"
    if momentum < entry_th:
        return False, f"모멘텀 {momentum:+.2f}% < 임계 {entry_th:.2f}%"
    if min_vol_surge > 0:
        if volume_surge is None:
            return False, "거래량 데이터 부족"
        if volume_surge < min_vol_surge:
            return False, f"거래량 증가 부족 ({volume_surge:.2f}배 < 임계 {min_vol_surge:.2f}배)"
    return True, f"모멘텀 진입 {momentum:+.2f}%" + (f" · 거래량 {volume_surge:.2f}배" if min_vol_surge > 0 else "")


def select_auto_candidates(
    candidates: list,
    existing_tickers: set,
    max_day_chg_pct: float,
    min_liquidity: float,
    slots: int,
    min_momentum_pct: float = 0.0,
    min_volume_surge: float = 0.0,
) -> list:
    """
    자동 종목 발굴 — 후보 목록에서 빈 슬롯 수만큼 선정 (거래소 무관 공용).
    candidates: [{"ticker", "name", "price", "chg_pct", "liquidity", "momentum", "volume_surge", ...}]
                호출부에서 momentum 내림차순으로 정렬해서 넘기는 것을 권장 (급등 우선순위)
    existing_tickers: 이미 진행 중(watching/holding)인 티커 — 중복 진입 방지
    max_day_chg_pct: 이 값을 넘게 오른 종목은 이미 과열된 것으로 보고 제외 (should_enter와 동일 철학)
    min_liquidity: 이 값 미만인 종목은 슬리피지 우려로 제외 (코인: 24h 거래대금, 주식: 누적거래대금 근사치)
    slots: 이번에 새로 열 수 있는 최대 개수
    min_momentum_pct: 0보다 크면 "갑자기 급등" 조건 — 최근 모멘텀(candidate["momentum"])이
                       이 값 이상이어야 후보 인정 (관측 데이터 없으면 제외)
    min_volume_surge: 0보다 크면 "거래량 증가" 조건 — candidate["volume_surge"]가
                       이 값 이상이어야 후보 인정 (관측 데이터 없으면 제외)
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
        if min_momentum_pct > 0:
            m = c.get("momentum")
            if m is None or m < min_momentum_pct:
                continue
        if min_volume_surge > 0:
            vs = c.get("volume_surge")
            if vs is None or vs < min_volume_surge:
                continue
        picked.append(c)
    return picked


def select_reversal_candidates(
    candidates: list,
    existing_tickers: set,
    min_liquidity: float,
    slots: int,
    min_decline_pct: float,
    min_rebound_pct: float,
) -> list:
    """
    급락 후 반등 후보 선정 — "빠르게 급락하다가 급 양전"하는 대상을 잡기 위한
    select_auto_candidates의 반대 방향 버전.
    candidates: [{"ticker", "name", "liquidity", "decline", "rebound", ...}]
      decline: decline_lookback_sec 동안의 가격변화율(%) — 하락 중이면 음수
      rebound: rebound_lookback_sec 동안의 가격변화율(%) — 반등 중이면 양수
    min_decline_pct/min_rebound_pct: 둘 다 양수로 입력 (부호는 내부에서 처리)
      예: min_decline_pct=2.0 → decline이 -2.0% 이하(더 많이 하락)여야 통과
          min_rebound_pct=0.4 → rebound이 +0.4% 이상이어야 통과
    당일 등락률 상한(max_day_chg_pct) 필터는 적용하지 않음 — 반등 후보는 보통
    당일 기준으로도 마이너스이거나 미미해서 "추격 과열" 개념 자체가 해당 없음.
    """
    if slots <= 0:
        return []
    picked = []
    for c in candidates:
        if len(picked) >= slots:
            break
        if c["ticker"] in existing_tickers:
            continue
        if c.get("liquidity", 0) < min_liquidity:
            continue
        decline = c.get("decline")
        rebound = c.get("rebound")
        if decline is None or rebound is None:
            continue
        if decline > -min_decline_pct:
            continue
        if rebound < min_rebound_pct:
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
      take_profit_pct   — 익절 기준 (기본 0.6%)
      stop_loss_pct     — 손절 기준 (기본 0.4%, 양수로 입력) — 시간과 무관하게 항상 적용
      time_stop_sec     — 시간초과 판단 시점 (기본 180초)
      time_stop_loss_pct — 시간초과 시점에 이 손실률(양수 입력)을 넘겼을 때만 청산 (기본 0.5%)
                           그 안이면(수익이거나 손실이 -0.5% 이내면) 무조건 청산하지 않고 계속 보유
                           — "3분 지났다고 무조건 손절"하던 것을 완화, 시간초과=거의 항상 수수료손실
                             패턴(승률 저하 요인)을 줄이기 위함
    entry_price/cur_price는 이미 수수료를 반영한 값을 넘기는 것을 권장 (job 쪽에서 계산).
    """
    take_pct           = float(params.get("take_profit_pct", 0.6))
    stop_pct           = float(params.get("stop_loss_pct", 0.4))
    time_stop          = float(params.get("time_stop_sec", 180))
    time_stop_loss_pct = float(params.get("time_stop_loss_pct", 0.5))

    if entry_price <= 0:
        return False, ""

    chg_pct = (cur_price - entry_price) / entry_price * 100

    if chg_pct >= take_pct:
        return True, f"익절 (+{chg_pct:.2f}%)"
    if chg_pct <= -stop_pct:
        return True, f"손절 ({chg_pct:.2f}%)"
    if now - entered_at >= time_stop:
        if chg_pct <= -time_stop_loss_pct:
            return True, f"시간초과 손절 ({int(now - entered_at)}초 경과, {chg_pct:+.2f}%)"
        return False, ""  # 시간은 지났지만 손실이 크지 않음 — 청산하지 않고 계속 관찰
    return False, ""
