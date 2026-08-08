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


def test_volume_surge_insufficient_history():
    se.reset()
    se.record_volume("A", 1000, now=0)
    se.record_volume("A", 1100, now=60)
    # baseline_sec(120) 만큼 관측이 안 됨 — 아직 판단 불가
    assert se.volume_surge_ratio("A", recent_sec=20, baseline_sec=120, now=60) is None
    print("OK: 거래량 관측기간 부족 시 None")


def test_volume_surge_detects_acceleration():
    se.reset()
    now = 0
    # 0~100초: 초당 1씩 완만하게 증가 (평소 속도)
    for t in range(0, 101, 10):
        se.record_volume("A", 1000 + t * 1, now=t)
    # 최근 20초(100~120초)에 갑자기 초당 10씩 급증 (거래량 폭증)
    se.record_volume("A", 1000 + 100 + 10 * 20, now=120)
    ratio = se.volume_surge_ratio("A", recent_sec=20, baseline_sec=120, now=120)
    assert ratio is not None and ratio > 1.5, ratio
    print(f"OK: 최근 거래량 급증 감지 — {ratio:.2f}배")


def test_volume_surge_flat_stays_near_one():
    se.reset()
    for t in range(0, 121, 10):
        se.record_volume("A", 1000 + t * 2, now=t)  # 일정한 속도(초당 2)
    ratio = se.volume_surge_ratio("A", recent_sec=20, baseline_sec=120, now=120)
    assert ratio is not None and 0.8 < ratio < 1.2, ratio
    print(f"OK: 거래량 속도 일정하면 배수 ~1.0 근처 — {ratio:.2f}배")


def test_should_enter_requires_volume_surge_when_configured():
    ok, reason = se.should_enter(momentum=1.0, today_chg_pct=1.0,
                                  params={"entry_momentum_pct": 0.4, "min_volume_surge_ratio": 1.5},
                                  volume_surge=1.1)
    assert ok is False and "거래량" in reason, reason
    print(f"OK: 거래량증가 조건 미달 시 진입 차단 — {reason}")

    ok, reason = se.should_enter(momentum=1.0, today_chg_pct=1.0,
                                  params={"entry_momentum_pct": 0.4, "min_volume_surge_ratio": 1.5},
                                  volume_surge=2.0)
    assert ok is True, reason
    print(f"OK: 거래량증가 조건 충족 시 진입 — {reason}")


def test_should_enter_skips_volume_check_when_not_configured():
    # min_volume_surge_ratio 미설정(수동 잡 기본값) — volume_surge 없어도 기존처럼 통과
    ok, reason = se.should_enter(momentum=1.0, today_chg_pct=1.0, params={"entry_momentum_pct": 0.4})
    assert ok is True, reason
    print("OK: 거래량증가 조건 미설정 시 기존 동작(모멘텀만) 유지")


def test_select_auto_candidates_requires_momentum_and_volume():
    candidates = [
        {"ticker": "A", "chg_pct": 2.0, "liquidity": 1_000_000, "momentum": None, "volume_surge": 2.0},   # 모멘텀 데이터 없음 제외
        {"ticker": "B", "chg_pct": 2.0, "liquidity": 1_000_000, "momentum": 0.3, "volume_surge": 2.0},    # 모멘텀 부족 제외
        {"ticker": "C", "chg_pct": 2.0, "liquidity": 1_000_000, "momentum": 1.0, "volume_surge": 1.1},    # 거래량 부족 제외
        {"ticker": "D", "chg_pct": 2.0, "liquidity": 1_000_000, "momentum": 1.0, "volume_surge": 2.0},    # 통과
    ]
    picked = se.select_auto_candidates(candidates, existing_tickers=set(), max_day_chg_pct=5.0,
                                        min_liquidity=1000, slots=5,
                                        min_momentum_pct=0.5, min_volume_surge=1.5)
    assert [c["ticker"] for c in picked] == ["D"], picked
    print(f"OK: 모멘텀+거래량증가 조건 동시 적용 — {picked}")


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


def test_select_reversal_candidates_requires_decline_and_rebound():
    candidates = [
        {"ticker": "A", "liquidity": 1_000_000, "decline": -1.0, "rebound": 1.0},  # 하락폭 부족
        {"ticker": "B", "liquidity": 1_000_000, "decline": -3.0, "rebound": 0.1},  # 반등폭 부족
        {"ticker": "C", "liquidity": 100, "decline": -3.0, "rebound": 1.0},        # 유동성 부족
        {"ticker": "D", "liquidity": 1_000_000, "decline": None, "rebound": 1.0},  # 관측 부족
        {"ticker": "E", "liquidity": 1_000_000, "decline": -3.0, "rebound": 1.0},  # 통과
    ]
    picked = se.select_reversal_candidates(candidates, existing_tickers=set(), min_liquidity=1000,
                                            slots=5, min_decline_pct=2.0, min_rebound_pct=0.4)
    assert [c["ticker"] for c in picked] == ["E"], picked
    print(f"OK: 급락+반등 조건 동시 충족한 후보만 선정 — {picked}")


def test_select_reversal_candidates_skips_existing_and_respects_slots():
    candidates = [
        {"ticker": "A", "liquidity": 1_000_000, "decline": -3.0, "rebound": 1.0},
        {"ticker": "B", "liquidity": 1_000_000, "decline": -3.0, "rebound": 1.0},
    ]
    picked = se.select_reversal_candidates(candidates, existing_tickers={"A"}, min_liquidity=1000,
                                            slots=5, min_decline_pct=2.0, min_rebound_pct=0.4)
    assert [c["ticker"] for c in picked] == ["B"], picked

    picked2 = se.select_reversal_candidates(candidates, existing_tickers=set(), min_liquidity=1000,
                                             slots=0, min_decline_pct=2.0, min_rebound_pct=0.4)
    assert picked2 == [], picked2
    print("OK: 급락반등 후보 — 중복 제외 + 슬롯 0이면 미선정")


def test_tick_aware_floor_raises_low_threshold():
    # 130원짜리 코인(호가단위 1원) — 1틱=0.77%, 2.5틱=1.92%. 설정값 0.4%보다 훨씬 크므로 상향됨
    floor = se.tick_aware_floor(price=130, tick_size=1, configured_pct=0.4)
    assert abs(floor - 2.5 * (1 / 130 * 100)) < 1e-6, floor
    # 5600원짜리 코인(호가단위 1원) — 1틱=0.018%, 2.5틱=0.045% — 설정값 0.4%가 더 크므로 그대로 유지
    floor2 = se.tick_aware_floor(price=5600, tick_size=1, configured_pct=0.4)
    assert floor2 == 0.4, floor2
    print(f"OK: 저가 코인은 호가단위 기준으로 임계값 자동 상향({floor:.2f}%), 고가 코인은 설정값 유지({floor2:.2f}%)")


def test_should_enter_blocks_pure_tick_noise_momentum():
    # 오늘 실거래 재현: 208원 코인, 1틱(1원)만 올라도 momentum이 0.48%로 나와 설정된 진입모멘텀(0.4%)을
    # 넘겨버리지만, 이는 실제 추세가 아니라 호가 노이즈이므로 tick_size를 주면 차단되어야 함
    ok, reason = se.should_enter(momentum=0.48, today_chg_pct=1.0,
                                  params={"entry_momentum_pct": 0.4},
                                  cur_price=208, tick_size=1)
    assert ok is False, reason
    print(f"OK: 저가 코인 1틱 노이즈로는 진입 차단 — {reason}")

    # 같은 코인이라도 진짜 모멘텀(2.5틱 이상)이면 정상적으로 진입 허용
    ok, reason = se.should_enter(momentum=2.0, today_chg_pct=1.0,
                                  params={"entry_momentum_pct": 0.4},
                                  cur_price=208, tick_size=1)
    assert ok is True, reason
    print(f"OK: 같은 코인도 충분한 모멘텀이면 진입 허용 — {reason}")


def test_should_exit_stop_loss_not_triggered_by_single_tick():
    # 오늘 실거래 재현: 130원 매수, 1틱(1원) 하락한 129원 — 순수 등락률 -0.77%로 손절선(0.5%)을
    # 넘기지만, tick_size를 주면 호가 노이즈로 보정되어 손절되지 않아야 함
    ok, reason = se.should_exit(entry_price=130, cur_price=129, entered_at=0, now=10,
                                 params={"stop_loss_pct": 0.5}, tick_size=1)
    assert ok is False, reason
    print("OK: 저가 코인 1틱 하락은 손절로 처리하지 않음(호가 노이즈 보정)")

    # 진짜로 여러 틱 밀리면(2.5틱 이상 하락) 정상적으로 손절
    ok, reason = se.should_exit(entry_price=130, cur_price=126, entered_at=0, now=10,
                                 params={"stop_loss_pct": 0.5}, tick_size=1)
    assert ok is True and "손절" in reason, reason
    print(f"OK: 충분히 하락하면 정상적으로 손절 — {reason}")


def test_select_auto_candidates_tick_floor_filters_low_price_noise():
    candidates = [
        # 130원, 1틱=0.77%, momentum 0.5%는 노이즈 수준(2.5틱=1.92%에 못 미침) — 제외
        {"ticker": "A", "chg_pct": 2.0, "liquidity": 1_000_000, "price": 130, "tick_size": 1, "momentum": 0.5, "volume_surge": 2.0},
        # 5600원, 1틱=0.018% — momentum 0.5%는 충분히 실질적 — 통과
        {"ticker": "B", "chg_pct": 2.0, "liquidity": 1_000_000, "price": 5600, "tick_size": 1, "momentum": 0.5, "volume_surge": 2.0},
    ]
    picked = se.select_auto_candidates(candidates, existing_tickers=set(), max_day_chg_pct=5.0,
                                        min_liquidity=1000, slots=5,
                                        min_momentum_pct=0.4, min_volume_surge=1.5)
    assert [c["ticker"] for c in picked] == ["B"], picked
    print(f"OK: 자동발굴 단계에서도 저가 코인 호가노이즈 모멘텀은 제외 — {picked}")


def test_should_enter_requires_confirm_cycles_before_entering():
    # 오늘 실거래 재현: AVAX처럼 순간적으로 모멘텀이 튀었다가 바로 꺾이는 "반짝 스파이크"를
    # 걸러내기 위해, ticker를 넘기면 기본 2회 연속 조건 충족해야 진입 허용
    se.reset("AVAX")
    params = {"entry_momentum_pct": 0.4}

    # 1회차: 조건은 충족하지만 아직 확인 중 — 진입 보류
    ok, reason = se.should_enter(momentum=0.6, today_chg_pct=1.0, params=params, ticker="AVAX")
    assert ok is False and "확인 중" in reason, reason
    print(f"OK: 1회차 충족은 진입 보류 — {reason}")

    # 다음 사이클에 모멘텀이 꺾이면(스파이크였던 것) 카운트 리셋되고 여전히 진입 안 함
    ok, reason = se.should_enter(momentum=0.1, today_chg_pct=1.0, params=params, ticker="AVAX")
    assert ok is False, reason
    ok, reason = se.should_enter(momentum=0.6, today_chg_pct=1.0, params=params, ticker="AVAX")
    assert ok is False and "1/2" in reason, reason
    print("OK: 중간에 조건 미달되면 확인 카운트가 리셋됨 (반짝 스파이크 필터링)")

    # 2회 연속 충족하면 그때 진입
    ok, reason = se.should_enter(momentum=0.6, today_chg_pct=1.0, params=params, ticker="AVAX")
    assert ok is True, reason
    print(f"OK: 2회 연속 충족 시 진입 허용 — {reason}")

    se.reset("AVAX")


def test_should_enter_ticker_none_skips_confirm_for_backward_compat():
    # ticker를 안 넘기면(기존 호출부/테스트) 확인 절차 없이 기존처럼 즉시 진입 — 하위호환
    ok, reason = se.should_enter(momentum=0.6, today_chg_pct=1.0, params={"entry_momentum_pct": 0.4})
    assert ok is True, reason
    print("OK: ticker 미지정 시 기존처럼 즉시 진입 (하위호환)")


def test_should_exit_trailing_take_profit_lets_winner_run():
    # 오늘 실거래 재현: ZKP는 65.1원 매수 후 익절선(+1.0%) 근처인 65.9원에서 바로 팔았지만
    # 그 뒤로도 68.2원(+4.76%)까지 계속 올랐음. peak_pnl_pct를 넘기면 목표가 도달 즉시 팔지 않고
    # 고점 대비 trailing_giveback_pct(기본 0.3%)만큼 되돌릴 때까지 계속 보유해야 함
    params = {"take_profit_pct": 1.0, "stop_loss_pct": 0.5}

    # 익절선(1.0%)을 막 넘긴 시점 — 아직 고점에서 안 밀렸으니 계속 보유
    ok, reason = se.should_exit(entry_price=100, cur_price=101.0, entered_at=0, now=10,
                                 params=params, peak_pnl_pct=1.0)
    assert ok is False, reason
    print("OK: 목표가에 막 도달한 시점엔 트레일링 대기 (즉시 매도 안 함)")

    # 계속 올라서 고점이 갱신됨 — 여전히 안 밀렸으니 보유
    ok, reason = se.should_exit(entry_price=100, cur_price=104.0, entered_at=0, now=20,
                                 params=params, peak_pnl_pct=4.0)
    assert ok is False, reason
    print("OK: 고점이 계속 갱신되는 동안은 계속 보유해서 추세를 따라감")

    # 고점(4.0%) 대비 0.3% 이상 되돌리면 그때 확정 익절
    ok, reason = se.should_exit(entry_price=100, cur_price=103.6, entered_at=0, now=30,
                                 params=params, peak_pnl_pct=4.0)
    assert ok is True and "고점" in reason, reason
    print(f"OK: 고점 대비 되돌림이 나오면 그때 익절 확정 — {reason}")


def test_should_exit_take_profit_immediate_when_no_peak_tracking():
    # peak_pnl_pct를 안 넘기면(하위호환) 기존처럼 목표가 도달 즉시 익절
    ok, reason = se.should_exit(entry_price=100, cur_price=101.0, entered_at=0, now=10,
                                 params={"take_profit_pct": 1.0})
    assert ok is True and "고점" not in reason, reason
    print(f"OK: peak_pnl_pct 미지정 시 기존처럼 즉시 익절 — {reason}")


def test_update_peak_pnl_tracks_max_per_ticker():
    se.reset("PEAKTEST")
    assert se.update_peak_pnl("PEAKTEST", 0.5) == 0.5
    assert se.update_peak_pnl("PEAKTEST", 2.0) == 2.0
    assert se.update_peak_pnl("PEAKTEST", 1.0) == 2.0  # 내려가도 고점은 유지
    se.clear_peak_pnl("PEAKTEST")
    assert se.update_peak_pnl("PEAKTEST", 0.3) == 0.3  # 클리어 후엔 새로 시작
    se.reset("PEAKTEST")
    print("OK: 티커별 고점 손익률 추적 및 초기화")


def test_select_reversal_candidates_requires_volume_surge_when_configured():
    # 오늘 실거래 재현: BORA는 반등폭은 기준을 넘겼지만(매수세 확인 없이) 27분간 정체하다 손절됨.
    # 반등 모드에도 거래량 증가 조건을 추가해 "힘없는 바운스"를 걸러낼 수 있어야 함
    candidates = [
        {"ticker": "A", "liquidity": 1_000_000, "decline": -3.0, "rebound": 1.0, "volume_surge": 1.1},   # 거래량 부족
        {"ticker": "B", "liquidity": 1_000_000, "decline": -3.0, "rebound": 1.0, "volume_surge": 2.0},   # 통과
    ]
    picked = se.select_reversal_candidates(candidates, existing_tickers=set(), min_liquidity=1000,
                                            slots=5, min_decline_pct=2.0, min_rebound_pct=0.4,
                                            min_volume_surge=1.5)
    assert [c["ticker"] for c in picked] == ["B"], picked
    print(f"OK: 반등 모드도 거래량 증가 미달이면 제외 — {picked}")

    # min_volume_surge=0(미설정)이면 기존처럼 거래량 검사 없이 통과 — 하위호환
    picked2 = se.select_reversal_candidates(candidates, existing_tickers=set(), min_liquidity=1000,
                                             slots=5, min_decline_pct=2.0, min_rebound_pct=0.4)
    assert [c["ticker"] for c in picked2] == ["A", "B"], picked2
    print("OK: min_volume_surge 미설정 시 기존처럼 거래량 검사 없이 통과 (하위호환)")


def test_should_exit_stagnation_closes_flat_position_after_long_hold():
    # 오늘 실거래 재현: BORA는 27분(1620초)간 손익이 거의 0%대로 정체했음.
    # time_stop_sec(180초)의 3배(540초)를 넘겨도 손익이 ±0.2% 안에 머물러 있으면 슬롯 반환을 위해 청산
    params = {"take_profit_pct": 1.0, "stop_loss_pct": 0.5, "time_stop_sec": 180}

    # 540초 전에는 정체라도 그냥 계속 보유 (아직 판단 시점 아님)
    ok, reason = se.should_exit(entry_price=100, cur_price=100.05, entered_at=0, now=400, params=params)
    assert ok is False, reason
    print("OK: 정체 판단 시점(3배) 전에는 그대로 보유")

    # 540초 넘었고 손익이 ±0.2% 안(정체)이면 청산
    ok, reason = se.should_exit(entry_price=100, cur_price=100.05, entered_at=0, now=600, params=params)
    assert ok is True and "정체" in reason, reason
    print(f"OK: 오래 정체된 포지션은 슬롯 반환을 위해 청산 — {reason}")

    # 540초 넘었어도 손익이 밴드를 벗어나 있으면(방향이 생긴 것) 정체 청산 대상 아님
    ok, reason = se.should_exit(entry_price=100, cur_price=100.3, entered_at=0, now=600, params=params)
    assert ok is False, reason
    print("OK: 방향이 생긴 포지션은 정체 청산 대상 아님 (계속 관찰)")


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
    test_volume_surge_insufficient_history()
    test_volume_surge_detects_acceleration()
    test_volume_surge_flat_stays_near_one()
    test_should_enter_requires_volume_surge_when_configured()
    test_should_enter_skips_volume_check_when_not_configured()
    test_select_auto_candidates_requires_momentum_and_volume()
    test_should_give_up_watching()
    test_select_auto_candidates_filters_and_caps()
    test_select_auto_candidates_skips_existing()
    test_select_reversal_candidates_requires_decline_and_rebound()
    test_select_reversal_candidates_skips_existing_and_respects_slots()
    test_tick_aware_floor_raises_low_threshold()
    test_should_enter_blocks_pure_tick_noise_momentum()
    test_should_exit_stop_loss_not_triggered_by_single_tick()
    test_select_auto_candidates_tick_floor_filters_low_price_noise()
    test_should_enter_requires_confirm_cycles_before_entering()
    test_should_enter_ticker_none_skips_confirm_for_backward_compat()
    test_should_exit_trailing_take_profit_lets_winner_run()
    test_should_exit_take_profit_immediate_when_no_peak_tracking()
    test_update_peak_pnl_tracks_max_per_ticker()
    test_select_reversal_candidates_requires_volume_surge_when_configured()
    test_should_exit_stagnation_closes_flat_position_after_long_hold()
    print("\n전체 통과")
