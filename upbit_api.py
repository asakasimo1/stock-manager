"""
Upbit API 모듈 — Upbit Open API 연동

기능:
  - JWT 인증 토큰 생성
  - 현재가 조회 (공개 API, 인증 불필요)
  - 잔고 조회
  - 매수 / 매도 주문
  - 주문 상태 조회

사용 전 .env 파일에 아래 값 설정:
  UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY

코인 마켓 코드 예시:
  KRW-BTC (비트코인), KRW-ETH (이더리움), KRW-XRP (리플), KRW-SOL (솔라나)

참고: https://docs.upbit.com/
"""

import os, uuid, hashlib, time, logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import jwt
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY", "")
SECRET_KEY = os.getenv("UPBIT_SECRET_KEY", "")

BASE_URL = "https://api.upbit.com/v1"

# 코인 이름 매핑 (ticker → 한글명)
COIN_NAMES = {
    "KRW-BTC":  "비트코인",
    "KRW-ETH":  "이더리움",
    "KRW-XRP":  "리플",
    "KRW-SOL":  "솔라나",
    "KRW-USDT": "테더",
    "KRW-DOGE": "도지코인",
    "KRW-ADA":  "에이다",
    "KRW-AVAX": "아발란체",
    "KRW-DOT":  "폴카닷",
    "KRW-LINK": "체인링크",
    "KRW-ATOM": "코스모스",
    "KRW-MATIC":"폴리곤",
    "KRW-TRX":  "트론",
    "KRW-SHIB": "시바이누",
    "KRW-LTC":  "라이트코인",
    "KRW-BCH":  "비트코인캐시",
    "KRW-ETC":  "이더리움클래식",
    "KRW-NEAR": "니어프로토콜",
    "KRW-AAVE": "에이브",
    "KRW-UNI":  "유니스왑",
    "KRW-SAND": "샌드박스",
    "KRW-SUI":  "수이",
    "KRW-HBAR": "헤데라",
    "KRW-ARB":  "아비트럼",
    "KRW-OP":   "옵티미즘",
    "KRW-XLM":  "스텔라루멘",
    "KRW-ALGO": "알고랜드",
    "KRW-FLOW": "플로우",
    "KRW-MANA": "디센트럴랜드",
    "KRW-CHZ":  "칠리즈",
    "KRW-KLAY": "클레이튼",
    "KRW-FIL":  "파일코인",
    "KRW-ICP":  "인터넷컴퓨터",
    "KRW-SEI":  "세이",
}

BUY_FEE  = 0.0005   # 0.05% (업비트 기본 수수료)
SELL_FEE = 0.0005   # 0.05%

_KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────
# 공유 HTTP 세션
# ─────────────────────────────────────────
_session = requests.Session()
_retry = Retry(total=3, backoff_factor=0.3, status_forcelist=(500, 502, 503, 504))
_session.mount("https://", HTTPAdapter(max_retries=_retry, pool_connections=4, pool_maxsize=10))


# ─────────────────────────────────────────
# JWT 인증 헤더 생성
# ─────────────────────────────────────────
def _auth_header(query_params: dict = None) -> dict:
    """Upbit API 인증 헤더 생성"""
    payload = {
        "access_key": ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
    }
    if query_params:
        query_string = urlencode(query_params).encode()
        m = hashlib.sha512()
        m.update(query_string)
        payload["query_hash"] = m.hexdigest()
        payload["query_hash_alg"] = "SHA512"

    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────
# 현재가 조회 (공개 API)
# ─────────────────────────────────────────
def get_price(market: str) -> dict:
    """단일 코인 현재가 조회
    Returns: {'price': float, 'chg_pct': float, 'volume': float}
    """
    t0 = time.monotonic()
    r = _session.get(f"{BASE_URL}/ticker", params={"markets": market}, timeout=5)
    ms = (time.monotonic() - t0) * 1000
    logger.info("⏱  GET  ticker %-20s %5.0fms  HTTP%s", market, ms, r.status_code)
    if not r.ok:
        raise RuntimeError(f"현재가 조회 실패 {market}: HTTP {r.status_code}")
    data = r.json()
    if not data:
        raise RuntimeError(f"현재가 조회 결과 없음: {market}")
    d = data[0]
    return {
        "price":   d["trade_price"],
        "chg_pct": round(d["signed_change_rate"] * 100, 2),
        "volume":  d["acc_trade_volume_24h"],
        "high":    d["high_price"],
        "low":     d["low_price"],
    }


def get_prices(markets: list) -> dict:
    """복수 코인 현재가 일괄 조회
    Returns: {'KRW-BTC': {'price': ..., 'chg_pct': ...}, ...}
    """
    if not markets:
        return {}
    t0 = time.monotonic()
    r = _session.get(f"{BASE_URL}/ticker", params={"markets": ",".join(markets)}, timeout=10)
    ms = (time.monotonic() - t0) * 1000
    logger.info("⏱  GET  tickers %-30s %5.0fms  HTTP%s", markets, ms, r.status_code)
    if not r.ok:
        raise RuntimeError(f"현재가 일괄 조회 실패: HTTP {r.status_code}")
    result = {}
    for d in r.json():
        result[d["market"]] = {
            "price":   d["trade_price"],
            "chg_pct": round(d["signed_change_rate"] * 100, 2),
            "volume":  d["acc_trade_volume_24h"],
        }
    return result


# ─────────────────────────────────────────
# 잔고 조회
# ─────────────────────────────────────────
def get_balance() -> dict:
    """계좌 잔고 조회
    Returns: {
      'krw': float,                     # 보유 원화
      'holdings': [{
        'ticker': 'KRW-BTC',
        'symbol': 'BTC',
        'name': '비트코인',
        'qty': float,
        'avg_price': float,
        'cur_price': float,
        'eval_amount': float,
        'pnl': float,
        'pnl_pct': float,
      }]
    }
    """
    t0 = time.monotonic()
    r = _session.get(f"{BASE_URL}/accounts", headers=_auth_header(), timeout=10)
    ms = (time.monotonic() - t0) * 1000
    logger.info("⏱  GET  accounts %5.0fms  HTTP%s", ms, r.status_code)
    if not r.ok:
        raise RuntimeError(f"잔고 조회 실패: HTTP {r.status_code} {r.text[:200]}")

    accounts = r.json()
    krw = 0.0
    holdings = []

    for acc in accounts:
        currency = acc["currency"]
        balance  = float(acc["balance"])
        avg_buy  = float(acc["avg_buy_price"])

        if currency == "KRW":
            krw = balance
            continue

        if balance <= 0:
            continue

        ticker = f"KRW-{currency}"
        try:
            price_info = get_price(ticker)
            cur_price = price_info["price"]
        except Exception:
            cur_price = avg_buy

        eval_amount = balance * cur_price
        cost        = balance * avg_buy * (1 + BUY_FEE)
        pnl         = eval_amount - cost
        pnl_pct     = (pnl / cost * 100) if cost > 0 else 0.0

        holdings.append({
            "ticker":      ticker,
            "symbol":      currency,
            "name":        COIN_NAMES.get(ticker, currency),
            "qty":         balance,
            "avg_price":   avg_buy,
            "cur_price":   cur_price,
            "eval_amount": eval_amount,
            "pnl":         pnl,
            "pnl_pct":     round(pnl_pct, 2),
        })

    return {"krw": krw, "holdings": holdings}


# ─────────────────────────────────────────
# 주문 실행
# ─────────────────────────────────────────
def place_order(
    market: str,
    side: str,           # "bid" (매수) | "ask" (매도)
    ord_type: str,       # "price" (시장가매수) | "market" (시장가매도) | "limit" (지정가)
    volume: float = None,   # 코인 수량 (매도/지정가 매수 시 필요)
    price: float = None,    # KRW 금액 (시장가 매수) 또는 지정가 단가
) -> dict:
    """주문 실행

    시장가 매수: side='bid', ord_type='price', price=KRW금액
    시장가 매도: side='ask', ord_type='market', volume=코인수량
    지정가 매수: side='bid', ord_type='limit', price=단가, volume=코인수량
    지정가 매도: side='ask', ord_type='limit', price=단가, volume=코인수량
    """
    params = {"market": market, "side": side, "ord_type": ord_type}
    if volume is not None:
        params["volume"] = str(volume)
    if price is not None:
        params["price"] = str(int(price)) if price > 1 else str(price)

    t0 = time.monotonic()
    r = _session.post(
        f"{BASE_URL}/orders",
        headers={**_auth_header(params), "Content-Type": "application/json"},
        json=params,
        timeout=10,
    )
    ms = (time.monotonic() - t0) * 1000
    logger.info("⏱  POST orders %-20s %5.0fms  HTTP%s", market, ms, r.status_code)

    if not r.ok:
        raise RuntimeError(f"주문 실패 {market} {side} {ord_type}: HTTP {r.status_code} {r.text[:300]}")

    data = r.json()
    return {
        "uuid":        data.get("uuid", ""),
        "side":        data.get("side", ""),
        "ord_type":    data.get("ord_type", ""),
        "price":       float(data.get("price") or 0),
        "volume":      float(data.get("volume") or 0),
        "state":       data.get("state", ""),
        "market":      data.get("market", ""),
        "created_at":  data.get("created_at", ""),
    }


# ─────────────────────────────────────────
# 주문 상태 조회
# ─────────────────────────────────────────
def get_order(order_uuid: str) -> dict:
    """주문 상태 조회"""
    params = {"uuid": order_uuid}
    t0 = time.monotonic()
    r = _session.get(
        f"{BASE_URL}/order",
        params=params,
        headers=_auth_header(params),
        timeout=10,
    )
    ms = (time.monotonic() - t0) * 1000
    logger.info("⏱  GET  order %-36s %5.0fms  HTTP%s", order_uuid[:12], ms, r.status_code)
    if not r.ok:
        raise RuntimeError(f"주문 조회 실패: HTTP {r.status_code}")
    data = r.json()
    return {
        "uuid":            data.get("uuid", ""),
        "state":           data.get("state", ""),          # wait | done | cancel
        "executed_volume": float(data.get("executed_volume") or 0),
        "remaining_volume":float(data.get("remaining_volume") or 0),
        "avg_price":       float(data.get("avg_buy_price") or 0),
        "trades_count":    data.get("trades_count", 0),
    }


# ─────────────────────────────────────────
# 주문 취소
# ─────────────────────────────────────────
def cancel_order(order_uuid: str) -> bool:
    params = {"uuid": order_uuid}
    r = _session.delete(
        f"{BASE_URL}/order",
        params=params,
        headers=_auth_header(params),
        timeout=10,
    )
    return r.ok
