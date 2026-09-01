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

_entry_confirm: dict = {}  # ticker -> 모멘텀 조건을 연속으로 만족한 횟수 (진입 확인용)

_peak_pnl: dict = {}  # ticker -> 현재 보유 포지션 진입 이후 관측된 최고 손익률(%) — 트레일링 익절용


def update_peak_pnl(ticker: str, chg_pct: float) -> float:
    """현재 손익률을 반영해 이 포지션의 고점 손익률을 갱신하고 반환. 프로세스 메모리에만 보관
    (Gist에 매 사이클 쓰지 않기 위함 — 데몬 재시작 시 리셋되지만 보유 중 재시작은 드묾)."""
    peak = max(_peak_pnl.get(ticker, chg_pct), chg_pct)
    _peak_pnl[ticker] = peak
    return peak


def clear_peak_pnl(ticker: str) -> None:
    _peak_pnl.pop(ticker, None)


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


def recent_volume_abs(ticker: str, window_sec: float = 120, now: float = None) -> float | None:
    """최근 window_sec 동안 실제로 체결된 거래량(절대값, 비율 아님).
    극단적 거래량 급증 판정 시 volume_surge_ratio()의 상대 비율만으로는 원래 거래가
    거의 없던 종목의 오탐(2주→10주도 "5배")을 못 걸러서, 10일 평균 거래량 기반 절대
    기준치와 비교하는 용도로 추가(2026-08-13 사용자 요청)."""
    now = now if now is not None else time.time()
    hist = _volume_hist.get(ticker)
    if not hist or len(hist) < 2:
        return None
    latest_v = hist[-1][1]
    cutoff = now - window_sec
    base_v = None
    for t, v in hist:
        if t <= cutoff:
            base_v = v
        else:
            break
    if base_v is None:
        return None
    return latest_v - base_v


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


def price_tick_pct(price: float, tick_size: float) -> float:
    """호가 1틱이 가격 대비 차지하는 비율(%)"""
    if price is None or tick_size is None or price <= 0 or tick_size <= 0:
        return 0.0
    return tick_size / price * 100


def tick_aware_floor(price: float, tick_size: float, configured_pct: float, min_ticks: float = 2.5) -> float:
    """설정된 %가 해당 가격대의 호가 노이즈(min_ticks 틱)보다 작으면 노이즈 기준으로 자동 상향.
    저가 코인/종목은 1틱만 움직여도 %가 크게 튀어, 모멘텀 진입·손절 조건이 실제 추세가 아니라
    호가 단위 자체에 의해 충족/발동되는 문제(예: 130원짜리 코인은 1틱=0.77%로 손절선 0.5%를
    단 1틱 하락만으로 넘겨버림)를 막기 위함. price/tick_size 정보가 없으면 설정값 그대로 사용."""
    floor = min_ticks * price_tick_pct(price, tick_size)
    return max(configured_pct, floor)


def fmt_opt(val: float | None, fmt: str = "{:+.2f}") -> str:
    """None-safe 로그 포맷팅 — momentum/volume_surge 등 관측 데이터 부족 시 None일 수 있는 값용"""
    return "N/A" if val is None else fmt.format(val)


def is_phantom_position_error(e: Exception) -> bool:
    """매도 주문 실패가 '실제로는 보유하고 있지 않은 수량'이라서 거부된 것인지 판별.
    잡 내부 상태(phase=holding)와 실제 거래소 잔고가 어긋나 있으면(다른 시스템이
    이미 팔았거나 체결이 잘못 기록된 경우 등) should_exit()가 계속 True를 반환하고
    같은 매도 주문이 영원히 실패하는 무한 재시도 루프가 됨 — 2026-08-11~12일 실측:
    148780(비큐AI) 6286회, 226320(잇츠한불) 4432회 연속 실패, KRW-CHZ 등 코인도 동일
    패턴 확인. 이 에러 유형이면 호출부가 재시도 대신 내부 상태를 즉시 정리해야 한다."""
    msg = str(e)
    return (
        "주문 가능한 수량을 초과" in msg      # KIS(주식): 보유 수량보다 많이 팔려 함
        or "insufficient_funds_ask" in msg    # Upbit(코인): 매도 가능 잔고 부족 에러 코드
        or ("주문 가능한 금액" in msg and "부족" in msg)  # Upbit 에러 메시지 문구
    )


def reset(ticker: str = None) -> None:
    if ticker:
        _price_hist.pop(ticker, None)
        _volume_hist.pop(ticker, None)
        _entry_confirm.pop(ticker, None)
        _peak_pnl.pop(ticker, None)
    else:
        _price_hist.clear()
        _volume_hist.clear()
        _entry_confirm.clear()
        _peak_pnl.clear()


def should_enter(
    momentum: float | None,
    today_chg_pct: float | None,
    params: dict,
    volume_surge: float | None = None,
    cur_price: float = 0.0,
    tick_size: float = 0.0,
    ticker: str | None = None,
    extreme_volume_baseline_2min: float | None = None,
) -> tuple[bool, str]:
    """
    params:
      entry_momentum_pct    — 진입 모멘텀 임계 (기본 0.4%)
      max_day_chg_pct       — 당일 이미 이 이상 오른 종목은 추격 제외 (기본 5.0%)
      min_volume_surge_ratio — 0보다 크면 거래량 증가 조건도 함께 검사 (기본 0=미검사, 수동 잡 하위호환)
      entry_confirm_cycles   — 모멘텀 조건을 연속 몇 회 만족해야 진입할지 (기본 2, 1이면 기존처럼 즉시 진입)
                               찰나의 스파이크가 정점을 찍는 순간 바로 사서 직후 반락하는 것을 방지
      strong_signal_multiplier — 모멘텀·거래량 둘 다 각자 임계치의 이 배수(기본 2.0배) 이상이면
                               "이미 충분히 강한 신호"로 보고 확인 절차 없이 즉시 진입.
                               애매한 신호만 confirm_cycles로 걸러내고, 확실한 신호는 놓치지 않기 위함
      extreme_volume_surge_ratio — 0보다 크면, volume_surge(비율)가 이 배수 이상일 때 모멘텀
                               확인 절차(confirm_cycles)를 생략하고 즉시 진입 (기본 0=비활성).
                               발굴 룩백(60초)·확인절차는 그대로 두되, "거래량이 매우 급증하는
                               경우"만 예외로 빠르게 태우기 위함(2026-08-13 사용자 요청).
                               extreme_volume_baseline_2min이 함께 주어지면, 최근 2분간
                               실제(절대) 거래량도 그 기준치의 같은 배수 이상이어야 통과 —
                               원래 거래가 거의 없던 종목이 2주→10주처럼 "비율상 5배"로만
                               보이는 오탐을 절대 기준치로 걸러냄
    volume_surge: scalp_engine.volume_surge_ratio() 결과 — min_volume_surge_ratio 검사 시에만 사용
    cur_price/tick_size: 호가 노이즈 보정용 — 저가 코인/종목에서 1틱 등락만으로 모멘텀 조건이
      충족되는 것을 막기 위해 entry_momentum_pct를 tick_aware_floor로 자동 상향 (둘 다 필요,
      하나라도 0이면 보정 없이 설정값 그대로 사용)
    ticker: entry_confirm_cycles 연속 확인 상태를 추적할 키. None이면 확인 절차 없이 즉시 판단(하위호환).
    extreme_volume_baseline_2min: 호출부가 kis_api.get_avg_daily_volume()로 미리 조회해서
      "10일 평균 일거래량 ÷ 하루 2분 구간 수"로 정규화해 캐싱해둔 절대 기준치.
      None이면 절대기준 검사 없이 extreme_volume_surge_ratio(비율)만으로 판단.
    """
    entry_th_cfg = float(params.get("entry_momentum_pct", 0.4))
    entry_th = tick_aware_floor(cur_price, tick_size, entry_th_cfg)
    max_day  = float(params.get("max_day_chg_pct", 5.0))
    min_vol_surge = float(params.get("min_volume_surge_ratio", 0) or 0)
    confirm_cycles = int(params.get("entry_confirm_cycles", 2) or 1)
    strong_multiplier = float(params.get("strong_signal_multiplier", 2.0) or 0)

    def _fail(reason: str) -> tuple[bool, str]:
        if ticker is not None:
            _entry_confirm[ticker] = 0
        return False, reason

    if momentum is None:
        return _fail("관측 데이터 부족")
    if today_chg_pct is not None and today_chg_pct > max_day:
        return _fail(f"당일 이미 +{today_chg_pct:.1f}% 과열 — 추격 제외")
    if momentum < entry_th:
        return _fail(f"모멘텀 {momentum:+.2f}% < 임계 {entry_th:.2f}%")
    if min_vol_surge > 0:
        if volume_surge is None:
            return _fail("거래량 데이터 부족")
        if volume_surge < min_vol_surge:
            return _fail(f"거래량 증가 부족 ({volume_surge:.2f}배 < 임계 {min_vol_surge:.2f}배)")

    base_reason = f"모멘텀 진입 {momentum:+.2f}%" + (f" · 거래량 {volume_surge:.2f}배" if min_vol_surge > 0 else "")

    if ticker is None or confirm_cycles <= 1:
        return True, base_reason

    if strong_multiplier > 0:
        momentum_strong = entry_th > 0 and momentum >= entry_th * strong_multiplier
        volume_strong = min_vol_surge <= 0 or (volume_surge is not None and volume_surge >= min_vol_surge * strong_multiplier)
        if momentum_strong and volume_strong:
            _entry_confirm.pop(ticker, None)
            return True, base_reason + " (강한 신호 — 확인 절차 생략)"

    extreme_ratio = float(params.get("extreme_volume_surge_ratio", 0) or 0)
    if extreme_ratio > 0 and volume_surge is not None and volume_surge >= extreme_ratio:
        abs_ok = True
        if extreme_volume_baseline_2min is not None and extreme_volume_baseline_2min > 0:
            recent_abs = recent_volume_abs(ticker, window_sec=120) if ticker is not None else None
            abs_ok = recent_abs is not None and recent_abs >= extreme_volume_baseline_2min * extreme_ratio
        if abs_ok:
            _entry_confirm.pop(ticker, None)
            return True, base_reason + f" (거래량 급증 {volume_surge:.2f}배 — 즉시 진입)"

    count = _entry_confirm.get(ticker, 0) + 1
    if count < confirm_cycles:
        _entry_confirm[ticker] = count
        return False, f"{base_reason} (지속 확인 중 {count}/{confirm_cycles}회)"
    _entry_confirm.pop(ticker, None)
    return True, base_reason


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
            if m is None:
                continue
            eff_th = tick_aware_floor(c.get("price", 0), c.get("tick_size", 0), min_momentum_pct)
            if m < eff_th:
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
    min_volume_surge: float = 0.0,
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
    min_volume_surge: 0보다 크면 반등 구간에 거래량 증가도 함께 요구 — 매수세 없는 "힘없는
      바운스"(예: 잠깐 튀었다가 방향 없이 정체)를 걸러내기 위함. select_auto_candidates와
      동일한 철학이나, 반등 모드는 원래 이 확인이 빠져 있어 추가함.
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
        eff_rebound_th = tick_aware_floor(c.get("price", 0), c.get("tick_size", 0), min_rebound_pct)
        if rebound < eff_rebound_th:
            continue
        if min_volume_surge > 0:
            vs = c.get("volume_surge")
            if vs is None or vs < min_volume_surge:
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
    tick_size: float = 0.0,
    peak_pnl_pct: float | None = None,
    cur_momentum_pct: float | None = None,
) -> tuple[bool, str]:
    """
    params:
      take_profit_pct   — 익절 기준 (기본 0.6%) — 호가 노이즈 보정 없음 (노이즈로 익절돼도 손해가 아님)
      fast_rise_momentum_pct — 보유 중 lookback_sec 동안의 모멘텀(cur_momentum_pct)이 이 값
                           (기본 0=비활성) 이상이면 "급등 중"으로 보고 take_profit_pct 대신
                           fast_rise_take_profit_pct를 목표로 사용 — 급등이 꺾이기 전에 너무
                           일찍 익절해서 남은 상승분을 놓치는 것을 막기 위함
      fast_rise_take_profit_pct — 급등 판단 시 적용할 상향된 익절 목표 (기본 take_profit_pct와 동일,
                           즉 값을 안 주면 사실상 비활성)
      stop_loss_pct     — 손절 기준 (기본 0.4%, 양수로 입력) — 시간과 무관하게 항상 적용
      fast_decline_momentum_pct — 보유 중 lookback_sec 동안의 모멘텀(cur_momentum_pct)이 이 값의
                           음수(기본 0=비활성) 이하로 급락 중이면 stop_loss_pct 대신 더 타이트한
                           fast_decline_stop_loss_pct를 손절선으로 사용 — 급락장에서 기본 손절선까지
                           기다리다 손실이 더 커지는 것을 막기 위함 (fast_rise의 대칭 개념)
      fast_decline_stop_loss_pct — 급락 판단 시 적용할 축소된 손절 기준 (기본 stop_loss_pct와 동일,
                           즉 값을 안 주면 사실상 비활성). stop_loss_pct보다 작아야 의미 있음
      time_stop_sec     — 시간초과 판단 시점 (기본 180초)
      time_stop_loss_pct — 시간초과 시점에 이 손실률(양수 입력)을 넘겼을 때만 청산 (기본 0.5%)
                           그 안이면(수익이거나 손실이 -0.5% 이내면) 무조건 청산하지 않고 계속 보유
                           — "3분 지났다고 무조건 손절"하던 것을 완화, 시간초과=거의 항상 수수료손실
                             패턴(승률 저하 요인)을 줄이기 위함
      trailing_giveback_pct — peak_pnl_pct 사용 시, 고점 대비 이만큼(양수, 기본 0.3%) 되돌리면
                           그때 익절 확정. take_profit_pct는 "트레일링을 시작하는 문턱"이 되고
                           실제 매도는 추세가 꺾이는 걸 확인한 뒤에 이뤄짐 — 목표가에 닿자마자
                           바로 팔아서 그 뒤로도 계속 오르는 상승분을 놓치는 문제를 줄이기 위함
      fast_rise_trailing_giveback_pct — 급등 판단(fast_rise) 중일 때 trailing_giveback_pct 대신
                           쓰는 더 넉넉한 되돌림 허용폭 (기본 trailing_giveback_pct와 동일, 즉
                           값을 안 주면 사실상 비활성). 급등 종목은 등락폭 자체가 커서 기본
                           되돌림폭(0.3%)이면 추세가 살아있는데도 너무 일찍 끊기는 문제가 있어
                           분리 — cur_momentum_pct가 매 사이클 갱신되므로, 급등 기세가 식으면
                           자동으로 다시 기본 되돌림폭으로 좁혀짐
      stagnation_multiplier — time_stop_sec의 이 배수(기본 3배)만큼 지나도 손익이 거의 없으면
                           정체로 보고 청산 (기본 0이면 비활성화 아님, 3배). 오르지도 내리지도
                           않는 포지션이 동시보유 슬롯을 계속 붙잡아 새 후보를 못 잡는 기회비용을 줄임
      stagnation_band_pct — 정체 판단 기준 손익 폭(절대값, 기본 0.2%) — 이 안에 머물러 있어야 정체로 간주
    entry_price/cur_price는 이미 수수료를 반영한 값을 넘기는 것을 권장 (job 쪽에서 계산).
    tick_size: 호가 노이즈 보정용 — 저가 코인/종목에서 1틱 하락만으로 손절선을 넘겨버리는 것을
      막기 위해 stop_loss_pct/time_stop_loss_pct를 tick_aware_floor로 자동 상향 (0이면 보정 없음).
    peak_pnl_pct: 이 포지션 보유 중 지금까지 관측된 최고 손익률(%, 호출부에서 매 사이클 추적해서 전달).
      None이면(하위호환) 트레일링 없이 기존처럼 목표가 도달 즉시 익절.
    cur_momentum_pct: 보유 중 lookback_sec 동안의 실시간 모멘텀(%, 호출부에서 매 사이클 계산해서 전달).
      None이면(하위호환) fast_rise 판단 없이 기존처럼 take_profit_pct만 사용.
    """
    take_pct               = float(params.get("take_profit_pct", 0.6))
    stop_pct               = tick_aware_floor(entry_price, tick_size, float(params.get("stop_loss_pct", 0.4)))
    time_stop              = float(params.get("time_stop_sec", 180))
    time_stop_loss_pct     = tick_aware_floor(entry_price, tick_size, float(params.get("time_stop_loss_pct", 0.5)))
    trailing_giveback_pct  = tick_aware_floor(entry_price, tick_size, float(params.get("trailing_giveback_pct", 0.3)))
    fast_rise_giveback_pct = tick_aware_floor(entry_price, tick_size,
                                               float(params.get("fast_rise_trailing_giveback_pct", trailing_giveback_pct) or trailing_giveback_pct))
    stagnation_multiplier  = float(params.get("stagnation_multiplier", 3.0))
    stagnation_band_pct    = float(params.get("stagnation_band_pct", 0.2))
    fast_rise_momentum_pct = float(params.get("fast_rise_momentum_pct", 0) or 0)
    fast_rise_take_pct     = float(params.get("fast_rise_take_profit_pct", take_pct) or take_pct)
    fast_decline_momentum_pct = float(params.get("fast_decline_momentum_pct", 0) or 0)
    fast_decline_stop_pct  = tick_aware_floor(entry_price, tick_size,
                                               float(params.get("fast_decline_stop_loss_pct", stop_pct) or stop_pct))

    if entry_price <= 0:
        return False, ""

    chg_pct = (cur_price - entry_price) / entry_price * 100

    effective_stop_pct = stop_pct
    is_fast_decline = (
        fast_decline_momentum_pct > 0
        and cur_momentum_pct is not None
        and cur_momentum_pct <= -fast_decline_momentum_pct
    )
    if is_fast_decline:
        effective_stop_pct = min(stop_pct, fast_decline_stop_pct)

    if chg_pct <= -effective_stop_pct:
        label = "급락 손절" if is_fast_decline and effective_stop_pct < stop_pct else "손절"
        return True, f"{label} ({chg_pct:.2f}%)"

    effective_take_pct = take_pct
    is_fast_rise = False
    if fast_rise_momentum_pct > 0 and cur_momentum_pct is not None and cur_momentum_pct >= fast_rise_momentum_pct:
        effective_take_pct = max(take_pct, fast_rise_take_pct)
        is_fast_rise = effective_take_pct > take_pct
    effective_giveback_pct = fast_rise_giveback_pct if is_fast_rise else trailing_giveback_pct

    if peak_pnl_pct is None:
        if chg_pct >= effective_take_pct:
            return True, f"익절 (+{chg_pct:.2f}%)"
    else:
        peak = max(peak_pnl_pct, chg_pct)
        if peak >= effective_take_pct and chg_pct <= peak - effective_giveback_pct:
            return True, f"익절 (고점 +{peak:.2f}% → 청산 {chg_pct:+.2f}%)"

    elapsed = now - entered_at

    if elapsed >= time_stop:
        if chg_pct <= -time_stop_loss_pct:
            return True, f"시간초과 손절 ({int(elapsed)}초 경과, {chg_pct:+.2f}%)"

    if stagnation_multiplier > 0 and elapsed >= time_stop * stagnation_multiplier:
        if abs(chg_pct) <= stagnation_band_pct:
            return True, f"정체 청산 ({int(elapsed)}초 경과, {chg_pct:+.2f}% 정체)"

    return False, ""
