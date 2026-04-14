"""
KIS API 모듈 — 한국투자증권 Open API 연동

기능:
  - 토큰 발급 / 자동 갱신 (만료 30분 전)
  - 현재가 조회
  - 잔고 조회
  - 매수 / 매도 주문
  - 미체결 주문 조회

사용 전 .env 파일에 아래 값 설정:
  KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, PAPER_TRADE

참고: https://apiportal.koreainvestment.com/
"""

import os, json, time, logging
from datetime import datetime, timedelta, timezone, time as dt_time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 환경 설정
# ─────────────────────────────────────────
APP_KEY    = os.getenv("KIS_APP_KEY",    "")
APP_SECRET = os.getenv("KIS_APP_SECRET", "")
ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")          # "12345678-01"
PAPER      = os.getenv("PAPER_TRADE", "true").lower() != "false"

BASE_URL = (
    "https://openapivts.koreainvestment.com:29443"   # 모의
    if PAPER else
    "https://openapi.koreainvestment.com:9443"        # 실계좌
)

TOKEN_FILE = Path(".token_cache.json")   # 토큰 로컬 캐시 (git 무시)

_KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────
# NXT (넥스트트레이드) 시간 감지
# 장전: 08:00~09:00 / 장후: 15:30~20:00 KST
# ─────────────────────────────────────────
def _is_nxt_time() -> bool:
    t = datetime.now(_KST).time()
    return (dt_time(8, 0) <= t < dt_time(9, 0) or
            dt_time(15, 30) <= t < dt_time(20, 0))

def is_any_market_open() -> bool:
    """KRX 또는 NXT 거래 가능 시간 (08:00~20:00 KST 평일)"""
    now = datetime.now(_KST)
    if now.weekday() >= 5:   # 토·일 제외
        return False
    t = now.time()
    return dt_time(8, 0) <= t < dt_time(20, 0)


# ─────────────────────────────────────────
# 토큰 관리
# ─────────────────────────────────────────
_token_cache: dict = {}

def _load_token_cache() -> dict:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_token_cache(data: dict):
    TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

def get_token(force_refresh: bool = False) -> str:
    """
    Access Token 반환 (캐시 우선, 만료 30분 전 자동 갱신)
    """
    global _token_cache
    if not _token_cache:
        _token_cache = _load_token_cache()

    now = datetime.now()
    expire_str = _token_cache.get("expires_at", "")
    if not force_refresh and expire_str:
        try:
            expire_dt = datetime.fromisoformat(expire_str)
            if now < expire_dt - timedelta(minutes=30):
                return _token_cache["access_token"]
        except Exception:
            pass

    # 새로 발급
    resp = requests.post(
        f"{BASE_URL}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey":     APP_KEY,
            "appsecret":  APP_SECRET,
        },
        timeout=10,
    )
    if not resp.ok:
        print(f"  토큰 에러 {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
    data = resp.json()

    if "access_token" not in data:
        raise RuntimeError(f"토큰 발급 실패: {data}")

    expires_at = (now + timedelta(seconds=int(data.get("expires_in", 86400)))).isoformat()
    _token_cache = {"access_token": data["access_token"], "expires_at": expires_at}
    _save_token_cache(_token_cache)

    logger.info("토큰 발급 완료  만료: %s", expires_at)
    return _token_cache["access_token"]


# ─────────────────────────────────────────
# 공통 요청 헬퍼
# ─────────────────────────────────────────
def _headers(tr_id: str, extra: dict = None) -> dict:
    h = {
        "content-type":  "application/json; charset=utf-8",
        "authorization": f"Bearer {get_token()}",
        "appkey":        APP_KEY,
        "appsecret":     APP_SECRET,
        "tr_id":         tr_id,
        "custtype":      "P",
    }
    if extra:
        h.update(extra)
    return h

def _account_parts() -> tuple[str, str]:
    """'12345678-01' → ('12345678', '01')"""
    parts = ACCOUNT_NO.replace("-", "")
    return parts[:8], parts[8:] or "01"


# ─────────────────────────────────────────
# 1. 현재가 조회
# ─────────────────────────────────────────
def get_price(ticker: str) -> dict:
    """
    반환:
      stck_prpr  현재가
      stck_oprc  시가
      stck_hgpr  고가
      stck_lwpr  저가
      acml_vol   누적거래량

    NXT 시간대(장전/장후)에는 NXT 시세(NX 마켓) 우선 조회,
    실패 시 KRX 시세(J 마켓)로 폴백.
    """
    tr_id = "FHKST01010100"

    # NXT 시간대: NXT 가격 우선 조회
    if not PAPER and _is_nxt_time():
        try:
            resp = requests.get(
                f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=_headers(tr_id),
                params={"fid_cond_mrkt_div_code": "NX", "fid_input_iscd": ticker},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") == "0" and int(data["output"].get("stck_prpr", 0)) > 0:
                logger.debug("NXT 시세 조회 성공: %s %s원", ticker, data["output"]["stck_prpr"])
                return data["output"]
            logger.debug("NXT 시세 없음(%s) — KRX 폴백", ticker)
        except Exception as e:
            logger.debug("NXT 시세 조회 실패(%s) — KRX 폴백: %s", ticker, e)

    # KRX 시세 (기본)
    resp = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=_headers(tr_id),
        params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(f"현재가 조회 실패: {data.get('msg1')}")
    return data["output"]


# ─────────────────────────────────────────
# 2. 잔고 조회
# ─────────────────────────────────────────
def get_balance() -> dict:
    """
    반환:
      cash           예수금 (주문 가능 현금)
      total_eval     총 평가금액 (현금 + 보유종목)
      holdings       [{ticker, name, qty, avg_price, eval_price, pnl_pct}]
    """
    cano, acnt = _account_parts()
    tr_id = "VTTC8434R" if PAPER else "TTTC8434R"

    resp = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers=_headers(tr_id, {"tr_cont": ""}),
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
    if not resp.ok:
        print(f"  상태코드: {resp.status_code}")
        print(f"  응답내용: {resp.text[:500]}")
        resp.raise_for_status()
    data = resp.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(f"잔고 조회 실패: {data.get('msg1')}")

    output2 = data.get("output2", [{}])[0]
    cash = int(output2.get("dnca_tot_amt", 0))          # 예수금
    total_eval = int(output2.get("tot_evlu_amt", 0))    # 총 평가금액

    holdings = []
    for item in data.get("output1", []):
        qty = int(item.get("hldg_qty", 0))
        if qty <= 0:
            continue
        holdings.append({
            "ticker":    item.get("pdno"),
            "name":      item.get("prdt_name"),
            "qty":       qty,
            "avg_price": int(float(item.get("pchs_avg_pric", 0))),
            "eval_price":int(item.get("prpr", 0)),
            "pnl_pct":   float(item.get("evlu_pfls_rt", 0)),
        })

    return {"cash": cash, "total_eval": total_eval, "holdings": holdings}


# ─────────────────────────────────────────
# 3. 주문 실행
# ─────────────────────────────────────────
def place_order(ticker: str, side: str, qty: int,
                price: int = 0, order_type: str = "market") -> dict:
    """
    side       : "BUY" | "SELL"
    order_type : "market" (시장가) | "limit" (지정가)
    price      : 지정가일 때 가격 (시장가면 0)

    반환: {order_no, message}
    """
    cano, acnt = _account_parts()

    nxt = (not PAPER) and _is_nxt_time()

    if PAPER:
        # 모의투자: NXT 미지원 → KRX TR ID 사용
        tr_id = "VTTC0802U" if side == "BUY" else "VTTC0801U"
    elif nxt:
        # 실계좌 + NXT 시간대: NXT TR ID 사용
        # 참고: https://apiportal.koreainvestment.com
        tr_id = "TTTS3012U" if side == "BUY" else "TTTS3013U"
        logger.info("NXT 시간대 주문 — TR_ID: %s", tr_id)
    else:
        # 실계좌 + KRX 정규시간
        tr_id = "TTTC0802U" if side == "BUY" else "TTTC0801U"

    ord_dvsn = "01" if order_type == "market" else "00"  # 01=시장가, 00=지정가
    ord_unpr = "0" if order_type == "market" else str(price)

    body = {
        "CANO":         cano,
        "ACNT_PRDT_CD": acnt,
        "PDNO":         ticker,
        "ORD_DVSN":     ord_dvsn,
        "ORD_QTY":      str(qty),
        "ORD_UNPR":     ord_unpr,
    }

    resp = requests.post(
        f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash",
        headers=_headers(tr_id),
        json=body,
        timeout=10,
    )
    if not resp.ok:
        logger.error("주문 HTTP 오류 %s: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
    data = resp.json()

    if data.get("rt_cd") != "0":
        raise RuntimeError(f"주문 실패 [{side} {ticker} {qty}주]: {data.get('msg1')}")

    order_no = data.get("output", {}).get("ODNO", "")
    logger.info("주문 완료  %s %s %s주  주문번호: %s", side, ticker, qty, order_no)
    return {"order_no": order_no, "message": data.get("msg1", "")}


# ─────────────────────────────────────────
# 4. 미체결 주문 조회
# ─────────────────────────────────────────
def get_pending_orders() -> list[dict]:
    """당일 미체결 주문 목록 반환"""
    cano, acnt = _account_parts()
    resp = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
        headers=_headers("VTTC8036R" if PAPER else "TTTC8036R"),
        params={
            "CANO":         cano,
            "ACNT_PRDT_CD": acnt,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
            "INQR_DVSN_1":  "0",
            "INQR_DVSN_2":  "0",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    orders = []
    for item in data.get("output", []):
        orders.append({
            "order_no": item.get("odno"),
            "ticker":   item.get("pdno"),
            "name":     item.get("prdt_name"),
            "side":     "BUY" if item.get("sll_buy_dvsn_cd") == "02" else "SELL",
            "qty":      int(item.get("ord_qty", 0)),
            "filled":   int(item.get("tot_ccld_qty", 0)),
            "price":    int(item.get("ord_unpr", 0)),
        })
    return orders


# ─────────────────────────────────────────
# 빠른 확인용 CLI
# ─────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    mode = "모의투자" if PAPER else "실계좌"
    print(f"\n모드: {mode}  ({BASE_URL})")

    if not APP_KEY or APP_KEY == "your_app_key_here":
        print("\n⚠ .env 파일에 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO 를 설정하세요.")
        print("   .env.example 파일을 .env 로 복사 후 값을 입력하세요.\n")
        exit(1)

    print("\n[1] 토큰 발급 테스트...")
    token = get_token()
    print(f"    ✅ 토큰: {token[:20]}...")

    print("\n[2] 현재가 조회 (삼성전자 005930)...")
    price_info = get_price("005930")
    print(f"    현재가: {int(price_info['stck_prpr']):,}원  "
          f"거래량: {int(price_info['acml_vol']):,}")

    print("\n[3] 잔고 조회...")
    bal = get_balance()
    print(f"    예수금: {bal['cash']:,}원  총평가: {bal['total_eval']:,}원")
    for h in bal["holdings"]:
        print(f"    보유: {h['ticker']} {h['name']}  {h['qty']}주  "
              f"평균단가 {h['avg_price']:,}원  손익 {h['pnl_pct']:+.2f}%")

    print("\n✅ API 연결 정상")
