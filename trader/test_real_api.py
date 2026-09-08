"""
실전투자 API 연결 테스트

실행 전 .env 설정 확인:
  PAPER_TRADE=false
  KIS_APP_KEY=실전투자_APP_KEY
  KIS_APP_SECRET=실전투자_APP_SECRET
  KIS_ACCOUNT_NO=64363044-01

주문은 실행하지 않고 조회만 테스트합니다.
"""

import json, time, logging
import kis_api
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def test_token():
    print("\n[1] 토큰 발급 테스트...")
    token = kis_api.get_token()   # 캐시 우선, 만료 시만 재발급
    print(f"    ✅ 토큰: {token[:20]}...")
    return token

def test_price():
    print("\n[2] 현재가 조회 (삼성전자 005930)...")
    info = kis_api.get_price("005930")
    print(f"    현재가: {int(info['stck_prpr']):,}원  거래량: {int(info['acml_vol']):,}")

def test_balance_real():
    """실전용 잔고 조회 — TR ID TTTC8434R + 추가 파라미터"""
    print("\n[3] 잔고 조회 (실전)...")
    cano, acnt = kis_api._account_parts()
    print(f"    계좌: {cano}-{acnt}")

    resp = requests.get(
        f"{kis_api.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers=kis_api._headers("TTTC8434R", {"tr_cont": ""}),
        params={
            "CANO":                  cano,
            "ACNT_PRDT_CD":          acnt,
            "AFHR_FLPR_YN":          "N",
            "OFL_YN":                "N",
            "INQR_DVSN":             "02",
            "UNPR_DVSN":             "01",
            "FUND_STTL_ICLD_YN":     "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN":             "00",
            "PDNO":                  "",
            "ORD_UNPR":              "0",
            "ORD_DVSN":              "00",
            "RVSE_CNCL_DVSN_CD":     "",
            "ORD_QTY":               "0",
            "CMA_EVLU_AMT_ICLD_YN":  "N",
            "OVRS_ICLD_YN":          "N",
            "CTX_AREA_FK100":        "",
            "CTX_AREA_NK100":        "",
        },
        timeout=10,
    )
    data = resp.json()
    rt_cd = data.get("rt_cd")
    msg   = data.get("msg1", "").strip()

    if rt_cd == "0":
        output2 = data.get("output2", [{}])[0]
        cash       = int(output2.get("dnca_tot_amt", 0))
        total_eval = int(output2.get("tot_evlu_amt", 0))
        print(f"    ✅ 예수금: {cash:,}원  총평가: {total_eval:,}원")
        holdings = data.get("output1", [])
        for h in holdings:
            qty = int(h.get("hldg_qty", 0))
            if qty <= 0:
                continue
            print(f"    보유: {h.get('pdno')} {h.get('prdt_name')}  "
                  f"{qty}주  평균단가 {int(h.get('pchs_avg_pric',0)):,}원")
        return True
    else:
        print(f"    ❌ 실패: {msg}")
        print(f"    raw: {json.dumps(data, ensure_ascii=False)}")
        return False

def test_pending_orders():
    print("\n[4] 미체결 주문 조회...")
    try:
        orders = kis_api.get_pending_orders()
        if orders:
            for o in orders:
                print(f"    {o['side']} {o['ticker']} {o['qty']}주  주문번호: {o['order_no']}")
        else:
            print("    ✅ 미체결 주문 없음")
    except Exception as e:
        print(f"    ❌ 실패: {e}")


if __name__ == "__main__":
    mode = "실계좌" if not kis_api.PAPER else "모의투자"
    print(f"\n{'='*50}")
    print(f"  실전 API 연결 테스트  [{mode}]")
    print(f"  BASE_URL: {kis_api.BASE_URL}")
    print(f"{'='*50}")

    if kis_api.PAPER:
        print("\n⚠  .env 의 PAPER_TRADE=false 로 변경 후 실행하세요.")
        exit(1)

    try:
        test_token()
        time.sleep(1)
        test_price()
        time.sleep(1)
        ok = test_balance_real()
        time.sleep(1)
        test_pending_orders()

        print(f"\n{'='*50}")
        if ok:
            print("  ✅ 실전 API 연결 정상 — 실거래 전환 준비 완료")
        else:
            print("  ⚠  잔고 조회 실패 — 위 에러 메시지를 확인하세요")
        print(f"{'='*50}\n")

    except Exception as e:
        print(f"\n❌ 오류: {e}")
