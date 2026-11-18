"""
공모주 청약 일정 자동 수집 모듈
Source: https://www.38.co.kr/html/fund/index.htm?o=k
"""
import re
from datetime import datetime, date

from modules.http38 import DEFAULT_HEADERS as _HEADERS, get as _get38, BASE_URL

TODAY = date.today()


def _get(url):
    """38.co.kr은 서버 TLS 설정이 낡아(dh key too small) 최신 OpenSSL(GitHub Actions 러너 등)의
    기본 보안수준에서 핸드셰이크가 실패한다(SSLV3_ALERT_HANDSHAKE_FAILURE).
    modules/http38 이 이 호스트에만 SECLEVEL=1 TLS 컨텍스트를 붙여 https로 연결하고,
    그래도 실패할 때만 http로 폴백한다."""
    return _get38(url, headers=_HEADERS, timeout=15)

# ──────────────────────────────────────────────────────────────
# 점수 산출
# ──────────────────────────────────────────────────────────────

def _score_inst_comp(rate):
    """기관 수요예측 경쟁률 → 0~40점"""
    if rate is None:
        return 0
    if rate < 100:
        return 5
    if rate < 500:
        return 15
    if rate < 1000:
        return 25
    if rate < 2000:
        return 35
    return 40


def _score_lock_up(pct):
    """의무보유확약 비율 → 0~40점"""
    if pct is None:
        return 0
    if pct < 5:
        return 5
    if pct < 10:
        return 15
    if pct < 20:
        return 25
    if pct < 40:
        return 35
    return 40


def _score_band_position(price_ipo, band_low, band_high):
    """확정가 밴드 내 위치 → 0~10점"""
    if price_ipo == 0 or band_low == 0 or band_high == 0:
        return 5  # 미정
    if band_high == band_low:
        return 10 if price_ipo >= band_high else 5
    if price_ipo > band_high:
        return 10
    ratio = (price_ipo - band_low) / (band_high - band_low)
    if ratio >= 1.0:
        return 10
    if ratio >= 0.5:
        return 7
    return 3


def _score_price_premium(name, inst_comp_rate):
    """예상 초기수익 가능성 → 0~10점"""
    if "스팩" in name:
        return 0
    if inst_comp_rate is None:
        return 5
    if inst_comp_rate >= 1000:
        return 10
    if inst_comp_rate >= 500:
        return 7
    return 3


def _band_position_ratio(price_ipo, band_low, band_high):
    """밴드 내 위치 비율 0.0~1.0 (초과시 >1)"""
    if price_ipo == 0 or band_low == 0 or band_high == 0:
        return 0.5
    if band_high == band_low:
        return 1.0 if price_ipo >= band_high else 0.5
    return (price_ipo - band_low) / (band_high - band_low)


def _calc_score(name, inst_comp_rate, lock_up_pct, price_ipo, band_low, band_high):
    s_inst = _score_inst_comp(inst_comp_rate)
    s_lock = _score_lock_up(lock_up_pct)
    s_band = _score_band_position(price_ipo, band_low, band_high)
    s_prem = _score_price_premium(name, inst_comp_rate)
    total = s_inst + s_lock + s_band + s_prem
    detail = {
        "inst_comp_rate": s_inst,
        "lock_up_pct": s_lock,
        "band_position": s_band,
        "price_premium": s_prem,
    }
    return total, detail


def _recommendation(name, score):
    if "스팩" in name:
        return "스팩주: 원금보장형, 수익 낮음"
    if score >= 90:
        return "⭐⭐⭐ 적극 청약 추천"
    if score >= 70:
        return "⭐⭐ 청약 추천"
    if score >= 50:
        return "⭐ 청약 고려"
    return "⚪ 청약 보류"


# ──────────────────────────────────────────────────────────────
# 날짜 유틸
# ──────────────────────────────────────────────────────────────

def _parse_date_range(year, start_mmdd, end_mmdd):
    """
    year: '2026'
    start_mmdd: '03.23'  end_mmdd: '03.24'  or  '04.01'
    Returns (date_sub_start: str, date_sub_end: str) as 'YYYY-MM-DD'
    """
    y = int(year)
    sm, sd = start_mmdd.split(".")
    em, ed = end_mmdd.split(".")
    start = date(y, int(sm), int(sd))
    # If end month < start month, might roll to next year
    end_year = y if int(em) >= int(sm) else y + 1
    end = date(end_year, int(em), int(ed))
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _status(date_sub_start: str, date_sub_end: str) -> str:
    if not date_sub_start:
        return "청약예정"
    s = date.fromisoformat(date_sub_start)
    e = date.fromisoformat(date_sub_end)
    if e < TODAY:
        return "청약완료"
    if s <= TODAY <= e:
        return "청약중"
    return "청약예정"


# ──────────────────────────────────────────────────────────────
# 파싱 헬퍼
# ──────────────────────────────────────────────────────────────

def _parse_int(s: str) -> int:
    """'16,600' → 16600, '-' or '' → 0"""
    s = s.replace(",", "").strip()
    if not s or s == "-":
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def _parse_float_rate(s: str):
    """'2097.68:1' → 2097.68, '-' or '' → None"""
    s = s.strip()
    if not s or s == "-":
        return None
    m = re.match(r"([\d,]+\.?\d*)\s*:\s*1", s)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


# ──────────────────────────────────────────────────────────────
# 상장일 사이드바 파싱
# ──────────────────────────────────────────────────────────────

def _parse_listing_dates(text: str) -> dict:
    """
    'IPO 신규상장 일정\nMM/DD 종목명\n...' 형식 파싱
    - 날짜가 오늘 이후이면 올해, 이미 지났으면 내년으로 판단
    Returns {name: 'YYYY-MM-DD'}
    """
    listing = {}
    idx = text.find("IPO 신규상장 일정")
    if idx == -1:
        idx = text.find("신규상장 일정")
    if idx == -1:
        return listing
    section = text[idx: idx + 3000]
    year_now = TODAY.year
    for m in re.finditer(r"(\d{2})/(\d{2})\s+([^\n\r\d][^\n\r]*)", section):
        mm, dd, name = int(m.group(1)), int(m.group(2)), m.group(3).strip()
        name = re.split(r"\s{2,}", name)[0].strip()
        if not name:
            continue
        try:
            # 올해 날짜로 먼저 시도, 이미 90일 이상 지났으면 내년
            d = date(year_now, mm, dd)
            if (TODAY - d).days > 90:
                d = date(year_now + 1, mm, dd)
            listing[name] = d.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return listing


# ──────────────────────────────────────────────────────────────
# 메인 파싱 로직
# ──────────────────────────────────────────────────────────────

# Pattern for subscription date range in the table
_DATE_RANGE_RE = re.compile(r"(20\d\d)\.(\d{2}\.\d{2})~(\d{2}\.\d{2})")

# Band price pattern: num~num
_BAND_RE = re.compile(r"^([\d,]+)~([\d,]+)$")

# Competition rate pattern
_RATE_RE = re.compile(r"([\d,]+\.?\d*)\s*:\s*1")


def _strip_line(s: str) -> str:
    """Strip whitespace from a line."""
    return s.strip()


def _extract_entries(plain: str) -> list:
    """
    The page has table rows structured as newline-separated cells:
        종목명 \n YYYY.MM.DD~MM.DD \n 확정가 \n 밴드low~밴드high \n 경쟁률 \n 주간사

    Strategy:
    - Find every date pattern
    - The token immediately before the date (in the same or nearby line) is the name
    - The tokens after are the data fields
    """
    # Split into lines for context-aware parsing
    lines = [l.strip() for l in re.split(r"[\n\r]+", plain)]
    # Filter out empty lines but keep index
    nonempty = [(i, l) for i, l in enumerate(lines) if l]

    entries = []
    seen_names = set()

    for idx, (line_i, line) in enumerate(nonempty):
        m = _DATE_RANGE_RE.match(line)
        if not m:
            # Sometimes the date is inline with the name
            m = _DATE_RANGE_RE.search(line)
            if not m:
                continue

        year = m.group(1)
        start_mmdd = m.group(2)
        end_mmdd = m.group(3)

        # The name is the nearest non-empty line before this one
        # that doesn't look like a date/number/header
        name = ""
        for back in range(1, 5):
            if idx - back < 0:
                break
            prev_line = nonempty[idx - back][1]
            # Skip lines that are: pure numbers, Korean headers, empty
            if re.match(r"^[\d,\.\~\-\:]+$", prev_line):
                continue
            if prev_line in (
                "종목명", "공모주일정", "확정공모가", "희망공모가",
                "청약경쟁률", "주간사", "분석", "수요예측결과",
            ):
                continue
            # Check if it looks like a broker list (contains 증권/투자 but no 스팩/기업명 patterns)
            if re.search(r"증권|투자증권|투자|자산", prev_line) and "스팩" not in prev_line:
                # Could be broker or stock name — check length
                if len(prev_line) > 20:
                    continue
            name = prev_line
            break

        if not name or name in seen_names:
            continue

        # Validate name: must contain Korean chars
        if not re.search(r"[\uAC00-\uD7A3]", name):
            continue

        seen_names.add(name)

        # Now collect the next fields after the date line
        confirmed_price_str = "-"
        band_low_str = "0"
        band_high_str = "0"
        comp_rate_str = "-"
        broker_str = ""

        field_lines = []
        for fwd in range(1, 8):
            if idx + fwd >= len(nonempty):
                break
            fl = nonempty[idx + fwd][1]
            # Stop if we hit another date (next entry)
            if _DATE_RANGE_RE.search(fl):
                break
            # Stop if we hit what looks like a name (next stock name will appear before next date)
            field_lines.append(fl)

        # Parse field_lines in order
        field_idx = 0

        # Field 1: 확정공모가
        if field_idx < len(field_lines):
            f = field_lines[field_idx]
            if re.match(r"^(-|[\d,]+)$", f):
                confirmed_price_str = f
                field_idx += 1

        # Field 2: 희망공모가 (band) — num~num
        if field_idx < len(field_lines):
            f = field_lines[field_idx]
            bm = _BAND_RE.match(f)
            if bm:
                band_low_str = bm.group(1)
                band_high_str = bm.group(2)
                field_idx += 1

        # Field 3: 경쟁률 (optional)
        if field_idx < len(field_lines):
            f = field_lines[field_idx]
            rm = _RATE_RE.search(f)
            if rm:
                comp_rate_str = f
                field_idx += 1
            elif f == "-":
                comp_rate_str = "-"
                field_idx += 1

        # Field 4: 주간사
        if field_idx < len(field_lines):
            f = field_lines[field_idx]
            # Broker lines contain 증권 or similar, or are comma-separated Korean text
            if re.search(r"[\uAC00-\uD7A3]", f) and not _DATE_RANGE_RE.search(f):
                broker_str = f
                field_idx += 1

        entries.append({
            "name": name,
            "year": year,
            "start_mmdd": start_mmdd,
            "end_mmdd": end_mmdd,
            "confirmed_price_str": confirmed_price_str,
            "band_low_str": band_low_str,
            "band_high_str": band_high_str,
            "comp_rate_str": comp_rate_str,
            "broker_str": broker_str,
        })

    return entries


# ──────────────────────────────────────────────────────────────
# 수요예측 결과 페이지 파싱 (기관경쟁률 + 의무보유확약)
# ──────────────────────────────────────────────────────────────

def _fetch_demand_forecast() -> dict:
    """
    수요예측 결과 페이지에서 종목별 기관경쟁률과 의무보유확약 비율을 읽어온다.
    Returns {name: {"inst_comp_rate": float, "lock_up_pct": float}}
    """
    url = f"{BASE_URL}/html/fund/index.htm?o=r"
    result = {}
    try:
        resp = _get(url)
        resp.encoding = "euc-kr"
        text = resp.text

        plain = re.sub(r"<[^>]+>", " ", text)
        plain = re.sub(r"&nbsp;", " ", plain)
        plain = re.sub(r"[ \t]+", " ", plain)

        lines = [l.strip() for l in re.split(r"[\n\r]+", plain)]
        nonempty = [(i, l) for i, l in enumerate(lines) if l]

        for idx, (line_i, line) in enumerate(nonempty):
            m = _DATE_RANGE_RE.search(line)
            if not m:
                continue

            # Find name
            name = ""
            for back in range(1, 5):
                if idx - back < 0:
                    break
                prev_line = nonempty[idx - back][1]
                if re.match(r"^[\d,\.\~\-\:]+$", prev_line):
                    continue
                if prev_line in (
                    "종목명", "공모주일정", "확정공모가", "희망공모가",
                    "청약경쟁률", "주간사", "분석", "수요예측결과",
                    "기관경쟁률", "의무보유확약",
                ):
                    continue
                if not re.search(r"[\uAC00-\uD7A3]", prev_line):
                    continue
                name = prev_line
                break

            if not name:
                continue

            # Look ahead for inst_rate and lock_up
            after_text = " ".join(nonempty[idx + j][1] for j in range(1, 10) if idx + j < len(nonempty))

            inst_m = _RATE_RE.search(after_text)
            inst_rate = float(inst_m.group(1).replace(",", "")) if inst_m else None

            lock_m = re.search(r"([\d,]+\.?\d*)\s*%", after_text)
            lock_pct = float(lock_m.group(1).replace(",", "")) if lock_m else None

            if inst_rate is not None or lock_pct is not None:
                result[name] = {
                    "inst_comp_rate": inst_rate,
                    "lock_up_pct": lock_pct,
                }

    except Exception as e:
        print(f"[IPO] 수요예측 결과 조회 실패: {e}")
    return result


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────

def fetch_ipo_schedule() -> list:
    """
    38커뮤니케이션 공모주 청약 일정 페이지 크롤링 후 파싱된 공모주 리스트 반환.
    """
    url = f"{BASE_URL}/html/fund/index.htm?o=k"
    print(f"[IPO] 페이지 가져오는 중: {url}")
    resp = _get(url)
    resp.encoding = "euc-kr"
    page_text = resp.text

    # Strip HTML tags to get plain text
    plain = re.sub(r"<[^>]+>", " ", page_text)
    plain = re.sub(r"&nbsp;", " ", plain)
    plain = re.sub(r"&amp;", "&", plain)
    plain = re.sub(r"&#\d+;", "", plain)
    plain = re.sub(r"[ \t]+", " ", plain)

    # Listing dates from sidebar
    listing_map = _parse_listing_dates(plain)
    print(f"[IPO] 상장일 사이드바 {len(listing_map)}건: {list(listing_map.keys())[:8]}")

    # Fetch demand forecast data
    print("[IPO] 수요예측 결과 페이지 조회 중...")
    demand_data = _fetch_demand_forecast()
    print(f"[IPO] 수요예측 데이터 {len(demand_data)}건")

    # Parse main schedule entries
    raw_entries = _extract_entries(plain)
    print(f"[IPO] 파싱 항목: {len(raw_entries)}건")

    today_str = TODAY.strftime("%Y-%m-%d")
    records = []

    for e in raw_entries:
        name = e["name"]

        date_sub_start, date_sub_end = _parse_date_range(
            e["year"], e["start_mmdd"], e["end_mmdd"]
        )

        price_ipo = _parse_int(e["confirmed_price_str"])
        band_low = _parse_int(e["band_low_str"])
        band_high = _parse_int(e["band_high_str"])
        inst_comp_rate_from_main = _parse_float_rate(e["comp_rate_str"])

        # Merge with demand forecast data
        dd = demand_data.get(name, {})
        inst_comp_rate = dd.get("inst_comp_rate") or inst_comp_rate_from_main
        lock_up_pct = dd.get("lock_up_pct", None)

        band_pos = _band_position_ratio(price_ipo, band_low, band_high)
        score, score_detail = _calc_score(
            name, inst_comp_rate, lock_up_pct, price_ipo, band_low, band_high
        )
        rec = _recommendation(name, score)

        note = ""
        if "스팩" in name:
            note = "스팩주: 원금보장형, 수익 낮음"

        records.append({
            "name": name,
            "date_sub_start": date_sub_start,
            "date_sub_end": date_sub_end,
            "date_allot": "",
            "date_list": listing_map.get(name, ""),
            "price_ipo": price_ipo,
            "price_band_low": band_low,
            "price_band_high": band_high,
            "broker": e["broker_str"],
            "inst_comp_rate": inst_comp_rate,
            "lock_up_pct": lock_up_pct,
            "band_position": round(band_pos, 4),
            "score": score,
            "score_detail": score_detail,
            "recommendation": rec,
            "status": _status(date_sub_start, date_sub_end),
            "subscribed": False,
            "shares_alloc": None,
            "note": note,
            "fetched_at": today_str,
        })

    # Sort: 청약중 first, then 청약예정 by date, then 청약완료
    order = {"청약중": 0, "청약예정": 1, "청약완료": 2, "상장완료": 3}
    records.sort(key=lambda r: (order.get(r["status"], 9), r["date_sub_start"]))

    print(f"[IPO] 최종 {len(records)}건")
    return records
