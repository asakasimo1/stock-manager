"""
코인 초단타(스캘핑) 잡 — daemon_scalp.py 에서 5초 주기로 실행
Gist scalp_coin_jobs.json 에서 잡 읽기 → 모멘텀 진입/청산 판단 → Upbit 시장가 주문 → 상태 갱신
Gist scalp_control.json 의 coin_enabled=false 이면 신규 진입 중단 + 보유 포지션 즉시 청산 (전체 정지 킬스위치)
Gist scalp_auto_config.json 의 coin.enabled=true 이면 전체 KRW 마켓을 스캔해 급등 후보를 watching 잡으로 자동 생성
  (source="auto" 잡은 1회 진입/청산 후 status=done 으로 종료 — 슬롯을 비우고 다음 사이클에 새 후보 재탐색)
  두 가지 발굴 모드(둘 다 독립적으로 켜고 끌 수 있음, discovery_mode 로 잡에 기록됨):
    surge(기본 켜짐)   : 최근 discovery_momentum_sec(60초)간 momentum_pct ≥ min_discovery_momentum_pct(0.4%)
                        AND 최근 거래량 ÷ 평소 거래량(volume_surge_ratio) ≥ min_volume_surge_ratio(1.3배)
    reversal(기본 꺼짐): 최근 decline_lookback_sec(300초)간 min_decline_pct(2.0%) 이상 하락
                        AND 최근 rebound_lookback_sec(30초)간 min_rebound_pct(0.4%) 이상 반등
                        — "빠르게 급락하다가 급 양전"하는 대상 포착용
  전체 유니버스 가격/거래량을 매 스캔마다 기록해서 아직 잡이 아닌 코인도 위 지표를 계산함

잡 스키마 (scalp_coin_jobs.json, 리스트):
  status  : active | paused | stopped
  phase   : watching | holding
  entry_momentum_pct, lookback_sec, max_day_chg_pct  — 진입 조건
  take_profit_pct, stop_loss_pct, time_stop_sec       — 청산 조건
  krw_amount           — 1회 진입 금액
  max_daily_loss_krw   — 당일 실현손실 한도(음수) 도달 시 자동 stopped
  buy_price/buy_qty/entered_at/buy_uuid — holding 상태 필드
  trades_today/realized_pnl_today/stats_date — 당일 통계 (자정 리셋)
"""
import logging
import math
import time
from datetime import datetime, timezone, timedelta

import upbit_api
import gist_writer
import scalp_engine

KST = timezone(timedelta(hours=9))
logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("scalp_coin_cloud.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s KST %(levelname)s %(message)s"))
logger.addHandler(_fh)

BUY_FEE  = upbit_api.BUY_FEE
SELL_FEE = upbit_api.SELL_FEE

MAX_CONCURRENT_POSITIONS = 3  # 코인 스캘핑 동시 보유 종목 상한 (하드 리밋)

DISCOVERY_INTERVAL_SEC = 30  # 자동발굴 스캔 주기 하한 — 5초마다 전체 마켓 스캔·Gist 쓰기하면
                              # GitHub Gist API 쓰기 레이트리밋(secondary rate limit)에 걸림
_last_discover_at = 0.0

RECONCILE_INTERVAL_SEC = 60  # 유령 포지션 점검 주기 — 실시간성 불필요, 60초면 충분
_last_reconcile_at = 0.0


def _should_run_reconcile(now_epoch: float) -> bool:
    global _last_reconcile_at
    if now_epoch - _last_reconcile_at < RECONCILE_INTERVAL_SEC:
        return False
    _last_reconcile_at = now_epoch
    return True


def _reconcile_orphan_positions(jobs: list, auto_cfg: dict, now_epoch: float) -> None:
    """실제 Upbit 잔고에는 있는데 추적하는 잡이 없는 '유령 포지션'을 감지해 holding
    잡으로 재생성한다.

    2026-08-24 실측 — 여러 종목이 거의 동시에 매수되며 Gist 쓰기가 몰리던 중 GitHub API
    레이트리밋(403)이 2분 넘게 지속됐고, _save_jobs()의 재시도(최대 수 초)로는 이걸
    못 버텨서 리플/오리진트레일/멀티버스엑스/프롬 매수 체결(되돌릴 수 없음)은
    성공했는데 이를 기록하는 잡만 유실되어 아무도 매도를 관리하지 않는 상태가 됐음.
    재시도를 더 늘리는 방향은 근본 해결이 안 됨(레이트리밋이 몇 분씩 갈 수도 있고,
    그동안 5초 주기 데몬을 블로킹할 수도 없음) — 대신 매 사이클 실제 잔고와 잡 목록을
    대조해서, 잔고엔 있는데 추적 잡이 없는 코인을 발견하면 그 자리에서 holding 잡을
    새로 만들어 즉시 저장한다. 이러면 유실 자체는 못 막아도 다음 정상 사이클(최대
    60초) 안에 자동으로 복구되어, 기존 익절/손절/시간초과 청산 로직이 정상적으로
    그 포지션을 관리하게 된다. 그리드 매매가 이미 들고 있는 코인은 건드리지 않음."""
    try:
        bal = upbit_api.get_balance()
    except Exception as e:
        logger.warning("[유령 포지션 점검] 잔고 조회 실패: %s", e)
        return

    try:
        grid_jobs = gist_writer._read_gist_file("coin_grid_jobs.json") or []
        grid_tickers = {j.get("ticker") for j in grid_jobs if j.get("status") not in ("done", "stopped")}
    except Exception as e:
        logger.warning("[유령 포지션 점검] 그리드 잡 조회 실패 — 안전하게 건너뜀: %s", e)
        return

    # coin_sell_jobs.json에 사용자가 직접 등록한 매도 목표가 이미 있는 티커도 건드리지
    # 않음 — 여기서 holding 잡을 또 만들면 기본 익절/손절%(auto_cfg)가 사용자가 지정한
    # 목표가보다 먼저 발동해서 의도와 다르게 조기 매도될 수 있음(2026-08-24).
    try:
        sell_jobs = gist_writer._read_gist_file("coin_sell_jobs.json") or []
        manual_sell_tickers = {j.get("ticker") for j in sell_jobs if j.get("status") in ("active", "submitted")}
    except Exception as e:
        logger.warning("[유령 포지션 점검] 매도 잡 조회 실패 — 안전하게 건너뜀: %s", e)
        return

    tracked_tickers = {j["ticker"] for j in jobs if j.get("status") not in ("stopped", "done")}
    recovered = []

    for h in bal["holdings"]:
        ticker = h["ticker"]
        if ticker in tracked_tickers or ticker in grid_tickers or ticker in manual_sell_tickers:
            continue
        if h["qty"] <= 0 or h["eval_amount"] < 5000:  # 업비트 최소주문금액 미만 잔량(dust)은 매도도 안 되므로 무시
            continue

        logger.warning("👻 [유령 포지션 발견] %s(%s) %.8f개 @ 평단 %s원 — 추적 잡 없음, holding 잡으로 복구",
                        h["name"], ticker, h["qty"], f"{h['avg_price']:,.0f}")
        jobs.append({
            "id":                 f"recovered-{ticker}-{int(now_epoch)}",
            "ticker":             ticker,
            "name":               h["name"],
            "status":             "active",
            "phase":              "holding",
            "source":             "auto",
            "discovery_mode":     "recovered",
            "entry_momentum_pct": auto_cfg.get("entry_momentum_pct", 0.4),
            "lookback_sec":       auto_cfg.get("lookback_sec", 30),
            "max_day_chg_pct":    auto_cfg.get("max_day_chg_pct", 5.0),
            "take_profit_pct":    auto_cfg.get("take_profit_pct", 0.6),
            "stop_loss_pct":      auto_cfg.get("stop_loss_pct", 0.4),
            "time_stop_sec":      auto_cfg.get("time_stop_sec", 180),
            "time_stop_loss_pct": auto_cfg.get("time_stop_loss_pct", 0.5),
            "fast_rise_momentum_pct":     auto_cfg.get("fast_rise_momentum_pct", 0),
            "fast_rise_take_profit_pct":  auto_cfg.get("fast_rise_take_profit_pct", 0),
            "fast_decline_momentum_pct":  auto_cfg.get("fast_decline_momentum_pct", 0),
            "fast_decline_stop_loss_pct": auto_cfg.get("fast_decline_stop_loss_pct", 0),
            "krw_amount":         auto_cfg.get("krw_amount", 0),
            "max_daily_loss_krw": float(auto_cfg.get("max_daily_loss_krw", -30000)),
            "watch_timeout_sec":  auto_cfg.get("watch_timeout_sec", 300),
            "min_volume_surge_ratio": auto_cfg.get("min_volume_surge_ratio", 1.3),
            "discovered_at":      now_epoch,
            "buy_price": h["avg_price"], "buy_qty": h["qty"], "entered_at": now_epoch, "buy_uuid": "",
            "trades_today": 0, "realized_pnl_today": 0, "stats_date": _today_str(),
            "created_at":    datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
            "recovered_at":  datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        })
        recovered.append(ticker)

    if recovered:
        # 이 사이클 뒤에서 벌어질 다른 처리(가격조회 실패, 다른 잡의 예외 등)와 무관하게
        # 복구 사실 자체는 반드시 남겨야 하므로 즉시 저장(_save_jobs가 자체 재시도 포함).
        _save_jobs(jobs)
        logger.info("👻 유령 포지션 %d건 복구 완료: %s", len(recovered), recovered)


def _should_run_discovery(now_epoch: float) -> bool:
    global _last_discover_at
    if now_epoch - _last_discover_at < DISCOVERY_INTERVAL_SEC:
        return False
    _last_discover_at = now_epoch
    return True


def _today_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def _reset_daily_stats_if_needed(job: dict) -> bool:
    today = _today_str()
    if job.get("stats_date") != today:
        job["stats_date"] = today
        job["trades_today"] = 0
        job["realized_pnl_today"] = 0
        return True
    return False


def _net_buy_cost(price: float) -> float:
    return price * (1 + BUY_FEE)


def _net_sell_value(price: float) -> float:
    return price * (1 - SELL_FEE)


def _is_coin_enabled() -> bool:
    ctrl = gist_writer._read_gist_file("scalp_control.json")
    if not isinstance(ctrl, dict):
        return True  # 컨트롤 파일 없음 → 기본 허용
    return ctrl.get("coin_enabled", True)


def _load_auto_config() -> dict:
    cfg = gist_writer._read_gist_file("scalp_auto_config.json")
    if not isinstance(cfg, dict):
        return {}
    return cfg.get("coin") or {}


def _auto_discover(jobs: list, auto_cfg: dict, price_cache: dict, coin_enabled: bool, now_epoch: float) -> bool:
    """scalp_auto_config.json의 coin 설정에 따라 급등 후보를 찾아 watching 잡을 자동 생성.
    반환: jobs 리스트가 변경되었는지 여부 (호출부에서 append 된 내용을 그대로 사용)"""
    if not auto_cfg.get("enabled") or not coin_enabled:
        return False

    today = _today_str()
    max_loss = float(auto_cfg.get("max_daily_loss_krw", -30000))
    auto_pnl_today = sum(
        j.get("realized_pnl_today", 0) for j in jobs
        if j.get("source") == "auto" and j.get("stats_date") == today
    )
    if auto_pnl_today <= max_loss:
        return False

    active_auto = [j for j in jobs if j.get("source") == "auto" and j.get("status") not in ("done", "stopped")]
    slots = int(auto_cfg.get("max_concurrent", 2)) - len(active_auto)
    if slots <= 0:
        return False

    # 전체 유니버스 가격·거래량 이력 기록 — 아직 잡으로 등록 안 된 코인도 모멘텀/거래량증가를
    # 계산할 수 있어야 "갑자기 급등 + 거래량 증가" 조건으로 후보를 뽑을 수 있음.
    # (record_price/record_volume는 이미 관찰중인 잡 티커에 대해서도 메인 루프에서 매 사이클
    #  호출되므로 여기서 다시 불러도 중복 문제 없음 — 같은 시각 값이면 그대로 갱신될 뿐)
    for ticker, info in price_cache.items():
        scalp_engine.record_price(ticker, info.get("price", 0), now=now_epoch)
        scalp_engine.record_volume(ticker, info.get("volume", 0), now=now_epoch)

    discovery_lookback = float(auto_cfg.get("discovery_momentum_sec", 60))
    min_momentum = float(auto_cfg.get("min_discovery_momentum_pct", 0.4))
    min_vol_surge = float(auto_cfg.get("min_volume_surge_ratio", 1.3))
    decline_lookback = float(auto_cfg.get("decline_lookback_sec", 300))
    min_decline = float(auto_cfg.get("min_decline_pct", 2.0))
    rebound_lookback = float(auto_cfg.get("rebound_lookback_sec", 30))
    min_rebound = float(auto_cfg.get("min_rebound_pct", 0.4))
    surge_enabled = auto_cfg.get("surge_enabled", True)
    reversal_enabled = auto_cfg.get("reversal_enabled", False)

    candidates = [
        {
            "ticker": ticker,
            "name": upbit_api.COIN_NAMES.get(ticker, ticker),
            "price": info.get("price", 0),
            "tick_size": upbit_api.price_unit(info.get("price", 0)),
            "chg_pct": info.get("chg_pct", 0),
            "liquidity": info.get("volume", 0) * info.get("price", 0),
            "momentum": scalp_engine.momentum_pct(ticker, discovery_lookback, now=now_epoch),
            "volume_surge": scalp_engine.volume_surge_ratio(ticker, now=now_epoch),
            "decline": scalp_engine.momentum_pct(ticker, decline_lookback, now=now_epoch),
            "rebound": scalp_engine.momentum_pct(ticker, rebound_lookback, now=now_epoch),
        }
        for ticker, info in price_cache.items()
    ]
    candidates.sort(key=lambda c: c["momentum"] if c["momentum"] is not None else -999, reverse=True)

    # 진행중인 티커 + 오늘 이미 시도한 자동발굴 티커는 재시도 제한 대상
    # retry_cooldown_sec=0(기본)이면 기존처럼 당일 1회, >0이면
    # 마지막 발굴 시점(discovered_at)으로부터 그 시간(초)이 지나면 같은 티커 재시도 허용
    # (손절 후 진짜 반등이 왔는데도 하루 종일 못 잡던 사각지대 완화 목적)
    retry_cooldown = float(auto_cfg.get("retry_cooldown_sec", 0) or 0)
    existing_tickers = set()
    for j in jobs:
        if j.get("status") not in ("done", "stopped"):
            existing_tickers.add(j["ticker"])
        elif j.get("source") == "auto" and j.get("stats_date") == today:
            if retry_cooldown <= 0 or (now_epoch - float(j.get("discovered_at", 0) or 0)) < retry_cooldown:
                existing_tickers.add(j["ticker"])
    max_day_chg = float(auto_cfg.get("max_day_chg_pct", 5.0))
    min_liquidity = float(auto_cfg.get("min_liquidity", 50_000_000))

    # 거래량 상위 N% 필터(기본 30%) — 업비트 전체 KRW 마켓 코인 중 거래대금 상위권만 후보로 삼음.
    # min_liquidity(절대 하한)와 별개로, 두 기준 중 더 엄격한(높은) 값을 실제 하한으로 적용.
    volume_top_pct = float(auto_cfg.get("volume_top_pct", 0.3) or 0)
    if volume_top_pct > 0 and candidates:
        liquidities = sorted((c["liquidity"] for c in candidates), reverse=True)
        cutoff_idx = min(int(len(liquidities) * volume_top_pct), len(liquidities) - 1)
        percentile_liquidity = liquidities[cutoff_idx]
        if percentile_liquidity > min_liquidity:
            min_liquidity = percentile_liquidity

    picked_surge = []
    if surge_enabled:
        picked_surge = scalp_engine.select_auto_candidates(
            candidates, existing_tickers, max_day_chg, min_liquidity, slots,
            min_momentum_pct=min_momentum, min_volume_surge=min_vol_surge,
        )
        for c in picked_surge:
            c["_mode"] = "surge"

    picked_reversal = []
    remaining = slots - len(picked_surge)
    if reversal_enabled and remaining > 0:
        picked_reversal = scalp_engine.select_reversal_candidates(
            candidates, existing_tickers | {c["ticker"] for c in picked_surge}, min_liquidity, remaining,
            min_decline_pct=min_decline, min_rebound_pct=min_rebound, min_volume_surge=min_vol_surge,
        )
        for c in picked_reversal:
            c["_mode"] = "reversal"

    picked = picked_surge + picked_reversal

    # 호가 스프레드 필터 — 유동성(거래대금) 기준은 통과해도 매수/매도 1호가 차이가 크면
    # 시장가 진입 시 슬리피지가 커질 수 있어 별도 확인. max_spread_pct=0(기본)이면 비활성.
    max_spread = float(auto_cfg.get("max_spread_pct", 0) or 0)
    if max_spread > 0 and picked:
        try:
            spreads = upbit_api.get_spread_pct([c["ticker"] for c in picked])
            filtered = []
            for c in picked:
                sp = spreads.get(c["ticker"])
                if sp is not None and sp > max_spread:
                    logger.info("🚫 [스프레드 초과] %s(%s) %.3f%% > 기준 %.2f%% — 후보 제외",
                                c["name"], c["ticker"], sp, max_spread)
                    continue
                filtered.append(c)
            picked = filtered
        except Exception as e:
            logger.warning("스프레드 필터 조회 실패 — 필터 없이 진행: %s", e)

    for c in picked:
        jobs.append({
            "id":                 f"auto-{c['ticker']}-{int(now_epoch)}",
            "ticker":             c["ticker"],
            "name":               c["name"],
            "status":             "active",
            "phase":              "watching",
            "source":             "auto",
            "discovery_mode":     c["_mode"],
            "entry_momentum_pct": auto_cfg.get("entry_momentum_pct", 0.4),
            "lookback_sec":       auto_cfg.get("lookback_sec", 30),
            "max_day_chg_pct":    max_day_chg,
            "take_profit_pct":    auto_cfg.get("take_profit_pct", 0.6),
            "stop_loss_pct":      auto_cfg.get("stop_loss_pct", 0.4),
            "time_stop_sec":      auto_cfg.get("time_stop_sec", 180),
            "time_stop_loss_pct": auto_cfg.get("time_stop_loss_pct", 0.5),
            "fast_rise_momentum_pct":    auto_cfg.get("fast_rise_momentum_pct", 0),
            "fast_rise_take_profit_pct": auto_cfg.get("fast_rise_take_profit_pct", 0),
            "fast_decline_momentum_pct":  auto_cfg.get("fast_decline_momentum_pct", 0),
            "fast_decline_stop_loss_pct": auto_cfg.get("fast_decline_stop_loss_pct", 0),
            "krw_amount":         auto_cfg.get("krw_amount", 0),
            "max_daily_loss_krw": max_loss,
            "watch_timeout_sec":  auto_cfg.get("watch_timeout_sec", 300),
            "min_volume_surge_ratio": min_vol_surge,
            "discovered_at":      now_epoch,
            "buy_price": 0, "buy_qty": 0, "entered_at": 0, "buy_uuid": "",
            "trades_today": 0, "realized_pnl_today": 0, "stats_date": today,
            "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        })
        if c["_mode"] == "surge":
            logger.info("🔍 [자동포착·급등] %s(%s) 최근%.0f초 모멘텀 %s%% · 거래량 %s배 (당일 %+.2f%%) — watching 잡 생성",
                        c["name"], c["ticker"], discovery_lookback,
                        scalp_engine.fmt_opt(c["momentum"]), scalp_engine.fmt_opt(c["volume_surge"], "{:.2f}"), c["chg_pct"])
        else:
            logger.info("🔍 [자동포착·반등] %s(%s) 최근%.0f초 %s%% 하락 후 최근%.0f초 %s%% 반등 — watching 잡 생성",
                        c["name"], c["ticker"], decline_lookback, scalp_engine.fmt_opt(c["decline"]),
                        rebound_lookback, scalp_engine.fmt_opt(c["rebound"]))

    return bool(picked)


def _poll_executed_volume(order_uuid: str, fallback: float, tries: int = 6, delay: float = 0.5) -> float:
    """시장가 매수 직후 실제 체결수량을 조회. 추정치(fallback)와 실제 체결량이 달라 매도 시
    잔량(dust)이 남는 문제를 막기 위함 — 반드시 Upbit이 확정한 executed_volume을 사용.

    주의: ord_type="price"(총액 지정 시장가 매수) 주문은 완전히 체결돼도 Upbit이 state를
    "done"이 아니라 "cancel"로 반환한다(소진 후 남은 자투리 금액을 취소 처리하는 방식) — 그래서
    예전에 state=="done"만 확인하던 코드는 이 경우를 절대 못 잡고 매번 추정치로 폴백했고,
    그 추정치가 실제 체결량보다 살짝 적어 매도 시마다 미세한 잔량(dust)이 쌓였다.
    state 값과 무관하게 remaining_volume==0(더 이상 미체결분이 없음)이면 체결 확정으로 본다."""
    if not order_uuid:
        return fallback
    for _ in range(tries):
        try:
            order = upbit_api.get_order(order_uuid)
            if order["remaining_volume"] == 0 and order["executed_volume"] > 0:
                return order["executed_volume"]
        except Exception as e:
            logger.warning("주문 체결 조회 실패 %s: %s", order_uuid, e)
            break
        time.sleep(delay)
    logger.warning("주문 %s 체결수량 확인 실패 — 추정치(%.8f) 사용", order_uuid, fallback)
    return fallback


def _sellable_qty(ticker: str, recorded_qty: float) -> float:
    """실제 매도 직전 계좌 잔고로 한 번 더 클램프. 체결수량 추정이 실제 잔고보다 커서
    insufficient_funds_ask로 매도가 영구히 막히는 사고(저유동성 코인에서 슬리피지로 인해
    실제 체결량이 추정치보다 적을 때 발생)를 방지한다."""
    try:
        actual = upbit_api.get_currency_balance(ticker.split("-", 1)[1])
    except Exception as e:
        logger.warning("%s 잔고 조회 실패 — 기록된 수량 그대로 사용: %s", ticker, e)
        return recorded_qty
    if actual < recorded_qty:
        logger.warning("%s 기록수량(%.8f) > 실제잔고(%.8f) — 실제잔고로 매도", ticker, recorded_qty, actual)
        return actual
    return recorded_qty


def _record_trade_log(job: dict, buy_price: float, sell_price: float, qty: float,
                       pnl: float, pnl_pct: float, reason: str) -> None:
    """탭 UI '초단타내역'에 1줄로 표시할 완료된 라운드트립(매수→매도) 기록.
    잡 하나가 여러 번 진입/청산을 반복할 수 있어 잡 객체 자체(trade_log)에 이력을 쌓는다."""
    now = datetime.now(KST)
    log = job.setdefault("trade_log", [])
    log.insert(0, {
        "buy_price": buy_price, "sell_price": sell_price, "qty": qty,
        "pnl": pnl, "pnl_pct": pnl_pct, "reason": reason,
        "date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"),
        "ts": now.timestamp(),
    })
    del log[20:]


def _force_close(job: dict, cur_price: float, reason: str) -> None:
    ticker = job["ticker"]
    name   = job.get("name", ticker)
    qty    = float(job.get("buy_qty", 0))
    if qty <= 0:
        job["phase"] = "watching"
        return
    qty = _sellable_qty(ticker, qty)
    try:
        result = upbit_api.place_order(market=ticker, side="ask", ord_type="market", volume=qty)
        scalp_engine.clear_peak_pnl(ticker)
        buy_price = float(job.get("buy_price", 0))
        # 청산 판단(트리거)은 수수료 포함 순가격으로 하되, 기록/표시되는 손익은
        # 수수료 제외 단순 가격차(기존 방식)로 계산 — 2026-08-11 사용자 요청으로
        # 트리거와 표시값을 분리함(트리거만 수수료 반영 유지).
        pnl = (cur_price - buy_price) * qty
        job["realized_pnl_today"] = job.get("realized_pnl_today", 0) + pnl
        job["trades_today"] = job.get("trades_today", 0) + 1
        job["phase"] = "watching"
        job["buy_price"] = 0
        job["buy_qty"] = 0
        job["entered_at"] = 0
        job["buy_uuid"] = ""
        if job.get("source") == "auto":
            job["status"] = "done"  # 자동발굴 잡은 1회성 — 청산 후 슬롯 반환
        pnl_pct = (pnl / (buy_price * qty) * 100) if buy_price > 0 else None
        _record_trade_log(job, buy_price, cur_price, qty, pnl, pnl_pct, reason)
        gist_writer.log_trade(ticker, name, "sell", cur_price, qty, pnl=pnl,
                               pnl_pct=pnl_pct, reason=reason, order_no=result.get("uuid", ""))
        logger.info("★ [%s] 강제청산 %s %.8f개 @ %s원  손익 %s원  UUID:%s",
                    reason, ticker, qty, f"{cur_price:,.0f}", f"{pnl:+,.0f}", result.get("uuid", ""))
    except Exception as e:
        if scalp_engine.is_phantom_position_error(e):
            logger.warning("%s 강제청산 실패 — 실제 계좌에 없는 포지션으로 판단, 내부 상태 초기화: %s", ticker, e)
            scalp_engine.clear_peak_pnl(ticker)
            job["phase"] = "watching"
            job["buy_price"] = 0
            job["buy_qty"] = 0
            job["entered_at"] = 0
            job["buy_uuid"] = ""
            if job.get("source") == "auto":
                job["status"] = "done"
        else:
            logger.error("%s 강제청산 실패: %s", ticker, e)


def _save_jobs(jobs: list) -> bool:
    """scalp_coin_jobs.json 저장 — job_scalp_stock.py의 동일 버그 수정과 같은
    이유로 재시도+에러로그 추가(2026-08-20). 상세 사유는 job_scalp_stock.py의
    _save_jobs() 주석 참고."""
    for attempt in range(3):
        if gist_writer._write_gist({"scalp_coin_jobs.json": jobs}):
            return True
        if attempt < 2:
            time.sleep(1.0 * (attempt + 1))
    logger.error("scalp_coin_jobs.json 저장 최종 실패 — 이번 사이클의 잡 상태 변경이 유실됐을 수 있음")
    return False


def main():
    jobs = gist_writer._read_gist_file("scalp_coin_jobs.json")
    if jobs is None:
        logger.error("scalp_coin_jobs.json Gist 읽기 실패")
        return
    jobs = jobs if isinstance(jobs, list) else []

    coin_enabled = _is_coin_enabled()
    auto_cfg = _load_auto_config()
    now_epoch = time.time()
    changed = False

    # 유령 포지션 자동 복구 — tickers/price_cache 흐름의 여러 조기 return과 무관하게
    # 항상 기회를 줘야 하므로 그보다 먼저 체크. coin_enabled=false(킬스위치)일 때는
    # 신규 포지션을 만들면 안 되므로 건너뜀.
    if coin_enabled and _should_run_reconcile(now_epoch):
        _reconcile_orphan_positions(jobs, auto_cfg, now_epoch)

    # 자동발굴은 30초 주기로만 스캔 (전체 마켓 조회 + Gist 쓰기를 5초마다 하면 GitHub 쓰기 레이트리밋에 걸림)
    # 이미 watching/holding 중인 티커는 이 주기와 무관하게 매 사이클(5초) 그대로 처리됨
    run_discovery = bool(auto_cfg.get("enabled")) and coin_enabled and _should_run_discovery(now_epoch)

    if run_discovery:
        try:
            tickers = upbit_api.get_all_krw_markets()
        except Exception as e:
            logger.error("전체 마켓 조회 실패: %s", e)
            tickers = list({j["ticker"] for j in jobs if j.get("status") not in ("stopped", "done")})
    else:
        tickers = list({j["ticker"] for j in jobs if j.get("status") not in ("stopped", "done")})

    if not tickers:
        return
    try:
        price_cache = upbit_api.get_prices(tickers)
    except Exception as e:
        logger.error("현재가 일괄 조회 실패: %s", e)
        return

    if run_discovery and _auto_discover(jobs, auto_cfg, price_cache, coin_enabled, now_epoch):
        changed = True

    if not jobs:
        gist_writer.flush_trades()
        return

    holding_count = sum(1 for j in jobs if j.get("phase") == "holding" and j.get("status") != "stopped")

    for job in jobs:
        # 2026-08-24 — "done"(완료·포기된 1회성 자동발굴 잡)도 여기서 계속 처리하면
        # _reset_daily_stats_if_needed()가 매일 stats_date를 오늘 날짜로 갱신해버려서
        # (1) prune_stale_auto_jobs가 "오늘 완료된 것"으로 착각해 영원히 안 지워지고
        # (2) _auto_discover의 재시도 제한 로직도 "오늘 이미 시도함"으로 착각해 해당
        # 티커를 영구적으로 재감시 대상에서 제외하는 심각한 버그가 있었음(실측: 8/7~
        # 8/24 사이 시도된 코인 215종목 전부가 이 상태로 갇혀서 그 뒤로 단 한 번도
        # 재감시가 안 됨 — 사실상 발굴 풀 전체가 고갈됨). status=="done"은 1회성 잡의
        # 최종 상태라 이후 절대 다시 거래하지 않으므로, 완전히 건드리지 않고 건너뛴다.
        if job.get("status") in ("stopped", "done"):
            continue

        ticker = job["ticker"]
        name   = job.get("name", ticker)
        info   = price_cache.get(ticker)
        if not info:
            continue
        cur_price = info["price"]
        today_chg = info.get("chg_pct")

        if _reset_daily_stats_if_needed(job):
            changed = True

        scalp_engine.record_price(ticker, cur_price, now=now_epoch)
        scalp_engine.record_volume(ticker, info.get("volume", 0), now=now_epoch)

        # ── 전체 정지 킬스위치: 신규진입 중단 + 보유 포지션 즉시 청산 ──
        if not coin_enabled:
            if job.get("phase") == "holding":
                _force_close(job, cur_price, "전체정지 킬스위치")
                changed = True
            continue

        # ── 일일 손실 한도 체크 (holding 중이 아니면 즉시 정지) ──
        max_loss = float(job.get("max_daily_loss_krw", -20000))
        if job.get("realized_pnl_today", 0) <= max_loss and job.get("phase") != "holding":
            if job.get("status") != "stopped":
                job["status"] = "stopped"
                job["stop_reason"] = f"일일 손실 한도 도달 ({job['realized_pnl_today']:+,.0f}원)"
                changed = True
                logger.warning("⛔ %s 일일 손실 한도 도달 — 잡 정지", name)
            continue

        if job.get("status") != "active":
            continue

        # ── holding: 청산 판단 ──────────────────────────────────
        if job.get("phase") == "holding":
            buy_price  = float(job.get("buy_price", 0))
            entered_at = float(job.get("entered_at", 0))
            net_entry  = _net_buy_cost(buy_price)
            net_cur    = _net_sell_value(cur_price)
            chg_pct_now = (net_cur - net_entry) / net_entry * 100 if net_entry > 0 else 0
            peak = scalp_engine.update_peak_pnl(ticker, chg_pct_now)
            cur_momentum = scalp_engine.momentum_pct(ticker, float(job.get("lookback_sec", 30)), now=now_epoch)
            should, reason = scalp_engine.should_exit(net_entry, net_cur, entered_at, now_epoch, job,
                                                       tick_size=upbit_api.price_unit(buy_price), peak_pnl_pct=peak,
                                                       cur_momentum_pct=cur_momentum)
            if should:
                qty = _sellable_qty(ticker, float(job.get("buy_qty", 0)))
                try:
                    result = upbit_api.place_order(market=ticker, side="ask", ord_type="market", volume=qty)
                    # 청산 판단(should_exit, 위에서 이미 net_entry/net_cur로 완료)은 수수료
                    # 포함 기준을 유지하되, 기록/표시되는 손익은 수수료 제외 단순 가격차
                    # (기존 방식)로 계산 — 2026-08-11 사용자 요청으로 트리거와 표시값 분리.
                    pnl = (cur_price - buy_price) * qty
                    pnl_pct = (cur_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
                    job["realized_pnl_today"] = job.get("realized_pnl_today", 0) + pnl
                    job["trades_today"] = job.get("trades_today", 0) + 1
                    job["phase"] = "watching"
                    job["buy_price"] = 0
                    job["buy_qty"] = 0
                    job["entered_at"] = 0
                    job["buy_uuid"] = ""
                    if job.get("source") == "auto":
                        job["status"] = "done"  # 자동발굴 잡은 1회성 — 청산 후 슬롯 반환
                    changed = True
                    scalp_engine.clear_peak_pnl(ticker)
                    _record_trade_log(job, buy_price, cur_price, qty, pnl, pnl_pct, reason)
                    gist_writer.log_trade(ticker, name, "sell", cur_price, qty, pnl=pnl,
                                           pnl_pct=pnl_pct, reason=reason, order_no=result.get("uuid", ""))
                    logger.info("★ [%s] %s %.8f개 @ %s원  손익 %s원(%.2f%%)  UUID:%s",
                                reason, ticker, qty, f"{cur_price:,.0f}", f"{pnl:+,.0f}", pnl_pct, result.get("uuid", ""))
                except Exception as e:
                    if scalp_engine.is_phantom_position_error(e):
                        logger.warning("%s 청산 실패 — 실제 계좌에 없는 포지션으로 판단, 내부 상태 초기화: %s", ticker, e)
                        job["phase"] = "watching"
                        job["buy_price"] = 0
                        job["buy_qty"] = 0
                        job["entered_at"] = 0
                        job["buy_uuid"] = ""
                        if job.get("source") == "auto":
                            job["status"] = "done"
                        changed = True
                        scalp_engine.clear_peak_pnl(ticker)
                    else:
                        logger.error("%s 청산 실패: %s", ticker, e)
            else:
                logger.info("  %s(%s) 보유중 @ %s원 (진입 %s원)", name, ticker,
                            f"{cur_price:,.0f}", f"{buy_price:,.0f}")
            continue

        # ── watching: 진입 판단 ─────────────────────────────────
        # 자동발굴 잡이 오래 지켜봐도 진입 조건을 못 채우면 포기하고 슬롯 반환
        # (안 그러면 조용한 코인 2개가 max_concurrent 슬롯을 계속 차지해 새 후보를 못 찾음)
        if job.get("source") == "auto":
            timeout = float(job.get("watch_timeout_sec", 300))
            if scalp_engine.should_give_up_watching(job.get("discovered_at", 0), now_epoch, timeout):
                job["status"] = "done"
                job["stop_reason"] = f"{int(timeout)}초 내 진입 조건 미충족 — 포기"
                changed = True
                logger.info("⌛ %s(%s) 관찰 시간초과 — 포기, 슬롯 반환", name, ticker)
                continue

        if holding_count >= MAX_CONCURRENT_POSITIONS:
            continue

        lookback = float(job.get("lookback_sec", 30))
        momentum = scalp_engine.momentum_pct(ticker, lookback, now=now_epoch)
        vol_surge = scalp_engine.volume_surge_ratio(ticker, now=now_epoch)
        should, reason = scalp_engine.should_enter(momentum, today_chg, job, volume_surge=vol_surge,
                                                    cur_price=cur_price, tick_size=upbit_api.price_unit(cur_price),
                                                    ticker=ticker)
        if not should:
            logger.info("  %s(%s) 대기 — %s", name, ticker, reason)
            continue

        krw_amount = float(job.get("krw_amount", 0))
        if krw_amount <= 0:
            logger.warning("%s(%s) krw_amount 미설정 — 건너뜀", name, ticker)
            continue

        logger.info("★ [진입] %s(%s) @ %s원 — %s", name, ticker, f"{cur_price:,.0f}", reason)
        try:
            result = upbit_api.place_order(market=ticker, side="bid", ord_type="price", price=krw_amount)
            estimated_qty = math.floor(krw_amount / cur_price * (1 - BUY_FEE) * 1e8) / 1e8
            coin_qty = _poll_executed_volume(result.get("uuid", ""), fallback=estimated_qty)
            scalp_engine.clear_peak_pnl(ticker)
            job["phase"]      = "holding"
            job["buy_price"]  = cur_price
            job["buy_qty"]    = coin_qty
            job["entered_at"] = now_epoch
            job["buy_uuid"]   = result.get("uuid", "")
            changed = True
            holding_count += 1
            gist_writer.log_trade(ticker, name, "buy", cur_price, coin_qty, order_no=result.get("uuid", ""))
            logger.info("매수 체결 %s %.8f개 @ %s원", ticker, coin_qty, f"{cur_price:,.0f}")
        except Exception as e:
            err = str(e)
            if "not_supported_ord_type" in err:
                # 거래소가 해당 코인의 시장가 매수 자체를 거부(투자유의 등) — 재시도해도 계속 실패하므로 즉시 포기
                job["status"] = "done"
                job["stop_reason"] = "이 코인은 시장가 매수 미지원 (거래소 제한)"
                changed = True
                logger.warning("⛔ %s(%s) 시장가 매수 미지원 — 포기, 재시도 안 함", name, ticker)
            else:
                logger.error("%s 진입 실패: %s", ticker, e)

    jobs, pruned = scalp_engine.prune_stale_auto_jobs(jobs, _today_str())
    if pruned:
        changed = True
        logger.info("지난 완료 자동발굴 잡 %d건 정리", pruned)

    if changed:
        _save_jobs(jobs)
    gist_writer.flush_trades()


if __name__ == "__main__":
    main()
