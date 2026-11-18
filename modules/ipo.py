"""
공모주 청약 일정 스크래퍼 (38커뮤니케이션)
- 청약 일정 + 신규상장 일정 파싱
- TLS: modules/http38 사용 (38.co.kr의 낡은 DH 파라미터 대응, 인증서 검증 유지)
"""
import json
import os
import re
from datetime import datetime, date

from bs4 import BeautifulSoup

from modules import http38

_HEADERS = http38.DEFAULT_HEADERS
_BASE = f"{http38.BASE_URL}/html/fund/index.htm"
_DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ipo_data.json")


# ─────────────────────────── 스크래핑 ────────────────────────────

def _get_soup(param: str) -> BeautifulSoup:
    r = http38.get(f"{_BASE}?o={param}", headers=_HEADERS, timeout=10)
    r.encoding = "euc-kr"
    return BeautifulSoup(r.text, "html.parser")


def _parse_date(s: str) -> str | None:
    """'2026.03.23' → '2026-03-23'"""
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", s.strip())
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def fetch_ipo_schedule() -> list[dict]:
    """청약 일정 파싱 (최근 ~ 향후 목록)"""
    soup = _get_soup("k")
    tables = soup.find_all("table")

    rows_data = []
    for t in tables:
        rows = t.find_all("tr")
        if not rows:
            continue
        header = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
        if header == ["종목명", "공모주일정", "확정공모가", "희망공모가", "청약경쟁률", "주간사", "분석"]:
            for row in rows[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) >= 4 and cells[0] and "~" in cells[1]:
                    rows_data.append(cells)
            break

    result = []
    for cells in rows_data:
        name = cells[0].strip()
        period = cells[1].strip()          # "2026.03.23~03.24"
        fixed_price = cells[2].strip()
        hope_price = cells[3].strip()
        competition = cells[4].strip() if len(cells) > 4 else ""
        underwriter = cells[5].strip() if len(cells) > 5 else ""

        # 청약 시작/마감일 파싱
        m = re.match(r"(\d{4}\.\d{2}\.\d{2})~(\d{2}\.\d{2})", period)
        if not m:
            continue
        year = m.group(1)[:4]
        start_str = _parse_date(m.group(1))
        end_month_day = m.group(2)
        end_str = _parse_date(f"{year}.{end_month_day}")

        result.append({
            "name": name,
            "sub_start": start_str,
            "sub_end": end_str,
            "fixed_price": fixed_price,
            "hope_price": hope_price,
            "competition": competition,
            "underwriter": underwriter,
            "listing_date": None,  # 아래에서 채움
        })

    return result


def fetch_listing_dates() -> dict[str, str]:
    """사이드바 신규상장 일정 파싱 → {종목명: 상장일(YYYY-MM-DD)}"""
    soup = _get_soup("k")
    text = soup.get_text(separator="\n")

    # "IPO 신규상장 일정\n03/25 한패스\n03/26 메쥬\n..." 패턴
    m = re.search(r"IPO 신규상장 일정(.+?)(?:빨간색|Copyright)", text, re.DOTALL)
    if not m:
        return {}

    block = m.group(1)
    today = date.today()
    year = today.year

    mapping = {}
    for line in block.splitlines():
        line = line.strip()
        mm = re.match(r"(\d{2})/(\d{2})\s+(.+)", line)
        if mm:
            month, day, name = mm.group(1), mm.group(2), mm.group(3).strip()
            listing = date(year, int(month), int(day))
            # 1월 데이터가 연말에 나오면 내년 처리
            if listing < today.replace(month=1, day=1):
                listing = listing.replace(year=year + 1)
            mapping[name] = listing.strftime("%Y-%m-%d")

    return mapping


def get_ipo_list() -> list[dict]:
    """청약 일정 + 상장예정일 매핑 통합"""
    ipos = fetch_ipo_schedule()
    listings = fetch_listing_dates()

    for ipo in ipos:
        name = ipo["name"]
        # 종목명이 길거나 괄호 포함된 경우 부분 매칭
        for k, v in listings.items():
            if k in name or name in k:
                ipo["listing_date"] = v
                break

    return ipos


# ─────────────────────────── 사용자 데이터 ─────────────────────────

def load_ipo_data() -> dict:
    if not os.path.exists(_DATA_FILE):
        return {}
    with open(_DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_ipo_data(data: dict):
    with open(_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def set_participation(name: str, shares: int, sub_end: str, listing_date: str | None):
    """참여 기록 저장. shares=0이면 미참여로 처리."""
    data = load_ipo_data()
    data[name] = {
        "shares": shares,
        "sub_end": sub_end,
        "listing_date": listing_date,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_ipo_data(data)


def get_participation(name: str) -> dict:
    return load_ipo_data().get(name, {})


# ─────────────────────────── 알림 계산 ────────────────────────────

def get_notifications(ipo_data: dict, ipos: list[dict]) -> list[dict]:
    """오늘~7일 이내 중요 이벤트 목록 반환"""
    today = date.today()
    alerts = []

    for ipo in ipos:
        name = ipo["name"]
        pdata = ipo_data.get(name, {})
        shares = pdata.get("shares", -1)  # -1: 아직 입력 안 함
        participated = shares > 0

        # 청약 시작일 알림
        if ipo["sub_start"]:
            d = date.fromisoformat(ipo["sub_start"])
            diff = (d - today).days
            if -1 <= diff <= 7:
                alerts.append({
                    "date": ipo["sub_start"],
                    "type": "청약시작",
                    "name": name,
                    "diff": diff,
                    "note": "",
                })

        # 청약 마감일 알림 → 마감 당일 배정주식수 확인 노티
        if ipo["sub_end"]:
            d = date.fromisoformat(ipo["sub_end"])
            diff = (d - today).days
            if -3 <= diff <= 7:
                note = ""
                if diff < 0 and shares == -1:
                    note = "📋 배정 주식수를 입력해주세요"
                elif diff == 0:
                    note = "🔔 오늘 마감! 배정 주식수 확인 예정"
                alerts.append({
                    "date": ipo["sub_end"],
                    "type": "청약마감",
                    "name": name,
                    "diff": diff,
                    "note": note,
                })

        # 상장예정일 알림 (참여한 종목만)
        if participated and ipo["listing_date"]:
            d = date.fromisoformat(ipo["listing_date"])
            diff = (d - today).days
            if -1 <= diff <= 14:
                alerts.append({
                    "date": ipo["listing_date"],
                    "type": "상장일",
                    "name": name,
                    "diff": diff,
                    "note": f"보유 {shares}주",
                })

    alerts.sort(key=lambda x: x["date"])
    return alerts
