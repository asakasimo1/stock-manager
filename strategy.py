"""
전략 프리셋 — 가벼운 의존성만 사용 (job 스크립트에서 직접 import)
"""

import os
from dotenv import load_dotenv
from backtest import Cfg

load_dotenv()

STRATEGY_PRESETS = {
    "optimized": {
        "desc":             "최적 전략 (백테스트 최우선, +96% / 낙폭 -27.5%)",
        "volume_mult":      1.5,
        "day_return_min":   0.005,
        "stop_loss":        -0.04,
        "take_profit":      0.20,
        "hold_days":        15,
        "max_positions":    5,
        "max_order_amount": 2_000_000,
        "max_daily_loss":   300_000,
        # 트레일링 스탑: +10% 달성 후 고점에서 -5% 하락 시 청산
        "trail_trigger":    0.10,
        "trail_pct":        0.05,
    },
    "war_risk": {
        "desc":             "전쟁/지정학 리스크 장기화 — 빠른 손절·단기 보유",
        "volume_mult":      2.5,
        "day_return_min":   0.01,
        "stop_loss":        -0.03,
        "take_profit":      0.08,
        "hold_days":        4,
        "max_positions":    3,
        "max_order_amount": 1_000_000,
        "max_daily_loss":   150_000,
        # 단기 전략: 낮은 트리거, 타이트한 트레일링
        "trail_trigger":    0.05,
        "trail_pct":        0.03,
    },
    "hold": {
        "desc":             "버티기 — 전쟁 종전·반등 기대, 익절폭 크게·장기 보유",
        "volume_mult":      2.0,
        "day_return_min":   0.005,
        "stop_loss":        -0.07,
        "take_profit":      0.20,
        "hold_days":        20,
        "max_positions":    5,
        "max_order_amount": 2_000_000,
        "max_daily_loss":   400_000,
        # 장기 전략: 높은 트리거, 여유 있는 트레일링
        "trail_trigger":    0.12,
        "trail_pct":        0.07,
    },
    "defensive": {
        "desc":             "방어형 — 강한 신호만 진입, 손실 최소화",
        "volume_mult":      3.0,
        "day_return_min":   0.01,
        "stop_loss":        -0.03,
        "take_profit":      0.08,
        "hold_days":        5,
        "max_positions":    2,
        "max_order_amount": 1_000_000,
        "max_daily_loss":   100_000,
        # 방어형: 조기 트리거, 타이트한 트레일링
        "trail_trigger":    0.05,
        "trail_pct":        0.03,
    },
    "aggressive": {
        "desc":             "공격형 — 손절 여유, 장기 익절 추구",
        "volume_mult":      2.0,
        "day_return_min":   0.005,
        "stop_loss":        -0.07,
        "take_profit":      0.15,
        "hold_days":        15,
        "max_positions":    5,
        "max_order_amount": 2_000_000,
        "max_daily_loss":   500_000,
        "trail_trigger":    0.10,
        "trail_pct":        0.07,
    },
    "rsi_rebound": {
        "desc":             "RSI 반등 + EMA 크로스 — 과매도 회복 구간 진입 (손실 방어형)",
        # 진입: RSI 30~55 구간 + EMA5>EMA20 + 거래량 1.2배 이상 + 양봉
        "volume_mult":      1.2,
        "day_return_min":   0.0,
        "rsi_min":          30.0,
        "rsi_max":          55.0,
        "use_ema_cross":    True,
        "ema_fast":         5,
        "ema_slow":         20,
        "stop_loss":        -0.05,
        "take_profit":      0.12,
        "hold_days":        10,
        "max_positions":    5,
        "max_order_amount": 2_000_000,
        "max_daily_loss":   300_000,
        "trail_trigger":    0.07,
        "trail_pct":        0.04,
    },
}


def load_strategy(name: str) -> tuple[Cfg, dict]:
    preset = STRATEGY_PRESETS.get(name)
    if preset is None:
        preset = STRATEGY_PRESETS["optimized"]
        name   = "optimized"

    # 환경변수로 오버라이드 가능 (GitHub Secrets 또는 .env)
    # 예: MAX_ORDER_AMOUNT=100000 / TAKE_PROFIT=0.15 / TRAIL_TRIGGER=0.08
    preset = dict(preset)  # 원본 보호
    env_order         = os.getenv("MAX_ORDER_AMOUNT")
    env_loss          = os.getenv("MAX_DAILY_LOSS")
    env_tp            = os.getenv("TAKE_PROFIT")
    env_sl            = os.getenv("STOP_LOSS")
    env_trail_trigger = os.getenv("TRAIL_TRIGGER")
    env_trail_pct     = os.getenv("TRAIL_PCT")
    if env_order:         preset["max_order_amount"] = int(env_order)
    if env_loss:          preset["max_daily_loss"]   = int(env_loss)
    if env_tp:            preset["take_profit"]       = float(env_tp)
    if env_sl:            preset["stop_loss"]         = float(env_sl)
    if env_trail_trigger: preset["trail_trigger"]     = float(env_trail_trigger)
    if env_trail_pct:     preset["trail_pct"]         = float(env_trail_pct)

    cfg = Cfg()
    cfg.volume_mult    = preset["volume_mult"]
    cfg.day_return_min = preset.get("day_return_min", 0.0)
    cfg.rsi_min        = preset.get("rsi_min",        0.0)
    cfg.rsi_max        = preset.get("rsi_max",        100.0)
    cfg.use_ema_cross  = preset.get("use_ema_cross",  False)
    cfg.ema_fast       = preset.get("ema_fast",       5)
    cfg.ema_slow       = preset.get("ema_slow",       20)
    cfg.stop_loss      = preset["stop_loss"]
    cfg.take_profit    = preset["take_profit"]
    cfg.hold_days      = preset["hold_days"]
    cfg.max_positions  = preset["max_positions"]
    cfg.trail_trigger  = preset.get("trail_trigger", 0.10)
    cfg.trail_pct      = preset.get("trail_pct",     0.05)
    return cfg, preset
