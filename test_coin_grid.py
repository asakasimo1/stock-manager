"""
job_coin_grid.py의 재초기화 보유물량 이월(carried) 로직 회귀 테스트.

2026-08-30 — "일별 손익현황엔 손실이 안 잡히는데 계좌 총액은 줄었다"는
사용자 리포트의 근본원인이었던 버그(재초기화 시 보유물량의 매수원가가
0으로 초기화되던 문제) 재발 방지용. upbit_api는 네트워크 호출 함수만
mock하고, round_ask_price/round_bid_price(호가단위 계산, 순수함수)는
실제 구현을 그대로 사용한다.
"""
import unittest
from unittest.mock import patch, MagicMock

import job_coin_grid as jcg
import upbit_api


def make_job(**overrides):
    job = {
        "id": "test", "name": "테스트코인", "ticker": "KRW-TEST",
        "status": "active", "grid_pct": 1.5,
        "lower_price": 900, "upper_price": 1100,
        "krw_per_grid": 100000,
        "grid_owned_qty": 0,
        "total_profit_krw": 0, "trade_count": 0,
    }
    job.update(overrides)
    return job


class TestExtractHeldInventory(unittest.TestCase):
    def test_sell_waiting_grid_extracted(self):
        job = make_job(grids=[
            {"level": 1000, "state": "sell_waiting", "buy_uuid": "", "sell_uuid": "u1",
             "coin_qty": 10.0, "last_buy_price": 990, "last_sell_price": 1010},
        ])
        held = jcg._extract_held_inventory(job)
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["qty"], 10.0)
        self.assertEqual(held[0]["buy_price"], 990)
        self.assertEqual(held[0]["sell_price"], 1010)

    def test_buy_waiting_with_missing_uuid_treated_as_held(self):
        # buy_uuid가 비어있는데 coin_qty>0 — 매도등록 실패로 남은 고아물량
        job = make_job(grids=[
            {"level": 1000, "state": "buy_waiting", "buy_uuid": "", "sell_uuid": "",
             "coin_qty": 5.0, "last_buy_price": 995, "last_sell_price": 0},
        ])
        held = jcg._extract_held_inventory(job)
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["buy_price"], 995)

    def test_buy_waiting_with_live_uuid_not_extracted(self):
        # 정상적으로 미체결 매수 주문이 걸려있는 경우(buy_uuid 존재) — 아직
        # 보유물량이 아니므로 회수 대상이 아님.
        job = make_job(grids=[
            {"level": 1000, "state": "buy_waiting", "buy_uuid": "live-uuid", "sell_uuid": "",
             "coin_qty": 5.0, "last_buy_price": 1000, "last_sell_price": 0},
        ])
        held = jcg._extract_held_inventory(job)
        self.assertEqual(held, [])

    def test_idle_with_leftover_qty_extracted(self):
        job = make_job(grids=[
            {"level": 1000, "state": "idle", "buy_uuid": "", "sell_uuid": "",
             "coin_qty": 3.0, "last_buy_price": 998, "last_sell_price": 0},
        ])
        held = jcg._extract_held_inventory(job)
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["buy_price"], 998)

    def test_zero_qty_grids_ignored(self):
        job = make_job(grids=[
            {"level": 1000, "state": "idle", "buy_uuid": "", "sell_uuid": "",
             "coin_qty": 0, "last_buy_price": 0, "last_sell_price": 0},
        ])
        self.assertEqual(jcg._extract_held_inventory(job), [])

    def test_missing_last_buy_price_falls_back_to_level(self):
        job = make_job(grids=[
            {"level": 1000, "state": "sell_waiting", "buy_uuid": "", "sell_uuid": "u1",
             "coin_qty": 2.0, "last_buy_price": 0, "last_sell_price": 0},
        ])
        held = jcg._extract_held_inventory(job)
        self.assertEqual(held[0]["buy_price"], 1000)  # level로 폴백


class TestInitializeGridCarried(unittest.TestCase):
    """핵심 회귀 테스트 — carried 항목의 last_buy_price가 0이 아니어야 한다."""

    @patch.object(jcg, "upbit_api")
    def test_carried_inventory_keeps_real_buy_price(self, mock_api):
        mock_api.get_price.return_value = {"price": 1000.0}
        mock_api.place_order.return_value = {"uuid": "sell-uuid-1"}
        mock_api.round_ask_price.side_effect = upbit_api.round_ask_price
        mock_api.round_bid_price.side_effect = upbit_api.round_bid_price

        job = make_job(grid_owned_qty=10.0)  # 이월물량만큼 이미 장부에 쌓여있음
        carried = [{"qty": 10.0, "buy_price": 950.0, "sell_price": 965.0}]

        ok = jcg.initialize_grid(job, carried=carried)
        self.assertTrue(ok)

        sell_waiting = [g for g in job["grids"] if g["state"] == "sell_waiting"]
        self.assertEqual(len(sell_waiting), 1)
        carried_grid = sell_waiting[0]
        # 버그 재현 조건: 예전엔 이게 0으로 남아 나중에 매도 체결 시 가짜
        # 수익(또는 숨겨진 손실)으로 기록됐음.
        self.assertGreater(carried_grid["last_buy_price"], 0)
        self.assertEqual(carried_grid["last_buy_price"], 950.0)
        self.assertEqual(carried_grid["coin_qty"], 10.0)

        # 이월물량이 있으므로 초기 시장가 매수는 스킵돼야 함(중복매수 방지)
        buy_calls = [c for c in mock_api.place_order.call_args_list
                     if c.kwargs.get("side") == "bid" and c.kwargs.get("ord_type") == "price"]
        self.assertEqual(len(buy_calls), 0)

    @patch.object(jcg, "upbit_api")
    def test_carried_deep_drawdown_anchors_to_current_price(self, mock_api):
        # 원래 매수가(950)가 현재가(1000) 대비 3격자 이상 위(=지금 이미 3격자
        # 넘게 하락한 상태)면, 원가를 그대로 쓰지 않고 현재가에 앵커링해야
        # 재초기화 직후 바로 손절당하는 사고(2026-08-27 실사고)를 막는다.
        mock_api.get_price.return_value = {"price": 1000.0}
        mock_api.place_order.return_value = {"uuid": "sell-uuid-2"}
        mock_api.round_ask_price.side_effect = upbit_api.round_ask_price
        mock_api.round_bid_price.side_effect = upbit_api.round_bid_price

        grid_pct = 1.5
        step_ratio = (1 + grid_pct / 100) ** jcg.REBALANCE_GRID_STEPS
        deep_buy_price = 1000.0 * step_ratio + 5  # 현재가 기준 3격자 초과 하락 상태 재현
        job = make_job(grid_pct=grid_pct, grid_owned_qty=1.0)
        carried = [{"qty": 1.0, "buy_price": deep_buy_price, "sell_price": deep_buy_price * 1.015}]

        jcg.initialize_grid(job, carried=carried)

        carried_grid = [g for g in job["grids"] if g["state"] == "sell_waiting"][0]
        # 원래 원가(deep_buy_price)가 아니라 현재가(1000) 근처로 앵커링돼야 함
        self.assertLess(carried_grid["last_buy_price"], deep_buy_price)
        self.assertAlmostEqual(carried_grid["last_buy_price"], 1000.0, delta=5)

    @patch.object(jcg, "upbit_api")
    def test_no_carried_behaves_like_before(self, mock_api):
        # carried=None(기본값)일 때 기존 동작(이월물량 없음)과 동일해야 함.
        mock_api.get_price.return_value = {"price": 1000.0}
        mock_api.place_order.side_effect = Exception("insufficient_funds")
        mock_api.round_ask_price.side_effect = upbit_api.round_ask_price
        mock_api.round_bid_price.side_effect = upbit_api.round_bid_price

        job = make_job()
        ok = jcg.initialize_grid(job)
        self.assertTrue(ok)
        self.assertEqual([g for g in job["grids"] if g["state"] == "sell_waiting"], [])


class TestSellFillAfterCarry(unittest.TestCase):
    """이월된 포지션이 이후 정상 매도체결되면 실제 매수원가 기준으로
    손익이 기록되는지 — 이번 버그의 최종 재현 시나리오가 더 이상
    재현되지 않음을 확인."""

    @patch.object(jcg, "upbit_api")
    def test_pnl_uses_real_buy_price_not_zero(self, mock_api):
        mock_api.get_price.return_value = {"price": 1000.0}
        mock_api.get_order.return_value = {"state": "done", "avg_price": 965.0,
                                            "executed_volume": 10.0}

        job = make_job(grids=[
            {"level": 950, "state": "sell_waiting", "buy_uuid": "", "sell_uuid": "sell-uuid-1",
             "coin_qty": 10.0, "last_buy_price": 950.0, "last_sell_price": 965.0,
             "buy_time": "10:00"},
        ])

        jcg.process_grid(job)

        hist = job["trade_history"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["buy_price"], 950.0)
        expected_pnl = (965.0 * (1 - jcg.SELL_FEE) - 950.0 * (1 + jcg.BUY_FEE)) * 10.0
        self.assertAlmostEqual(hist[0]["profit"], round(expected_pnl, 2), places=2)
        # 버그였다면 buy_price=0으로 기록돼 이 profit이 실제 손익과 무관한
        # 값(거의 매도금액 전체)이 됐을 것 — 여기선 정상적인 소액 손익.
        self.assertLess(abs(hist[0]["profit"]), 965.0 * 10.0)


if __name__ == "__main__":
    unittest.main()
