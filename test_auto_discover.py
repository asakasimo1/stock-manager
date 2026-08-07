"""_auto_discover() 동작 검증 (합성 데이터, 네트워크 호출 없음) — python3 test_auto_discover.py 로 직접 실행"""
import job_scalp_coin
import job_scalp_stock
import kis_api


def test_coin_auto_discover_creates_job():
    jobs = []
    auto_cfg = {"enabled": True, "max_concurrent": 2, "max_day_chg_pct": 5.0,
                "min_liquidity": 1000, "krw_amount": 10000, "max_daily_loss_krw": -20000}
    price_cache = {
        "KRW-AAA": {"chg_pct": 2.0, "price": 1000, "volume": 100000},   # 통과
        "KRW-BBB": {"chg_pct": 9.0, "price": 1000, "volume": 100000},   # 과열 제외
    }
    changed = job_scalp_coin._auto_discover(jobs, auto_cfg, price_cache, coin_enabled=True, now_epoch=1000.0)
    assert changed is True
    assert len(jobs) == 1 and jobs[0]["ticker"] == "KRW-AAA" and jobs[0]["source"] == "auto"
    assert jobs[0]["status"] == "active" and jobs[0]["phase"] == "watching"
    print("OK: 코인 자동발굴 — 과열종목 제외 + watching 잡 생성")


def test_coin_auto_discover_respects_slots():
    jobs = [
        {"ticker": "KRW-X", "source": "auto", "status": "active", "stats_date": "2099-01-01", "realized_pnl_today": 0},
        {"ticker": "KRW-Y", "source": "auto", "status": "active", "stats_date": "2099-01-01", "realized_pnl_today": 0},
    ]
    auto_cfg = {"enabled": True, "max_concurrent": 2, "max_day_chg_pct": 5.0, "min_liquidity": 0}
    price_cache = {"KRW-Z": {"chg_pct": 2.0, "price": 1000, "volume": 100000}}
    changed = job_scalp_coin._auto_discover(jobs, auto_cfg, price_cache, coin_enabled=True, now_epoch=1000.0)
    assert changed is False and len(jobs) == 2
    print("OK: 코인 자동발굴 — 슬롯(max_concurrent) 초과 시 신규 생성 안 함")


def test_coin_auto_discover_disabled_noop():
    jobs = []
    changed = job_scalp_coin._auto_discover(jobs, {"enabled": False}, {"KRW-A": {"chg_pct": 2.0, "price": 1, "volume": 1}}, True, 0)
    assert changed is False and jobs == []
    print("OK: 코인 자동발굴 — enabled=False 시 아무 것도 안 함")


def test_stock_auto_discover_creates_job(monkeypatch):
    jobs = []
    auto_cfg = {"enabled": True, "max_concurrent": 2, "max_day_chg_pct": 5.0,
                "min_liquidity": 1000, "amount": 500000, "max_daily_loss_krw": -30000}
    monkeypatch.setattr(kis_api, "get_fluctuation_ranking", lambda top_n=30: [
        {"ticker": "005930", "name": "삼성전자", "price": 70000, "chg_pct": 2.5, "acml_vol": 1000000},
        {"ticker": "000660", "name": "SK하이닉스", "price": 150000, "chg_pct": 12.0, "acml_vol": 1000000},
    ])
    changed = job_scalp_stock._auto_discover(jobs, auto_cfg, stock_enabled=True, now_epoch=1000.0)
    assert changed is True
    assert len(jobs) == 1 and jobs[0]["ticker"] == "005930" and jobs[0]["source"] == "auto"
    print("OK: 주식 자동발굴 — 등락률 순위에서 과열종목 제외 + watching 잡 생성")


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
    mp = _FakeMonkeypatch()
    test_stock_auto_discover_creates_job(mp)
    mp.undo()
    print("\n전체 통과")
