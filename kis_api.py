from __future__ import annotations
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

import os, json, time, logging, threading
from datetime import datetime, timedelta, timezone, time as dt_time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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

# 매수/매도 수수료+세금 — 모든 손익률/손익금액 계산은 이 상수를 통해 수수료를
# 포함해서 산출해야 함(2026-08-10: 사용자 요청으로 전면 수수료 반영 전환).
BUY_FEE  = 0.00015                 # 매수 수수료 0.015%
SELL_FEE = 0.00015 + 0.0018        # 매도 수수료 0.015% + 증권거래세 0.18%

TOKEN_FILE  = Path(".token_cache.json")   # 토큰 로컬 캐시 (git 무시)
_token_lock = threading.Lock()            # 병렬 토큰 갱신 방지 (ThreadPoolExecutor 대응)

import logging as _logging
_logging.getLogger(__name__).info("KIS 모드: %s  BASE_URL: %s", "모의투자" if PAPER else "실계좌", BASE_URL)

_KST = timezone(timedelta(hours=9))

# 국내주식(KRX) 호가단위 (가격 범위 → 단위) — 2023.1 개편 이후 KOSPI/KOSDAQ 공통
_PRICE_UNITS = [
    (500_000, 1000),
    (200_000,  500),
    ( 50_000,  100),
    ( 20_000,   50),
    (  5_000,   10),
    (  2_000,    5),
    (      0,    1),
]


def price_unit(price: float) -> float:
    """KRX 호가단위 반환"""
    for threshold, unit in _PRICE_UNITS:
        if price >= threshold:
            return unit
    return 1

# ─────────────────────────────────────────
# 공유 HTTP 세션 (TLS/TCP 연결 재활용)
# ─────────────────────────────────────────
_session = requests.Session()
_retry = Retry(total=3, backoff_factor=0.3,
               status_forcelist=(500, 502, 503, 504))
_session.mount("https://", HTTPAdapter(max_retries=_retry, pool_connections=4, pool_maxsize=10))

# ─────────────────────────────────────────
# API 응답 시간 측정 래퍼
# ─────────────────────────────────────────
def _api_get(url: str, **kwargs) -> requests.Response:
    t0 = time.monotonic()
    resp = _session.get(url, **kwargs)
    ms = (time.monotonic() - t0) * 1000
    label = url.replace(BASE_URL, "").split("?")[0]
    logger.info("⏱  GET  %-52s %5.0fms  HTTP%s", label, ms, resp.status_code)
    return resp

def _api_post(url: str, **kwargs) -> requests.Response:
    t0 = time.monotonic()
    resp = _session.post(url, **kwargs)
    ms = (time.monotonic() - t0) * 1000
    label = url.replace(BASE_URL, "").split("?")[0]
    logger.info("⏱  POST %-52s %5.0fms  HTTP%s", label, ms, resp.status_code)
    return resp

# ─────────────────────────────────────────
# NXT (넥스트트레이드) 시간 감지
# 프리마켓: 08:00~08:50 / 애프터마켓: 15:30~20:00 KST
# ─────────────────────────────────────────
def _is_nxt_premarket() -> bool:
    """NXT 프리마켓 (08:00~08:50 KST) — 전 종목 거래 가능"""
    t = datetime.now(_KST).time()
    return dt_time(8, 0) <= t < dt_time(8, 50)

def _is_nxt_aftermarket() -> bool:
    """NXT 애프터마켓 (15:30~20:00 KST) — 종목별 거래 가능 여부 다름"""
    t = datetime.now(_KST).time()
    return dt_time(15, 30) <= t < dt_time(20, 0)

def _is_nxt_time() -> bool:
    return _is_nxt_premarket() or _is_nxt_aftermarket()

_holiday_cache: dict = {}

def _is_krx_open_day(target_date=None) -> bool:
    """KIS 국내휴장일조회(CTCA0903R)로 해당 날짜가 실제 개장일인지 확인.
    설날/추석 등 공휴일은 요일만으로는 못 걸러내서 별도 조회가 필요함 — 하루 1회만
    호출하도록 날짜별로 캐시. job_report.py가 이 함수를 호출하는데 기존엔 정의 자체가
    없어서 평일 리포트 작업이 매번 AttributeError로 실패하고 있었음 — 같이 해결.
    API 실패 시에는 평일 기준 개장으로 간주(fail-open) — 공휴일 오탐지로 신규 발굴을
    몇 번 헛되이 시도하는 것보다, API 일시 오류로 실제 거래일을 통째로 놓치는 게
    훨씬 큰 손실이기 때문."""
    d = target_date or datetime.now(_KST).date()
    key = d.strftime("%Y%m%d")
    if key in _holiday_cache:
        return _holiday_cache[key]
    try:
        params = {"BASS_DT": key, "CTX_AREA_NK": "", "CTX_AREA_FK": ""}
        resp = _api_get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/chk-holiday",
            headers=_headers("CTCA0903R"),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        row = next((r for r in data.get("output", []) if r.get("bass_dt") == key), None)
        is_open = (row.get("opnd_yn") == "Y") if row else True
        _holiday_cache[key] = is_open
        return is_open
    except Exception as e:
        logger.warning("KRX 휴장일 조회 실패 — 평일 기준 개장으로 간주: %s", e)
        return True


def is_any_market_open() -> bool:
    """KRX 또는 NXT 거래 가능 시간 (08:00~20:00 KST 평일, 공휴일 제외)"""
    now = datetime.now(_KST)
    if now.weekday() >= 5:   # 토·일 제외
        return False
    if not _is_krx_open_day(now.date()):
        return False
    t = now.time()
    return dt_time(8, 0) <= t < dt_time(20, 0)


# ─────────────────────────────────────────
# 정규장 개장 직전 "무체결 구간" 감지
# 공식 KRX 시가결정 동시호가는 08:30~09:00이지만, 08:30~08:50은 NXT
# 프리마켓이 겹쳐 있어 NXT 대상 종목은 실제 체결(가격 발견)이 일어난다.
# 반면 08:50(NXT 프리마켓 종료)~09:00(KRX 접속매매 시작) 구간은 어느
# 거래소에서도 실제 체결이 없고 KRX 예상체결가만 출렁이므로, 가격 기반
# 자동매매(범위이탈/재초기화 등) 판단에 이 구간의 "현재가"를 쓰면 안 됨.
# ─────────────────────────────────────────
def _is_krx_call_auction() -> bool:
    """정규장 개장 직전 무체결 구간 (08:50~09:00 KST)."""
    t = datetime.now(_KST).time()
    return dt_time(8, 50) <= t < dt_time(9, 0)


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

def _token_valid() -> bool:
    """현재 캐시 토큰이 유효(만료 30분 이상 남음)한지 확인"""
    expire_str = _token_cache.get("expires_at", "")
    if not expire_str:
        return False
    try:
        return datetime.now() < datetime.fromisoformat(expire_str) - timedelta(minutes=30)
    except Exception:
        return False


def get_token(force_refresh: bool = False) -> str:
    """
    Access Token 반환 (캐시 우선, 만료 30분 전 자동 갱신)
    ThreadPoolExecutor 병렬 호출 시 단 1회만 갱신 (이중 검사 잠금)
    """
    global _token_cache
    if not _token_cache:
        _token_cache = _load_token_cache()

    # 빠른 경로: 잠금 없이 유효성 확인
    if not force_refresh and _token_valid():
        return _token_cache["access_token"]

    # 잠금 획득 후 재확인 — 다른 스레드가 이미 갱신했을 수 있음
    with _token_lock:
        if not force_refresh and _token_valid():
            return _token_cache["access_token"]

        # 실제 토큰 갱신
        now  = datetime.now()
        resp = _api_post(
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
        _notify_token_issued(expires_at)
        return _token_cache["access_token"]


def _notify_token_issued(expires_at: str):
    """토큰 신규 발급 시 텔레그램으로 GitHub Actions 컨텍스트 알림"""
    try:
        import notify as _notify

        # GitHub Actions 환경변수 (로컬 실행 시 기본값)
        workflow = os.getenv("GITHUB_WORKFLOW", "로컬 실행")
        job      = os.getenv("GITHUB_JOB",      "")
        trigger  = os.getenv("GITHUB_EVENT_NAME", "manual")
        run_id   = os.getenv("GITHUB_RUN_ID",   "")

        now_kst    = datetime.now(_KST).strftime("%Y-%m-%d %H:%M")
        expire_kst = (datetime.fromisoformat(expires_at) + timedelta(hours=9)).strftime("%m/%d %H:%M")

        job_line     = f"  잡: <b>{job}</b>\n" if job else ""
        run_line     = f"  Run ID: {run_id}\n" if run_id else ""
        trigger_icon = "⏰" if trigger == "schedule" else "🖱"

        _notify.send(
            f"🔑 <b>KIS API 토큰 발급</b>\n"
            f"  시각: {now_kst} KST\n"
            f"  워크플로우: {workflow}\n"
            f"{job_line}"
            f"  트리거: {trigger_icon} {trigger}\n"
            f"{run_line}"
            f"  토큰 만료: {expire_kst} KST"
        )
    except Exception as e:
        logger.debug("토큰 발급 알림 전송 실패 (무시): %s", e)


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

def _account_parts():
    """'12345678-01' → ('12345678', '01')"""
    parts = ACCOUNT_NO.replace("-", "")
    return parts[:8], parts[8:] or "01"


# ─────────────────────────────────────────
# 등락률 순위 조회 (초단타 자동 종목 발굴용)
# VM 실계좌로 직접 호출 검증 완료 (2026-08-07)
# ─────────────────────────────────────────
def get_fluctuation_ranking(top_n: int = 30, sort: str = "gainers", market: str | None = None) -> list[dict]:
    """
    국내주식 등락률 순위 조회 — 한 번 호출로 상위 N종목 반환.
    sort: "gainers"(상승률순, 급등 후보 발굴용) | "losers"(하락률순, 급락후반등 후보 발굴용)
    market: "J"(KRX) | "NX"(NXT) | None이면 현재 시각 기준 자동 선택
            (NXT 프리마켓/애프터마켓 시간대엔 "NX", 그 외(KRX 정규장 등)엔 "J")
            VM 실계좌로 NX가 KRX와 다른 실제 NXT 전용 순위를 반환하는 것을 직접 조회로 검증 완료
            (2026-08-09) — 기존엔 이 함수가 "J" 고정이라 NXT 전용 시간대엔 신규 후보 발굴이
            전혀 안 되던 문제를 해결.
    반환: [{ticker, name, price, chg_pct, acml_vol}, ...]
    VM 실계좌로 두 모드 모두 직접 호출 검증 완료 (2026-08-07)
    """
    if market is None:
        market = "NX" if _is_nxt_time() else "J"
    tr_id = "FHPST01700000"
    params = {
        "fid_rsfl_rate2":         "",
        "fid_cond_mrkt_div_code": market,
        "fid_cond_scr_div_code":  "20170",
        "fid_input_iscd":         "0000",   # 전체
        "fid_rank_sort_cls_code": "1" if sort == "losers" else "0",  # 0=상승률순, 1=하락률순
        "fid_input_cnt_1":        "0",
        "fid_prc_cls_code":       "0",
        "fid_input_price_1":      "",
        "fid_input_price_2":      "",
        "fid_vol_cnt":            "",
        "fid_trgt_cls_code":      "0",
        "fid_trgt_exls_cls_code": "0",
        "fid_div_cls_code":       "0",
        "fid_rsfl_rate1":         "",
    }
    resp = _api_get(
        f"{BASE_URL}/uapi/domestic-stock/v1/ranking/fluctuation",
        headers=_headers(tr_id),
        params=params,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("rt_cd") != "0":
        raise RuntimeError(f"등락률 순위 조회 실패: {data.get('msg1')}")

    rows = data.get("output", [])[:top_n]
    return [
        {
            "ticker":   row.get("stck_shrn_iscd", ""),
            "name":     row.get("hts_kor_isnm", ""),
            "price":    int(row.get("stck_prpr", 0) or 0),
            "chg_pct":  float(row.get("prdy_ctrt", 0) or 0),
            "acml_vol": int(row.get("acml_vol", 0) or 0),
        }
        for row in rows if row.get("stck_shrn_iscd")
    ]


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
            resp = _api_get(
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
    resp = _api_get(
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


def get_avg_daily_volume(ticker: str, days: int = 10) -> float | None:
    """최근 days거래일의 일별 거래량 평균.
    스캘핑 극단적 거래량 급증 판정(2026-08-13 사용자 요청)의 절대 기준치로 사용 —
    원래 거래가 거의 없던 종목(예: 2분 평균 2주)이 짧은 구간 비율만으로 "5배 급증"
    (2주→10주)처럼 보이는 오탐을 막기 위해, 10일 평균 거래량을 2분 단위로 정규화한
    값 대비로도 급증 여부를 다시 확인한다. 조회 실패 시 None(호출부는 이 조건을
    건너뛰고 기존 비율 기준만 적용)."""
    tr_id = "FHKST03010100"
    today = datetime.now(_KST).strftime("%Y%m%d")
    start = (datetime.now(_KST) - timedelta(days=days * 2 + 5)).strftime("%Y%m%d")  # 주말/휴장 감안 여유
    try:
        resp = _api_get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=_headers(tr_id),
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": ticker,
                "fid_input_date_1": start,
                "fid_input_date_2": today,
                "fid_period_div_code": "D",
                "fid_org_adj_prc": "1",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            logger.warning("일별거래량 조회 실패(%s): %s", ticker, data.get("msg1"))
            return None
        rows = data.get("output2", [])[:days]
        vols = [int(r["acml_vol"]) for r in rows if r.get("acml_vol")]
        if not vols:
            return None
        return sum(vols) / len(vols)
    except Exception as e:
        logger.warning("일별거래량 조회 예외(%s): %s", ticker, e)
        return None


def get_spread_pct(ticker: str) -> float | None:
    """매수/매도 1호가 스프레드(%) 조회 — 슬리피지 우려 종목 배제용.
    get_price()와 동일하게 NXT 시간대엔 NX 시세 우선, 실패 시 KRX(J)로 폴백.
    호가가 없거나 조회 실패 시 None(호출부에서 필터를 건너뛰도록)."""
    tr_id = "FHKST01010200"

    def _fetch(market: str) -> float | None:
        resp = _api_get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            headers=_headers(tr_id),
            params={"fid_cond_mrkt_div_code": market, "fid_input_iscd": ticker},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rt_cd") != "0":
            return None
        out1 = data.get("output1", {})
        bid, ask = int(out1.get("bidp1", 0) or 0), int(out1.get("askp1", 0) or 0)
        if bid <= 0 or ask <= 0:
            return None
        mid = (bid + ask) / 2
        return round((ask - bid) / mid * 100, 4)

    if not PAPER and _is_nxt_time():
        try:
            spread = _fetch("NX")
            if spread is not None:
                return spread
        except Exception as e:
            logger.debug("NXT 호가 조회 실패(%s) — KRX 폴백: %s", ticker, e)

    try:
        return _fetch("J")
    except Exception as e:
        logger.warning("%s 호가 조회 실패: %s", ticker, e)
        return None


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

    resp = _api_get(
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
    bfdy_total_eval = int(output2.get("bfdy_tot_asst_evlu_amt", 0))  # 전일 총자산평가금액

    holdings = []
    for item in data.get("output1", []):
        qty = int(item.get("hldg_qty", 0))
        if qty <= 0:
            continue
        avg_price  = int(float(item.get("pchs_avg_pric", 0)))
        eval_price = int(item.get("prpr", 0))
        # KIS가 주는 evlu_pfls_rt(평가손익율)는 수수료/세금 미반영 단순 시세차익률이라
        # 여기서 직접 수수료 포함 손익률로 재계산함(매수수수료+매도수수료+거래세 반영).
        cost     = avg_price * qty * (1 + BUY_FEE)
        net_eval = eval_price * qty * (1 - SELL_FEE)
        pnl_pct  = (net_eval - cost) / cost * 100 if cost > 0 else 0.0
        holdings.append({
            "ticker":         item.get("pdno"),
            "name":           item.get("prdt_name"),
            "qty":            qty,
            "avg_price":      avg_price,
            "eval_price":     eval_price,
            "pnl_pct":        round(pnl_pct, 2),
            "pnl":            round(net_eval - cost),
            # 전일대비 증감(원) — 당일손익 계산용 (누적손익인 pnl_pct와 다름)
            "bfdy_close_diff": int(float(item.get("bfdy_cprs_icdc", 0))),
            # 실제 매도가능수량 — 그리드 등 다른 미체결 주문이 일부를 잠그고 있으면
            # hldg_qty(총보유)보다 작음. 전량매도 시 이 값을 써야 "주문 가능한
            # 수량을 초과했습니다" 오류를 피할 수 있음.
            "sellable_qty":   int(item.get("ord_psbl_qty", qty)),
        })

    return {
        "cash": cash,
        "total_eval": total_eval,
        "bfdy_total_eval": bfdy_total_eval,
        "holdings": holdings,
    }


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

    # 2026-08 KIS 공식 문서 기준 신규 TR_ID (구 TTTC0802U/0801U는 사전고지 없이
    # 막힐 수 있어 폐지 — 실전/모의 공통으로 신TR 사용. 실거래로 검증됨)
    if side == "BUY":
        tr_id = "VTTC0012U" if PAPER else "TTTC0012U"
    else:
        tr_id = "VTTC0011U" if PAPER else "TTTC0011U"

    # NXT 시간대: 시장가 불가 → 지정가 자동 전환
    if nxt and order_type == "market":
        price_info = get_price(ticker)
        price = int(price_info["stck_prpr"])
        order_type = "limit"
        logger.info("NXT 시간대 — 시장가→지정가 전환: %s %d원", ticker, price)

    if nxt:
        logger.info("NXT 시간대 주문 — TR_ID: %s  지정가: %d원", tr_id, price)

    ord_dvsn = "01" if order_type == "market" else "00"  # 01=시장가, 00=지정가
    ord_unpr = "0" if order_type == "market" else str(price)

    body = {
        "CANO":             cano,
        "ACNT_PRDT_CD":     acnt,
        "PDNO":             ticker,
        "ORD_DVSN":         ord_dvsn,
        "ORD_QTY":          str(qty),
        "ORD_UNPR":         ord_unpr,
        # 거래소 구분 — KIS 공식 문서: 미입력 시 KRX. 모의투자는 KRX만 지원.
        "EXCG_ID_DVSN_CD":  "NXT" if nxt else "KRX",
    }

    resp = _api_post(
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

    out = data.get("output", {})
    order_no = out.get("ODNO", "")
    org_no   = out.get("KRX_FWDG_ORD_ORGNO", "")
    logger.info("주문 완료  %s %s %s주  주문번호: %s", side, ticker, qty, order_no)
    return {"order_no": order_no, "org_no": org_no, "message": data.get("msg1", "")}


# ─────────────────────────────────────────
# 4. 미체결 주문 조회
# ─────────────────────────────────────────
def get_pending_orders() -> list[dict]:
    """당일 미체결 주문 목록 반환"""
    cano, acnt = _account_parts()
    resp = _api_get(
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
            "org_no":   item.get("ord_gno_brno", ""),
            "excg_id":  item.get("excg_id_dvsn_cd", "KRX"),  # KRX/NXT — 취소 시 원주문과 동일하게 지정해야 함
        })
    return orders


def get_daily_executions(target_date: str = None) -> list[dict]:
    """지정일(기본: 오늘)의 실제 체결 내역 전체 조회 (KRX+NXT 통합).
    자동매매 잡뿐 아니라 MTS 앱 등에서 수동으로 낸 주문의 체결도 전부 포함된다
    (KIS 계좌 자체의 체결 이력이므로 주문 경로와 무관).
    target_date: YYYYMMDD 형식. 생략 시 오늘(KST)."""
    cano, acnt = _account_parts()
    d = target_date or datetime.now(_KST).strftime("%Y%m%d")
    tr_id = "VTTC0081R" if PAPER else "TTTC0081R"

    executions: list[dict] = []
    seen_order_nos: set = set()

    # EXCG_ID_DVSN_CD=SOR(통합)이 빈 결과를 반환하는 회귀가 발견되어
    # (2026-08-06 실측), KRX/NXT 각각 조회 후 합치는 방식으로 변경.
    for excg in ("KRX", "NXT"):
        fk100, nk100 = "", ""
        while True:
            resp = _api_get(
                f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                headers=_headers(tr_id, {"tr_cont": "N" if fk100 else ""}),
                params={
                    "CANO":             cano,
                    "ACNT_PRDT_CD":     acnt,
                    "INQR_STRT_DT":     d,
                    "INQR_END_DT":      d,
                    "SLL_BUY_DVSN_CD":  "00",   # 00=전체(매수+매도)
                    "PDNO":             "",
                    "ORD_GNO_BRNO":     "",
                    "ODNO":             "",
                    "CCLD_DVSN":        "01",   # 01=체결만
                    "INQR_DVSN":        "00",   # 00=역순
                    "INQR_DVSN_1":      "",
                    "INQR_DVSN_3":      "00",   # 00=전체
                    "EXCG_ID_DVSN_CD":  excg,
                    "CTX_AREA_FK100":   fk100,
                    "CTX_AREA_NK100":   nk100,
                },
                timeout=10,
            )
            if not resp.ok:
                logger.warning("일별체결조회 실패 상태코드=%s 응답=%s", resp.status_code, resp.text[:300])
                resp.raise_for_status()
            data = resp.json()
            if data.get("rt_cd") != "0":
                raise RuntimeError(f"일별체결조회 실패: {data.get('msg1')}")

            for item in data.get("output1", []):
                qty = int(item.get("tot_ccld_qty", 0))
                order_no = item.get("odno")
                if qty <= 0 or not order_no or order_no in seen_order_nos:
                    continue
                seen_order_nos.add(order_no)
                executions.append({
                    "order_no":  order_no,
                    "ticker":    item.get("pdno"),
                    "name":      item.get("prdt_name"),
                    "side":      "BUY" if item.get("sll_buy_dvsn_cd") == "02" else "SELL",
                    "qty":       qty,
                    "price":     int(float(item.get("avg_prvs") or 0)),
                    "time":      item.get("ord_tmd", ""),      # HHMMSS
                    "excg_cd":   item.get("excg_dvsn_cd", ""),  # 거래소구분코드 (참고용, 코드→명칭 매핑 미확인)
                })

            tr_cont = resp.headers.get("tr_cont", "")
            fk100 = data.get("ctx_area_fk100", "").strip()
            nk100 = data.get("ctx_area_nk100", "").strip()
            if tr_cont not in ("F", "M") or not fk100:
                break

    return executions


# ─────────────────────────────────────────
# 5. 주문 취소
# ─────────────────────────────────────────
def cancel_order(order_no: str, org_no: str = "", excg_id: str = "") -> bool:
    """당일 미체결 지정가 주문 취소. 이미 체결/없으면 True 반환.
    excg_id: 원주문이 체결 대기 중인 거래소(KRX/NXT). 원주문과 다르게 지정하면
    "취소주문 불가합니다" 오류가 남 — 지정 안 하면 미체결 목록에서 자동 조회."""
    if not org_no or not excg_id:
        pending = get_pending_orders()
        found = next((o for o in pending if o["order_no"] == order_no), None)
        if not found:
            logger.info("취소 대상 없음(이미 체결됨?): %s", order_no)
            return True
        org_no  = org_no or found.get("org_no", "")
        excg_id = excg_id or found.get("excg_id", "KRX")

    cano, acnt = _account_parts()
    tr_id = "VTTC0803U" if PAPER else "TTTC0803U"
    body = {
        "CANO":                cano,
        "ACNT_PRDT_CD":        acnt,
        "KRX_FWDG_ORD_ORGNO": org_no,
        "ORGN_ODNO":           order_no,
        "ORD_DVSN":            "00",
        "RVSE_CNCL_DVSN_CD":   "02",
        "ORD_QTY":             "0",
        "ORD_UNPR":            "0",
        "QTY_ALL_ORD_YN":      "Y",
        "EXCG_ID_DVSN_CD":     excg_id or "KRX",
    }
    try:
        resp = _api_post(
            f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-rvsecncl",
            headers=_headers(tr_id),
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        ok = data.get("rt_cd") == "0"
        if not ok:
            logger.warning("주문취소 실패 %s: %s", order_no, data.get("msg1"))
        return ok
    except Exception as e:
        logger.error("주문취소 오류 %s: %s", order_no, e)
        return False


# ─────────────────────────────────────────
# 6. 주식 호가 단위 반올림
# ─────────────────────────────────────────
def round_price(price: float) -> int:
    """KRX 호가 단위로 내림 (지정가 매수는 호가 단위 맞춤 필수).
    _PRICE_UNITS(price_unit()) 기준으로 통일."""
    p = int(price)
    unit = price_unit(p)
    return (p // unit) * unit


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
