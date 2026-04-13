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
        "volume_mult":      2.2,
        "day_return_min":   0.005,
        "stop_loss":        -0.04,
        "take_profit":      0.12,
        "hold_days":        7,
        "max_positions":    5,
        "max_order_amount": 2_000_000,
        "max_daily_loss":   300_000,
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
    },
}


def load_strategy(name: str) -> tuple[Cfg, dict]:
    preset = STRATEGY_PRESETS.get(name)
    if preset is None:
        preset = STRATEGY_PRESETS["optimized"]
        name   = "optimized"

    cfg = Cfg()
    cfg.volume_mult    = preset["volume_mult"]
    cfg.day_return_min = preset["day_return_min"]
    cfg.stop_loss      = preset["stop_loss"]
    cfg.take_profit    = preset["take_profit"]
    cfg.hold_days      = preset["hold_days"]
    cfg.max_positions  = preset["max_positions"]
    return cfg, preset
