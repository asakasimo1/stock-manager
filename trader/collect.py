"""
데이터 수집 모듈 (FinanceDataReader 기반)

- KOSPI 시가총액 상위 200종목 (KOSPI200 근사)
- 종목별 OHLCV 수집 → data/ohlcv.csv 저장
"""

import os, time
import pandas as pd
import FinanceDataReader as fdr
from datetime import datetime, timedelta
from tqdm import tqdm

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


# ─────────────────────────────────────────
# 1. KOSPI 시가총액 상위 N종목
# ─────────────────────────────────────────
def get_top_tickers(n: int = 200) -> pd.DataFrame:
    """KOSPI 시총 상위 N종목 코드+이름 반환"""
    kospi = fdr.StockListing("KOSPI")
    # 시총 컬럼 정리
    kospi = kospi[kospi["Marcap"] > 0].copy()
    kospi = kospi.sort_values("Marcap", ascending=False).head(n)
    return kospi[["Code", "Name", "Marcap"]].reset_index(drop=True)


# ─────────────────────────────────────────
# 2. OHLCV 수집
# ─────────────────────────────────────────
def collect_ohlcv(tickers: list[str], names: dict,
                  start: str, end: str) -> pd.DataFrame:
    """종목별 일별 OHLCV 수집 후 합치기"""
    frames = []
    for code in tqdm(tickers, desc="OHLCV 수집"):
        try:
            df = fdr.DataReader(code, start, end)
            if df is None or df.empty:
                continue
            df = df.reset_index()
            df.columns = df.columns.str.strip()
            df["ticker"] = code
            df["name"]   = names.get(code, "")
            # 컬럼 표준화
            df = df.rename(columns={
                "Date": "date", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume"
            })
            df["date"] = pd.to_datetime(df["date"])
            frames.append(df[["date", "ticker", "name",
                               "open", "high", "low", "close", "volume"]])
        except Exception as e:
            print(f"  ⚠ {code} 실패: {e}")
        time.sleep(0.15)

    if not frames:
        raise RuntimeError("수집된 데이터 없음")

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values(["ticker", "date"]).reset_index(drop=True)
    return result


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────
if __name__ == "__main__":
    END   = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    START = (datetime.now() - timedelta(days=366)).strftime("%Y-%m-%d")
    TOP_N = 200

    print(f"수집 기간  : {START} ~ {END}")
    print(f"대상 종목  : KOSPI 시총 상위 {TOP_N}개")

    # 종목 목록
    top = get_top_tickers(TOP_N)
    tickers = top["Code"].tolist()
    names   = dict(zip(top["Code"], top["Name"]))
    print(f"종목 수    : {len(tickers)}개\n")

    # OHLCV 수집
    ohlcv = collect_ohlcv(tickers, names, START, END)

    path = os.path.join(DATA_DIR, "ohlcv.csv")
    ohlcv.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n✅ 저장 완료: {path}  ({len(ohlcv):,}행)")
