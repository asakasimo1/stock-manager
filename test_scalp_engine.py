"""scalp_engine.py 동작 검증 (합성 데이터) — python3 test_scalp_engine.py 로 직접 실행"""
import scalp_engine as se


def test_momentum_insufficient_data():
    se.reset()
    se.record_price("A", 100, now=0)
    assert se.momentum_pct("A", lookback_sec=30, now=0) is None
    print("OK: 데이터 1개뿐일 때 momentum None")


def test_momentum_calc():
    se.reset()
    se.record_price("A", 100, now=0)
    se.record_price("A", 101, now=15)
    se.record_price("A", 102, now=30)
    m = se.momentum_pct("A", lookback_sec=30, now=30)
    assert m is not None and abs(m - 2.0) < 1e-9, m
    print(f"OK: 30초간 100→102 momentum={m:.2f}%")


def test_should_enter_blocks_when_overheated():
    ok, reason = se.should_enter(momentum=1.0, today_chg_pct=6.0, params={})
    assert ok is False, reason
    print(f"OK: 당일 +6% 과열 시 진입 차단 — {reason}")


def test_should_enter_blocks_when_momentum_low():
    ok, reason = se.should_enter(momentum=0.1, today_chg_pct=1.0, params={"entry_momentum_pct": 0.4})
    assert ok is False, reason
    print(f"OK: 모멘텀 미달 시 진입 차단 — {reason}")


def test_should_enter_passes():
    ok, reason = se.should_enter(momentum=0.5, today_chg_pct=1.0, params={"entry_momentum_pct": 0.4, "max_day_chg_pct": 5.0})
    assert ok is True, reason
    print(f"OK: 조건 충족 시 진입 — {reason}")


def test_should_exit_take_profit():
    ok, reason = se.should_exit(entry_price=100, cur_price=100.7, entered_at=0, now=10, params={"take_profit_pct": 0.6})
    assert ok is True and "익절" in reason, reason
    print(f"OK: 익절 트리거 — {reason}")


def test_should_exit_stop_loss():
    ok, reason = se.should_exit(entry_price=100, cur_price=99.5, entered_at=0, now=10, params={"stop_loss_pct": 0.4})
    assert ok is True and "손절" in reason, reason
    print(f"OK: 손절 트리거 — {reason}")


def test_should_exit_time_stop():
    ok, reason = se.should_exit(entry_price=100, cur_price=100.1, entered_at=0, now=200, params={"time_stop_sec": 180, "take_profit_pct": 0.6, "stop_loss_pct": 0.4})
    assert ok is True and "시간초과" in reason, reason
    print(f"OK: 시간초과 강제청산 — {reason}")


def test_should_exit_holds():
    ok, reason = se.should_exit(entry_price=100, cur_price=100.1, entered_at=0, now=10, params={"take_profit_pct": 0.6, "stop_loss_pct": 0.4, "time_stop_sec": 180})
    assert ok is False, reason
    print("OK: 조건 미달 시 보유 유지")


if __name__ == "__main__":
    test_momentum_insufficient_data()
    test_momentum_calc()
    test_should_enter_blocks_when_overheated()
    test_should_enter_blocks_when_momentum_low()
    test_should_enter_passes()
    test_should_exit_take_profit()
    test_should_exit_stop_loss()
    test_should_exit_time_stop()
    test_should_exit_holds()
    print("\n전체 통과")
