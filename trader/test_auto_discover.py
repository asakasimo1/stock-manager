"""_auto_discover() 동작 검증 (합성 데이터, 네트워크 호출 없음) — python3 test_auto_discover.py 로 직접 실행"""
import job_scalp_coin
import job_scalp_stock
import kis_api
import upbit_api
import scalp_engine as se

# 모멘텀/거래량증가 게이팅과 무관하게 기존 필터(과열/유동성/슬롯)만 검증하는 테스트에서는
# min_discovery_momentum_pct=0, min_volume_surge_ratio=0 으로 새 조건을 꺼둔다.
_NO_MOMENTUM_GATE = {"min_discovery_momentum_pct": 0, "min_volume_surge_ratio": 0}


def test_coin_auto_discover_creates_job():
    se.reset()
    jobs = []
    auto_cfg = {"enabled": True, "max_concurrent": 2, "max_day_chg_pct": 5.0,
                "min_liquidity": 1000, "krw_amount": 10000, "max_daily_loss_krw": -20000, **_NO_MOMENTUM_GATE}
    price_cache = {
        "KRW-AAA": {"chg_pct": 2.0, "price": 1000, "volume": 100000},   # 통과
        "KRW-BBB": {"chg_pct": 9.0, "price": 1000, "volume": 100000},   # 과열 제외
    }
    changed = job_scalp_coin._auto_discover(jobs, auto_cfg, price_cache, coin_enabled=True, now_epoch=1000.0)
    assert changed is True
    assert len(jobs) == 1 and jobs[0]["ticker"] == "KRW-AAA" and jobs[0]["source"] == "auto"
    assert jobs[0]["status"] == "active" and jobs[0]["phase"] == "watching"
    print("OK: 코인 자동발굴 — 과열종목 제외 + watching 잡 생성 (모멘텀/거래량 게이트 비활성 시)")


def test_coin_auto_discover_respects_slots():
    se.reset()
    jobs = [
        {"ticker": "KRW-X", "source": "auto", "status": "active", "stats_date": "2099-01-01", "realized_pnl_today": 0},
        {"ticker": "KRW-Y", "source": "auto", "status": "active", "stats_date": "2099-01-01", "realized_pnl_today": 0},
    ]
    auto_cfg = {"enabled": True, "max_concurrent": 2, "max_day_chg_pct": 5.0, "min_liquidity": 0, **_NO_MOMENTUM_GATE}
    price_cache = {"KRW-Z": {"chg_pct": 2.0, "price": 1000, "volume": 100000}}
    changed = job_scalp_coin._auto_discover(jobs, auto_cfg, price_cache, coin_enabled=True, now_epoch=1000.0)
    assert changed is False and len(jobs) == 2
    print("OK: 코인 자동발굴 — 슬롯(max_concurrent) 초과 시 신규 생성 안 함")


def test_coin_auto_discover_disabled_noop():
    se.reset()
    jobs = []
    changed = job_scalp_coin._auto_discover(jobs, {"enabled": False}, {"KRW-A": {"chg_pct": 2.0, "price": 1, "volume": 1}}, True, 0)
    assert changed is False and jobs == []
    print("OK: 코인 자동발굴 — enabled=False 시 아무 것도 안 함")


def test_coin_auto_discover_requires_momentum_and_volume_surge():
    """모멘텀/거래량증가 게이트가 켜져 있으면(기본값) 이력이 충분히 쌓이기 전엔 후보를 못 찾고,
    실제로 급등+거래량증가 패턴이 나타나야만 후보로 선정됨을 검증 (엔진 단위테스트가 아니라
    job_scalp_coin._auto_discover를 통해 실제로 연결되어 있는지 확인하는 배선 테스트)"""
    se.reset()
    jobs = []
    auto_cfg = {"enabled": True, "max_concurrent": 2, "max_day_chg_pct": 5.0, "min_liquidity": 0,
                "krw_amount": 10000, "max_daily_loss_krw": -20000,
                "discovery_momentum_sec": 60, "min_discovery_momentum_pct": 0.5, "min_volume_surge_ratio": 1.5}

    # 0~120초: 가격/거래량 모두 평온 (모멘텀도 거래량도 안 늘어남)
    for t in range(0, 121, 30):
        price_cache = {"KRW-CALM": {"chg_pct": 1.0, "price": 1000, "volume": 100000 + t * 10}}
        changed = job_scalp_coin._auto_discover(jobs, auto_cfg, price_cache, coin_enabled=True, now_epoch=float(t))
        assert changed is False and jobs == [], f"t={t}: 평온한데 후보가 선정됨"
    print("OK: 평온한 흐름에서는 이력이 쌓여도 후보 선정 안 함")

    # 150초 시점: 가격이 갑자기 튀고(60초간 +1.0%) 거래량도 급증
    price_cache = {"KRW-CALM": {"chg_pct": 2.0, "price": 1010, "volume": 100000 + 120 * 10 + 50000}}
    changed = job_scalp_coin._auto_discover(jobs, auto_cfg, price_cache, coin_enabled=True, now_epoch=150.0)
    assert changed is True and len(jobs) == 1 and jobs[0]["ticker"] == "KRW-CALM", jobs
    print("OK: 갑자기 급등+거래량증가 시에만 후보로 선정 (job_scalp_coin 배선 확인)")


def test_stock_auto_discover_creates_job(monkeypatch):
    se.reset()
    job_scalp_stock._last_discover_at = -9999.0  # 다른 테스트와 스캔 주기 스로틀 공유 방지
    jobs = []
    auto_cfg = {"enabled": True, "max_concurrent": 2, "max_day_chg_pct": 5.0,
                "min_liquidity": 1000, "amount": 500000, "max_daily_loss_krw": -30000, **_NO_MOMENTUM_GATE}
    monkeypatch.setattr(kis_api, "get_fluctuation_ranking", lambda top_n=30, sort="gainers": [
        {"ticker": "005930", "name": "삼성전자", "price": 70000, "chg_pct": 2.5, "acml_vol": 1000000},
        {"ticker": "000660", "name": "SK하이닉스", "price": 150000, "chg_pct": 12.0, "acml_vol": 1000000},
    ] if sort == "gainers" else [])
    changed = job_scalp_stock._auto_discover(jobs, auto_cfg, stock_enabled=True, now_epoch=1000.0)
    assert changed is True
    assert len(jobs) == 1 and jobs[0]["ticker"] == "005930" and jobs[0]["source"] == "auto"
    assert jobs[0]["discovery_mode"] == "surge"
    print("OK: 주식 자동발굴 — 등락률 순위에서 과열종목 제외 + watching 잡 생성 (모멘텀/거래량 게이트 비활성 시)")


def test_stock_auto_discover_reversal_mode(monkeypatch):
    se.reset()
    job_scalp_stock._last_discover_at = -9999.0  # 다른 테스트와 스캔 주기 스로틀 공유 방지
    jobs = []
    auto_cfg = {"enabled": True, "max_concurrent": 2, "max_day_chg_pct": 5.0, "min_liquidity": 0,
                "amount": 500000, "max_daily_loss_krw": -30000, "reversal_enabled": True,
                "decline_lookback_sec": 300, "min_decline_pct": 2.0,
                "rebound_lookback_sec": 30, "min_rebound_pct": 0.4,
                "surge_enabled": False}

    def fake_ranking(top_n=30, sort="gainers"):
        if sort != "losers":
            return []
        return [{"ticker": "900001", "name": "급락주", "price": 1000, "chg_pct": -5.0, "acml_vol": 1_000_000}]

    monkeypatch.setattr(kis_api, "get_fluctuation_ranking", fake_ranking)

    # 0~120초: 완만하게 하락 (아직 급락 판단 기준 -2%까지는 안 옴 + 반등도 없음)
    for t in range(0, 121, 30):
        price = 1000 - t * 0.5
        monkeypatch.setattr(kis_api, "get_fluctuation_ranking",
                             lambda top_n=30, sort="gainers", _p=price: (
                                 [] if sort != "losers" else
                                 [{"ticker": "900001", "name": "급락주", "price": _p, "chg_pct": -5.0, "acml_vol": 1_000_000}]
                             ))
        changed = job_scalp_stock._auto_discover(jobs, auto_cfg, stock_enabled=True, now_epoch=float(t))
        assert changed is False and jobs == [], f"t={t}: 아직 반등 안 했는데 후보 선정됨"
    print("OK: 완만한 하락 흐름에서는 후보 선정 안 함 (반등 신호 없음)")

    # 150초: 급하게 반등 (30초 전보다 +2% 반등, 300초 누적으로는 여전히 하락)
    monkeypatch.setattr(kis_api, "get_fluctuation_ranking",
                         lambda top_n=30, sort="gainers": (
                             [] if sort != "losers" else
                             [{"ticker": "900001", "name": "급락주", "price": 960, "chg_pct": -4.0, "acml_vol": 1_500_000}]
                         ))
    changed = job_scalp_stock._auto_discover(jobs, auto_cfg, stock_enabled=True, now_epoch=150.0)
    assert changed is True and len(jobs) == 1 and jobs[0]["ticker"] == "900001", jobs
    assert jobs[0]["discovery_mode"] == "reversal"
    print("OK: 급락 후 반등 시에만 후보로 선정 (job_scalp_stock 배선 확인)")


def test_stock_close_stale_auto_watching():
    jobs = [
        {"ticker": "A", "source": "auto", "phase": "watching", "status": "active"},   # 종료 대상
        {"ticker": "B", "source": "auto", "phase": "holding", "status": "active"},    # 보유중 — 건드리면 안 됨
        {"ticker": "C", "source": "manual", "phase": "watching", "status": "active"}, # 수동 등록 — 건드리면 안 됨
        {"ticker": "D", "source": "auto", "phase": "watching", "status": "done"},     # 이미 종료 — 그대로
    ]
    changed = job_scalp_stock._close_stale_auto_watching(jobs)
    assert changed is True
    assert jobs[0]["status"] == "done" and "stop_reason" in jobs[0]
    assert jobs[1]["status"] == "active"  # holding은 그대로
    assert jobs[2]["status"] == "active"  # manual은 그대로
    print("OK: 장마감 시 미체결 자동발굴 watching 잡만 종료 처리")


def test_poll_executed_volume_accepts_cancel_state_when_fully_filled(monkeypatch):
    # 실거래 재현: Upbit는 ord_type="price" 시장가 매수가 완전 체결돼도 state를 "done"이 아니라
    # "cancel"로 반환한다. remaining_volume==0이면 state와 무관하게 체결 확정으로 봐야
    # 매도 시 잔량(dust)이 안 남는다 (예전엔 state=="done"만 봐서 매번 추정치로 폴백했음)
    monkeypatch.setattr(upbit_api, "get_order", lambda uuid: {
        "uuid": uuid, "state": "cancel", "executed_volume": 153.84615384,
        "remaining_volume": 0.0, "avg_price": 0.0, "trades_count": 1,
    })
    qty = job_scalp_coin._poll_executed_volume("fake-uuid", fallback=153.76923076, tries=1, delay=0)
    assert qty == 153.84615384, qty
    print("OK: state='cancel'이어도 remaining_volume==0이면 실제 체결량 사용 (dust 방지)")


def test_poll_executed_volume_falls_back_when_still_pending(monkeypatch):
    monkeypatch.setattr(upbit_api, "get_order", lambda uuid: {
        "uuid": uuid, "state": "wait", "executed_volume": 0.0,
        "remaining_volume": 100.0, "avg_price": 0.0, "trades_count": 0,
    })
    qty = job_scalp_coin._poll_executed_volume("fake-uuid", fallback=99.0, tries=2, delay=0)
    assert qty == 99.0, qty
    print("OK: 아직 미체결분이 남아있으면(remaining_volume>0) 추정치로 폴백")


class _FakeMonkeypatch:
    def setattr(self, obj, name, value):
        self._orig = (obj, name, getattr(obj, name))
        setattr(obj, name, value)

    def undo(self):
        obj, name, orig = self._orig
        setattr(obj, name, orig)


if __name__ == "__main__":
    test_coin_auto_discover_creates_job()
    test_coin_auto_discover_respects_slots()
    test_coin_auto_discover_disabled_noop()
    test_coin_auto_discover_requires_momentum_and_volume_surge()
    mp = _FakeMonkeypatch()
    test_stock_auto_discover_creates_job(mp)
    mp.undo()
    mp = _FakeMonkeypatch()
    test_stock_auto_discover_reversal_mode(mp)
    mp.undo()
    test_stock_close_stale_auto_watching()
    mp = _FakeMonkeypatch()
    test_poll_executed_volume_accepts_cancel_state_when_fully_filled(mp)
    mp.undo()
    mp = _FakeMonkeypatch()
    test_poll_executed_volume_falls_back_when_still_pending(mp)
    mp.undo()
    print("\n전체 통과")
