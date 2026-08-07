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


def test_should_exit_time_stop_closes_when_loss_exceeds_threshold():
    # 손절선(-0.4%)도, time_stop_loss_pct(-0.5%)도 아직 안 넘은 -0.3% — 계속 보유
    ok, reason = se.should_exit(entry_price=100, cur_price=99.7, entered_at=0, now=200,
                                 params={"time_stop_sec": 180, "take_profit_pct": 0.6, "stop_loss_pct": 0.4, "time_stop_loss_pct": 0.5})
    assert ok is False, reason
    print("OK: 시간초과지만 손실이 time_stop_loss_pct 이내면 계속 보유")

    # -0.5%를 넘긴 경우엔 시간초과 손절
    ok, reason = se.should_exit(entry_price=100, cur_price=99.4, entered_at=0, now=200,
                                 params={"time_stop_sec": 180, "take_profit_pct": 0.6, "stop_loss_pct": 100, "time_stop_loss_pct": 0.5})
    assert ok is True and "시간초과" in reason, reason
    print(f"OK: 시간초과 + 손실 -0.5% 초과 시 청산 — {reason}")


def test_should_exit_time_stop_holds_when_flat_or_profit():
    # 3분 지나도 소폭 플러스면 무조건 청산하지 않고 계속 관찰 (기존 "시간초과=무조건 청산" 완화)
    ok, reason = se.should_exit(entry_price=100, cur_price=100.1, entered_at=0, now=200,
                                 params={"time_stop_sec": 180, "take_profit_pct": 0.6, "stop_loss_pct": 0.4, "time_stop_loss_pct": 0.5})
    assert ok is False, reason
    print("OK: 시간초과 시점에 플러스/보합이면 무조건 청산하지 않음")


def test_should_exit_holds():
    ok, reason = se.should_exit(entry_price=100, cur_price=100.1, entered_at=0, now=10, params={"take_profit_pct": 0.6, "stop_loss_pct": 0.4, "time_stop_sec": 180})
    assert ok is False, reason
    print("OK: 조건 미달 시 보유 유지")


def test_should_give_up_watching():
    assert se.should_give_up_watching(discovered_at=1000, now=1000 + 299, timeout_sec=300) is False
    assert se.should_give_up_watching(discovered_at=1000, now=1000 + 300, timeout_sec=300) is True
    assert se.should_give_up_watching(discovered_at=0, now=5, timeout_sec=300) is False  # 아직 5초밖에 안 지남
    assert se.should_give_up_watching(discovered_at=0, now=1, timeout_sec=0) is False    # timeout 비활성화
    print("OK: watching 포기 타임아웃 판단")


def test_select_auto_candidates_filters_and_caps():
    candidates = [
        {"ticker": "A", "chg_pct": 2.0, "liquidity": 1_000_000},   # 통과
        {"ticker": "B", "chg_pct": 8.0, "liquidity": 1_000_000},   # 과열 제외
        {"ticker": "C", "chg_pct": 1.0, "liquidity": 100},          # 유동성 부족 제외
        {"ticker": "D", "chg_pct": -1.0, "liquidity": 1_000_000},  # 하락 제외
        {"ticker": "E", "chg_pct": 3.0, "liquidity": 1_000_000},   # 통과 (슬롯 부족으로 잘림)
    ]
    picked = se.select_auto_candidates(candidates, existing_tickers=set(), max_day_chg_pct=5.0, min_liquidity=1000, slots=1)
    assert [c["ticker"] for c in picked] == ["A"], picked
    print(f"OK: 과열/유동성/하락 필터링 + 슬롯 제한 — {picked}")


def test_select_auto_candidates_skips_existing():
    candidates = [{"ticker": "A", "chg_pct": 2.0, "liquidity": 1_000_000}]
    picked = se.select_auto_candidates(candidates, existing_tickers={"A"}, max_day_chg_pct=5.0, min_liquidity=1000, slots=2)
    assert picked == [], picked
    print("OK: 이미 진행중인 티커 중복 제외")


if __name__ == "__main__":
    test_momentum_insufficient_data()
    test_momentum_calc()
    test_should_enter_blocks_when_overheated()
    test_should_enter_blocks_when_momentum_low()
    test_should_enter_passes()
    test_should_exit_take_profit()
    test_should_exit_stop_loss()
    test_should_exit_time_stop_closes_when_loss_exceeds_threshold()
    test_should_exit_time_stop_holds_when_flat_or_profit()
    test_should_exit_holds()
    test_should_give_up_watching()
    test_select_auto_candidates_filters_and_caps()
    test_select_auto_candidates_skips_existing()
    print("\n전체 통과")
