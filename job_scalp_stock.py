"""
국내주식 초단타(스캘핑) 잡 — daemon_scalp.py 에서 5초 주기(장중에만)로 실행
Gist scalp_stock_jobs.json 에서 잡 읽기 → 모멘텀 진입/청산 판단 → KIS 시장가 주문 → 상태 갱신
Gist scalp_control.json 의 stock_enabled=false 이면 신규 진입 중단 + 보유 포지션 즉시 청산
Gist scalp_auto_config.json 의 stock.enabled=true 이면 KIS 등락률 순위에서 후보를 watching 잡으로 자동 생성
  (source="auto" 잡은 1회 진입/청산 후 status=done 으로 종료 — 장마감까지 미체결이면 그날로 종료 처리)
  두 가지 발굴 모드(둘 다 독립적으로 켜고 끌 수 있음, discovery_mode 로 잡에 기록됨):
    surge(기본 켜짐)   : 상승률순위에서 최근 모멘텀+거래량증가 조건 만족 시
    reversal(기본 꺼짐): 하락률순위도 함께 조회해서 "급락 후 반등" 조건(decline+rebound) 만족 시
                        — 상승률순위만 보면 이미 급락한 종목은 후보 풀에 아예 안 잡히기 때문

잡 스키마는 job_scalp_coin.py의 scalp_coin_jobs.json 과 동일 구조 (qty는 정수 주 단위).
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta, time as _dtime

import kis_api
import gist_writer
import scalp_engine

KST = timezone(timedelta(hours=9))
logging.Formatter.converter = lambda *args: datetime.now(KST).timetuple()
logging.basicConfig(level=logging.INFO, format="%(asctime)s KST %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
_fh = logging.FileHandler("scalp_stock_cloud.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s KST %(levelname)s %(message)s"))
logger.addHandler(_fh)

BUY_FEE  = 0.00015
SELL_FEE = 0.00195

MAX_CONCURRENT_POSITIONS = 3  # 주식 스캘핑 동시 보유 종목 상한 (하드 리밋)

DISCOVERY_INTERVAL_SEC = 5  # 자동발굴 스캔 주기 하한 — 등락률 순위 조회·Gist 쓰기 빈도 제한
# (2026-08-12: 30초→15초로 단축. 2026-08-13: NHN(181710) 사례 분석 후 15초→5초로
#  추가 단축 — 코인 스캔 주기(5초)와 동일하게 맞춤. discovery_momentum_sec(60초 룩백)와
#  entry_confirm_cycles/strong_signal_multiplier는 사용자 요청으로 그대로 유지 —
#  "포착 스캔 자체가 느린" 부분만 더 개선, 신호 품질/확인절차는 안 건드림)
_last_discover_at = 0.0


def _time_based_stop_loss_pct(auto_cfg: dict) -> float:
    """09:00~10:00(거래량 많은 개장 직후)는 변동성이 커서 손절폭을 넉넉하게,
    그 외 시간대는 상대적으로 타이트하게. 잡 발굴(진입) 시점 기준으로 한 번
    정해지면 그 포지션이 살아있는 동안 유지됨(보유 중 시각 경과로 재조정 안 함).

    NXT 프리마켓(08:00~08:50)은 예전엔 이 분기에 안 걸려서 quiet_hour(타이트한 손절폭)가
    적용됐는데, 실제로는 유동성이 얕고 시장가 주문도 안 돼서(지정가만 가능) 09~10시
    정규장보다 오히려 변동성이 거친 시간대였음. 손절폭을 quiet_hour의 2배로 넓게 잡는
    별도 버킷을 추가함(2026-08-12, 오늘 손실 6건 중 4건이 프리마켓 실측 후)."""
    now = datetime.now(KST).time()
    if kis_api._is_nxt_premarket():
        quiet = float(auto_cfg.get("stop_loss_pct_quiet_hour", 1.0))
        return float(auto_cfg.get("stop_loss_pct_premarket", quiet * 2))
    if _dtime(9, 0) <= now < _dtime(10, 0):
        return float(auto_cfg.get("stop_loss_pct_active_hour", 1.5))
    return float(auto_cfg.get("stop_loss_pct_quiet_hour", 1.0))


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


def _is_stock_enabled() -> bool:
    ctrl = gist_writer._read_gist_file("scalp_control.json")
    if not isinstance(ctrl, dict):
        return True
    return ctrl.get("stock_enabled", True)


def _load_auto_config() -> dict:
    cfg = gist_writer._read_gist_file("scalp_auto_config.json")
    if not isinstance(cfg, dict):
        return {}
    return cfg.get("stock") or {}


def _auto_discover(jobs: list, auto_cfg: dict, stock_enabled: bool, now_epoch: float) -> bool:
    """scalp_auto_config.json의 stock 설정에 따라 KIS 등락률 순위에서 급등 후보를 찾아
    watching 잡을 자동 생성. 반환: jobs 리스트가 변경되었는지 여부"""
    if not auto_cfg.get("enabled") or not stock_enabled:
        return False
    if not _should_run_discovery(now_epoch):
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

    surge_enabled = auto_cfg.get("surge_enabled", True)
    reversal_enabled = auto_cfg.get("reversal_enabled", False)

    try:
        ranking = kis_api.get_fluctuation_ranking(top_n=30, sort="gainers")
        if reversal_enabled:
            ranking = ranking + kis_api.get_fluctuation_ranking(top_n=30, sort="losers")
    except Exception as e:
        logger.error("등락률 순위 조회 실패: %s", e)
        return False

    # 순위에 나온 종목들의 가격·거래량 이력 기록 — 아직 잡이 아닌 종목도 모멘텀/거래량증가
    # 계산이 가능해야 "갑자기 급등 + 거래량 증가" 조건으로 후보를 뽑을 수 있음.
    # (KIS는 코인처럼 전체 종목을 한 번에 못 받아오므로 순위 상위 종목 범위 안에서만 추적됨.
    #  reversal 모드는 하락률순위도 함께 조회해야 "급락 후 반등" 후보가 나올 수 있음 —
    #  상승률순위만 보면 이미 급락한 종목은 아예 안 보임)
    seen_tickers = set()
    for r in ranking:
        if r["ticker"] in seen_tickers:
            continue
        seen_tickers.add(r["ticker"])
        scalp_engine.record_price(r["ticker"], r["price"], now=now_epoch)
        scalp_engine.record_volume(r["ticker"], r["acml_vol"], now=now_epoch)

    discovery_lookback = float(auto_cfg.get("discovery_momentum_sec", 60))
    min_momentum = float(auto_cfg.get("min_discovery_momentum_pct", 0.4))
    min_vol_surge = float(auto_cfg.get("min_volume_surge_ratio", 1.3))
    decline_lookback = float(auto_cfg.get("decline_lookback_sec", 300))
    min_decline = float(auto_cfg.get("min_decline_pct", 2.0))
    rebound_lookback = float(auto_cfg.get("rebound_lookback_sec", 30))
    min_rebound = float(auto_cfg.get("min_rebound_pct", 0.4))

    ranking_by_ticker = {r["ticker"]: r for r in ranking}  # 중복 시 뒤(losers)가 유지돼도 무방 (동일 종목 기본정보)
    candidates = [
        {
            "ticker": r["ticker"],
            "name": r["name"],
            "price": r["price"],
            "tick_size": kis_api.price_unit(r["price"]),
            "chg_pct": r["chg_pct"],
            "liquidity": r["acml_vol"] * r["price"],
            "momentum": scalp_engine.momentum_pct(r["ticker"], discovery_lookback, now=now_epoch),
            "volume_surge": scalp_engine.volume_surge_ratio(r["ticker"], now=now_epoch),
            "decline": scalp_engine.momentum_pct(r["ticker"], decline_lookback, now=now_epoch),
            "rebound": scalp_engine.momentum_pct(r["ticker"], rebound_lookback, now=now_epoch),
        }
        for r in ranking_by_ticker.values()
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
    min_liquidity = float(auto_cfg.get("min_liquidity", 100_000_000))

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

    # 호가 스프레드 필터 — KIS는 코인처럼 일괄조회가 안 돼 종목별로 확인.
    # max_spread_pct=0(기본)이면 비활성. 조회 실패 시 필터 없이 통과(과도한 제외 방지).
    max_spread = float(auto_cfg.get("max_spread_pct", 0) or 0)
    if max_spread > 0 and picked:
        filtered = []
        for c in picked:
            try:
                sp = kis_api.get_spread_pct(c["ticker"])
            except Exception as e:
                logger.warning("%s 스프레드 조회 실패 — 필터 없이 통과: %s", c["ticker"], e)
                sp = None
            if sp is not None and sp > max_spread:
                logger.info("🚫 [스프레드 초과] %s(%s) %.3f%% > 기준 %.2f%% — 후보 제외",
                            c["name"], c["ticker"], sp, max_spread)
                continue
            filtered.append(c)
        picked = filtered

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
            "stop_loss_pct":      _time_based_stop_loss_pct(auto_cfg),
            "time_stop_sec":      auto_cfg.get("time_stop_sec", 180),
            "time_stop_loss_pct": auto_cfg.get("time_stop_loss_pct", 0.5),
            "fast_rise_momentum_pct":    auto_cfg.get("fast_rise_momentum_pct", 0),
            "fast_rise_take_profit_pct": auto_cfg.get("fast_rise_take_profit_pct", 0),
            "fast_rise_trailing_giveback_pct": auto_cfg.get("fast_rise_trailing_giveback_pct", 0),
            "fast_decline_momentum_pct":  auto_cfg.get("fast_decline_momentum_pct", 0),
            "fast_decline_stop_loss_pct": auto_cfg.get("fast_decline_stop_loss_pct", 0),
            "amount":             auto_cfg.get("amount", 0),
            "qty":                0,
            "max_daily_loss_krw": max_loss,
            "watch_timeout_sec":  auto_cfg.get("watch_timeout_sec", 300),
            "min_volume_surge_ratio": min_vol_surge,
            "discovered_at":      now_epoch,
            "buy_price": 0, "buy_qty": 0, "entered_at": 0,
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


def _fetch_prices(tickers: list) -> dict:
    result = {}

    def _fetch(t):
        try:
            return t, kis_api.get_price(t)
        except Exception as e:
            logger.error("현재가 조회 실패 %s: %s", t, e)
            return t, None

    with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as ex:
        for t, info in ex.map(_fetch, tickers):
            if info:
                result[t] = info
    return result


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


def _force_close(job: dict, cur_price: int, reason: str) -> None:
    ticker = job["ticker"]
    name   = job.get("name", ticker)
    qty    = int(job.get("buy_qty", 0))
    if qty <= 0:
        job["phase"] = "watching"
        return
    try:
        result = kis_api.place_order(ticker, "SELL", qty, order_type="market")
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
        if job.get("source") == "auto":
            job["status"] = "done"  # 자동발굴 잡은 1회성 — 청산 후 슬롯 반환
        pnl_pct = (pnl / (buy_price * qty) * 100) if buy_price > 0 else None
        _record_trade_log(job, buy_price, cur_price, qty, pnl, pnl_pct, reason)
        gist_writer.log_trade(ticker, name, "sell", cur_price, qty, pnl=pnl,
                               pnl_pct=pnl_pct, reason=reason, order_no=result.get("order_no", ""))
        logger.info("★ [%s] 강제청산 %s %d주 @ %d원  손익 %s원", reason, ticker, qty, cur_price, f"{pnl:+,.0f}")
    except Exception as e:
        if scalp_engine.is_phantom_position_error(e):
            logger.warning("%s 강제청산 실패 — 실제 계좌에 없는 포지션으로 판단, 내부 상태 초기화: %s", ticker, e)
            scalp_engine.clear_peak_pnl(ticker)
            job["phase"] = "watching"
            job["buy_price"] = 0
            job["buy_qty"] = 0
            job["entered_at"] = 0
            if job.get("source") == "auto":
                job["status"] = "done"
        else:
            logger.error("%s 강제청산 실패: %s", ticker, e)


def _close_stale_auto_watching(jobs: list) -> bool:
    """장마감 시 그날 미체결로 끝난 자동발굴 watching 잡을 종료 처리.
    (당일 급등 후보였을 뿐이라 다음날까지 들고 있을 이유가 없고, 방치하면
    'watching=active'가 '실행중'으로 표시돼 이미 끝난 매매처럼 보이는 혼란을 줌)
    반환: jobs가 변경되었는지 여부"""
    changed = False
    for job in jobs:
        if job.get("source") == "auto" and job.get("phase") == "watching" and job.get("status") == "active":
            job["status"] = "done"
            job["stop_reason"] = "장마감 — 미체결 종료"
            changed = True
    return changed


def main():
    jobs = gist_writer._read_gist_file("scalp_stock_jobs.json")
    if jobs is None:
        logger.error("scalp_stock_jobs.json Gist 읽기 실패")
        return
    jobs = jobs if isinstance(jobs, list) else []

    if not kis_api.is_any_market_open():
        if _close_stale_auto_watching(jobs):
            gist_writer._write_gist({"scalp_stock_jobs.json": jobs})
        return

    stock_enabled = _is_stock_enabled()
    auto_cfg = _load_auto_config()
    now_epoch = time.time()
    changed = False

    if _auto_discover(jobs, auto_cfg, stock_enabled, now_epoch):
        changed = True

    tickers = list({j["ticker"] for j in jobs if j.get("status") not in ("stopped", "done")})
    if not tickers:
        if changed:
            gist_writer._write_gist({"scalp_stock_jobs.json": jobs})
        return
    price_cache = _fetch_prices(tickers)

    holding_count = sum(1 for j in jobs if j.get("phase") == "holding" and j.get("status") != "stopped")

    for job in jobs:
        if job.get("status") == "stopped":
            continue

        ticker = job["ticker"]
        name   = job.get("name", ticker)
        info   = price_cache.get(ticker)
        if not info:
            continue
        cur_price = int(info["stck_prpr"])
        today_chg = float(info.get("prdy_ctrt", 0))

        if _reset_daily_stats_if_needed(job):
            changed = True

        scalp_engine.record_price(ticker, cur_price, now=now_epoch)
        scalp_engine.record_volume(ticker, int(info.get("acml_vol", 0)), now=now_epoch)

        if not stock_enabled:
            if job.get("phase") == "holding":
                _force_close(job, cur_price, "전체정지 킬스위치")
                changed = True
            continue

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
                                                       tick_size=kis_api.price_unit(buy_price), peak_pnl_pct=peak,
                                                       cur_momentum_pct=cur_momentum)
            if should:
                qty = int(job.get("buy_qty", 0))
                try:
                    result = kis_api.place_order(ticker, "SELL", qty, order_type="market")
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
                    if job.get("source") == "auto":
                        job["status"] = "done"  # 자동발굴 잡은 1회성 — 청산 후 슬롯 반환
                    changed = True
                    scalp_engine.clear_peak_pnl(ticker)
                    _record_trade_log(job, buy_price, cur_price, qty, pnl, pnl_pct, reason)
                    gist_writer.log_trade(ticker, name, "sell", cur_price, qty, pnl=pnl,
                                           pnl_pct=pnl_pct, reason=reason, order_no=result.get("order_no", ""))
                    logger.info("★ [%s] %s %d주 @ %d원  손익 %s원(%.2f%%)",
                                reason, ticker, qty, cur_price, f"{pnl:+,.0f}", pnl_pct)
                except Exception as e:
                    if scalp_engine.is_phantom_position_error(e):
                        logger.warning("%s 청산 실패 — 실제 계좌에 없는 포지션으로 판단, 내부 상태 초기화: %s", ticker, e)
                        job["phase"] = "watching"
                        job["buy_price"] = 0
                        job["buy_qty"] = 0
                        job["entered_at"] = 0
                        if job.get("source") == "auto":
                            job["status"] = "done"
                        changed = True
                        scalp_engine.clear_peak_pnl(ticker)
                    else:
                        logger.error("%s 청산 실패: %s", ticker, e)
            else:
                logger.info("  %s(%s) 보유중 @ %d원 (진입 %d원)", name, ticker, cur_price, int(buy_price))
            continue

        # ── watching: 진입 판단 ─────────────────────────────────
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
                                                    cur_price=cur_price, tick_size=kis_api.price_unit(cur_price),
                                                    ticker=ticker)
        if not should:
            logger.info("  %s(%s) 대기 — %s", name, ticker, reason)
            continue

        qty = int(job.get("qty", 0))
        amount = float(job.get("amount", 0))
        test_until = auto_cfg.get("test_fixed_qty_until", "")
        if qty <= 0 and test_until and _today_str() <= test_until:
            # 초단타 운용 첫날 테스트 — 지정일까지는 예산 계산 대신 고정 소량만 매수
            # (scalp_auto_config.json의 test_fixed_qty_until 지난 뒤엔 자동으로 원래
            # amount 기반 로직으로 돌아감 — 되돌리는 걸 잊어도 문제 없음).
            qty = int(auto_cfg.get("test_fixed_qty", 1))
            logger.info("%s(%s) 테스트모드 — 고정 %d주 (%s까지)", name, ticker, qty, test_until)
        elif qty <= 0 and amount > 0:
            # 설정 예산(amount)이 실제 예수금보다 크면 "주문 가능한 금액을
            # 초과했습니다"로 진입 자체가 계속 실패함 — 예수금 한도 내에서
            # 살 수 있는 만큼만 수량을 줄여서 시도.
            try:
                cash = kis_api.get_balance()["cash"]
            except Exception as e:
                logger.warning("%s(%s) 예수금 조회 실패 — 설정 예산 그대로 사용: %s", name, ticker, e)
                cash = amount
            qty = int(min(amount, cash) // cur_price)
        if qty < 1:
            logger.warning("%s(%s) 수량/금액 미설정 또는 예수금 부족 — 건너뜀", name, ticker)
            continue

        logger.info("★ [진입] %s(%s) @ %d원 — %s", name, ticker, cur_price, reason)
        try:
            result = kis_api.place_order(ticker, "BUY", qty, order_type="market")
            scalp_engine.clear_peak_pnl(ticker)
            job["phase"]      = "holding"
            job["buy_price"]  = cur_price
            job["buy_qty"]    = qty
            job["entered_at"] = now_epoch
            changed = True
            holding_count += 1
            gist_writer.log_trade(ticker, name, "buy", cur_price, qty, order_no=result.get("order_no", ""))
            logger.info("매수 체결 %s %d주 @ %d원", ticker, qty, cur_price)
        except Exception as e:
            logger.error("%s 진입 실패: %s", ticker, e)

    jobs, pruned = scalp_engine.prune_stale_auto_jobs(jobs, _today_str())
    if pruned:
        changed = True
        logger.info("지난 완료 자동발굴 잡 %d건 정리", pruned)

    if changed:
        gist_writer._write_gist({"scalp_stock_jobs.json": jobs})
    gist_writer.flush_trades()


if __name__ == "__main__":
    main()
