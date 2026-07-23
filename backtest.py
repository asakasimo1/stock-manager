"""
백테스트 모듈 (고도화 버전)

전략: 거래량 급증 + 가격 모멘텀 → 익일 시가 매수
      손절 -7% / 익절 +10% / 10일 보유 청산

현실 비용 반영:
  - 수수료: 매수 0.015% + 매도 0.015%
  - 증권거래세: 매도 시 0.18% (KOSPI)
  - 슬리피지: 매수 시가 + 0.05% (대형주 기준)
  - 동시 보유 종목 수: 최대 5개
  - 포지션 크기: 가용 현금 / (max_positions - 현재 보유 수)

사용법:
    python backtest.py
    python backtest.py --volume 2.0 --stop -7 --profit 10 --hold 10
"""

import argparse, os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

DATA_DIR   = "data"
RESULT_DIR = "data/results"
os.makedirs(RESULT_DIR, exist_ok=True)

# 한글 폰트
for _f in ["AppleGothic", "NanumGothic", "Malgun Gothic"]:
    if any(_f.lower() in f.name.lower() for f in fm.fontManager.ttflist):
        plt.rcParams["font.family"] = _f
        break
plt.rcParams["axes.unicode_minus"] = False


# ─────────────────────────────────────────
# 거래 비용 상수
# ─────────────────────────────────────────
BUY_COMMISSION  = 0.00015   # 매수 수수료 0.015%
SELL_COMMISSION = 0.00015   # 매도 수수료 0.015%
SELL_TAX        = 0.0018    # 증권거래세 0.18% (KOSPI)
SLIPPAGE        = 0.0005    # 슬리피지 0.05% (대형주)

TOTAL_BUY_COST  = 1 + BUY_COMMISSION + SLIPPAGE          # 매수 실질 단가 배수
TOTAL_SELL_GAIN = 1 - SELL_COMMISSION - SELL_TAX          # 매도 실질 수취 배수


# ─────────────────────────────────────────
# 전략 파라미터
# ─────────────────────────────────────────
class Cfg:
    vol_lookback:   int   = 20
    volume_mult:    float = 2.0    # 거래량 배수
    day_return_min: float = 0.005  # 신호 당일 최소 수익률
    stop_loss:      float = -0.07  # 손절 -7%
    take_profit:    float = 0.10   # 익절 +10%
    hold_days:      int   = 10     # 최대 보유 일수
    max_positions:  int   = 5      # 동시 최대 보유 종목 수
    initial_cash:   float = 10_000_000   # 초기 자본
    # 트레일링 스탑 파라미터
    trail_trigger:  float = 0.10   # 이 수익률 달성 시 트레일링 스탑 활성화
    trail_pct:      float = 0.05   # 활성화 이후 고점 대비 허용 낙폭 (이 비율만큼 빠지면 청산)


# ─────────────────────────────────────────
# 피처 계산
# ─────────────────────────────────────────
def add_features(df: pd.DataFrame, cfg: Cfg) -> pd.DataFrame:
    df = df.sort_values(["ticker", "date"]).copy()
    grp = df.groupby("ticker")

    df["vol_ma"] = grp["volume"].transform(
        lambda x: x.shift(1).rolling(cfg.vol_lookback, min_periods=10).mean()
    )
    df["vol_ratio"]   = df["volume"] / df["vol_ma"]
    df["day_return"]  = (df["close"] - df["open"]) / df["open"]
    df["mom5"]        = grp["close"].transform(lambda x: x.pct_change(5))
    rng = df["high"] - df["low"]
    df["low_recovery"] = np.where(rng > 0, (df["close"] - df["low"]) / rng, 0.5)
    return df


# ─────────────────────────────────────────
# 신호 생성
# ─────────────────────────────────────────
def generate_signals(df: pd.DataFrame, cfg: Cfg) -> pd.DataFrame:
    sig = df[
        (df["vol_ratio"]  >= cfg.volume_mult) &
        (df["day_return"] >= cfg.day_return_min) &
        (df["mom5"]       >  0) &
        (df["volume"]     >  0) &
        (df["open"]       >  0)
    ].copy()
    return sig[["date", "ticker", "name", "close",
                "vol_ratio", "day_return"]].reset_index(drop=True)


# ─────────────────────────────────────────
# 반등 포착 신호 생성
# ─────────────────────────────────────────
def generate_rebound_signals(df: pd.DataFrame,
                             vol_mult: float = 2.0,
                             mom5_max: float = -0.07,
                             recovery_min: float = 0.35) -> pd.DataFrame:
    """반등 포착: 거래량 급증(2배↑) + 5일 낙폭(-7%↓) + 당일 양봉 + 아랫꼬리 회복"""
    sig = df[
        (df["vol_ratio"]    >= vol_mult)     &
        (df["mom5"]         <= mom5_max)     &
        (df["day_return"]   >  0)            &
        (df["low_recovery"] >= recovery_min) &
        (df["volume"]       >  0)            &
        (df["open"]         >  0)
    ].copy()
    sig["signal_type"] = "rebound"
    cols = ["date", "ticker", "name", "close", "vol_ratio", "day_return", "mom5", "low_recovery", "signal_type"]
    return sig[cols].reset_index(drop=True)


# ─────────────────────────────────────────
# 포트폴리오 시뮬레이션 (자본 기반, 동시 보유 제한)
# ─────────────────────────────────────────
def simulate_portfolio(signals: pd.DataFrame, ohlcv: pd.DataFrame,
                       cfg: Cfg) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    자본금 기반 포지션 관리:
    - 매수 시: 가용 현금 / (max_positions - 현재 보유 수)
    - 동시 max_positions 초과 시 신호 무시
    - 수수료·슬리피지·세금 실제 반영
    """
    idx        = ohlcv.set_index(["ticker", "date"])
    date_list  = sorted(ohlcv["date"].unique())
    date_pos   = {d: i for i, d in enumerate(date_list)}
    nxt        = {d: date_list[i+1] for i, d in enumerate(date_list[:-1])}

    # 신호를 buy_date 기준으로 변환
    sig_by_buy = {}
    for _, row in signals.iterrows():
        if row["date"] not in nxt:
            continue
        buy_date = nxt[row["date"]]
        sig_by_buy.setdefault(buy_date, []).append(row)

    cash       = cfg.initial_cash
    positions  = {}   # ticker → {buy_date, buy_price, qty, tp, sl, expire_date}
    trades     = []
    daily_pf   = []

    for date in date_list:
        # ── 1. 기존 포지션 청산 체크 ──────────────────
        to_close = []
        for ticker, pos in positions.items():
            if (ticker, date) not in idx.index:
                continue
            bar = idx.loc[(ticker, date)]

            # 고점(HWM) 갱신
            pos["hwm"] = max(pos.get("hwm", pos["buy_price"]), bar["high"])

            # 실효 손절가: 원래 sl / 수익보호(원금) / 트레일링 중 최고값
            hwm_pnl  = (pos["hwm"] - pos["buy_price"]) / pos["buy_price"]
            eff_sl   = pos["sl"]
            safe_thr = round(cfg.trail_trigger * 0.8, 6)  # 수익보호 기준 (float 오차 방지)

            if hwm_pnl >= cfg.trail_trigger:
                trail_sl = pos["hwm"] * (1 - cfg.trail_pct)
                eff_sl   = max(eff_sl, trail_sl)
            elif hwm_pnl >= safe_thr:
                eff_sl   = max(eff_sl, pos["buy_price"])  # 원금 보장

            sell_price = None
            exit_type  = None

            if bar["low"] <= eff_sl and bar["high"] >= pos["tp"]:
                sell_price, exit_type = eff_sl, "손절"
            elif bar["low"] <= eff_sl:
                if eff_sl > pos["buy_price"]:
                    exit_type = "트레일링" if hwm_pnl >= cfg.trail_trigger else "수익보호"
                else:
                    exit_type = "손절"
                sell_price = eff_sl
            elif bar["high"] >= pos["tp"]:
                sell_price, exit_type = pos["tp"], "익절"
            elif date >= pos["expire_date"]:
                sell_price, exit_type = bar["close"], f"{cfg.hold_days}일청산"

            if sell_price is not None:
                proceeds = sell_price * pos["qty"] * TOTAL_SELL_GAIN
                cost     = pos["buy_price"] * pos["qty"] * TOTAL_BUY_COST
                ret_pct  = (proceeds - cost) / cost * 100
                cash    += proceeds

                trades.append({
                    "buy_date":   pos["buy_date"],
                    "sell_date":  date,
                    "ticker":     ticker,
                    "name":       pos["name"],
                    "매수가(실질)": round(pos["buy_price"] * TOTAL_BUY_COST, 0),
                    "매도가(실질)": round(sell_price * TOTAL_SELL_GAIN, 0),
                    "수량":        pos["qty"],
                    "수익률(%)":   round(ret_pct, 2),
                    "손익(원)":    round(proceeds - cost, 0),
                    "청산유형":    exit_type,
                })
                to_close.append(ticker)

        for t in to_close:
            del positions[t]

        # ── 2. 새 포지션 진입 ──────────────────────────
        if date in sig_by_buy:
            for row in sig_by_buy[date]:
                ticker = row["ticker"]

                if ticker in positions:
                    continue
                if len(positions) >= cfg.max_positions:
                    continue
                if (ticker, date) not in idx.index:
                    continue

                bar       = idx.loc[(ticker, date)]
                buy_price = bar["open"]
                if buy_price <= 0:
                    continue

                # 가용 자본 / 남은 슬롯
                slots = cfg.max_positions - len(positions)
                alloc = cash / slots if slots > 0 else 0
                qty   = int(alloc / (buy_price * TOTAL_BUY_COST))
                if qty <= 0:
                    continue

                cost  = buy_price * qty * TOTAL_BUY_COST
                if cost > cash:
                    continue

                cash -= cost
                expire_pos = date_pos.get(date, 0) + cfg.hold_days
                expire_date = date_list[min(expire_pos, len(date_list) - 1)]

                positions[ticker] = {
                    "buy_date":    date,
                    "buy_price":   buy_price,
                    "qty":         qty,
                    "tp":          buy_price * (1 + cfg.take_profit),
                    "sl":          buy_price * (1 + cfg.stop_loss),
                    "expire_date": expire_date,
                    "name":        row.get("name", ""),
                    "hwm":         buy_price,   # 트레일링 스탑용 고점 추적
                }

        # ── 3. 일별 포트폴리오 평가 ───────────────────
        holdings_val = 0.0
        for ticker, pos in positions.items():
            if (ticker, date) in idx.index:
                holdings_val += idx.loc[(ticker, date), "close"] * pos["qty"]

        daily_pf.append({
            "date":       date,
            "현금":       round(cash, 0),
            "평가금액":   round(holdings_val, 0),
            "총자산":     round(cash + holdings_val, 0),
            "보유종목수": len(positions),
        })

    return pd.DataFrame(trades), pd.DataFrame(daily_pf)


# ─────────────────────────────────────────
# 결과 출력
# ─────────────────────────────────────────
def print_summary(trades: pd.DataFrame, pf: pd.DataFrame, cfg: Cfg):
    n = len(trades)
    if n == 0:
        print("신호 없음")
        return

    wins      = (trades["수익률(%)"] > 0).sum()
    final     = pf["총자산"].iloc[-1]
    total_ret = (final - cfg.initial_cash) / cfg.initial_cash * 100
    avg_ret   = trades["수익률(%)"].mean()
    max_dd    = (pf["총자산"] / pf["총자산"].cummax() - 1).min() * 100
    p_avg     = trades[trades["수익률(%)"] > 0]["수익률(%)"].mean() if wins > 0 else 0
    l_avg     = trades[trades["수익률(%)"] < 0]["수익률(%)"].mean() if wins < n else 0
    total_fee = n * cfg.initial_cash / cfg.max_positions * (BUY_COMMISSION + SELL_COMMISSION + SELL_TAX + SLIPPAGE)

    print()
    print("=" * 54)
    print("  백테스트 결과 (수수료·슬리피지 반영)")
    print("=" * 54)
    print(f"  총 트레이드  : {n}건")
    print(f"  승률         : {wins/n*100:.1f}%  ({wins}승 {n-wins}패)")
    print(f"  평균 수익률  : {avg_ret:+.2f}%  (비용 후)")
    print(f"  평균 수익(승): {p_avg:+.2f}%")
    print(f"  평균 손실(패): {l_avg:+.2f}%")
    rir = abs(p_avg / l_avg) if l_avg != 0 else float("inf")
    print(f"  손익비       : {rir:.2f}")
    print(f"  총 수익률    : {total_ret:+.1f}%")
    print(f"  최대 낙폭    : {max_dd:.1f}%")
    print(f"  최종 자산    : {final:,.0f}원")
    print(f"  추정 총 비용 : 약 {total_fee:,.0f}원")
    print(f"  동시 최대 보유: {cfg.max_positions}종목")
    print("=" * 54)

    print("\n  [청산 유형]")
    for k, v in trades["청산유형"].value_counts().items():
        print(f"    {k:<10}: {v:>3}건 ({v/n*100:.1f}%)")

    print("\n  [수익 상위 5]")
    for _, r in trades.nlargest(5, "수익률(%)").iterrows():
        print(f"    {r['ticker']} {str(r['name'])[:6]:<6}  "
              f"{str(r['sell_date'])[:10]}  {r['수익률(%)']:+.2f}%  ({r['청산유형']})")

    print("\n  [손실 하위 5]")
    for _, r in trades.nsmallest(5, "수익률(%)").iterrows():
        print(f"    {r['ticker']} {str(r['name'])[:6]:<6}  "
              f"{str(r['sell_date'])[:10]}  {r['수익률(%)']:+.2f}%  ({r['청산유형']})")


# ─────────────────────────────────────────
# 차트 저장
# ─────────────────────────────────────────
def save_charts(trades: pd.DataFrame, pf: pd.DataFrame, cfg: Cfg):
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle(
        f"백테스트 (수수료+슬리피지 반영)  |  "
        f"거래량 {cfg.volume_mult}배 · {cfg.hold_days}일 보유 · "
        f"손절{cfg.stop_loss*100:.0f}% · 익절+{cfg.take_profit*100:.0f}%  |  "
        f"동시 최대 {cfg.max_positions}종목",
        fontsize=11, fontweight="bold"
    )

    # 1. 누적 자산 vs 벤치마크
    ax = axes[0, 0]
    ax.plot(pf["date"], pf["총자산"] / 1e4, color="#1d4ed8", label="전략", linewidth=1.5)
    ax.axhline(cfg.initial_cash / 1e4, color="gray", linestyle="--",
               linewidth=0.8, label="원금")
    ax.fill_between(pf["date"], pf["총자산"] / 1e4, cfg.initial_cash / 1e4,
                    where=(pf["총자산"] >= cfg.initial_cash),
                    alpha=0.08, color="#16a34a")
    ax.fill_between(pf["date"], pf["총자산"] / 1e4, cfg.initial_cash / 1e4,
                    where=(pf["총자산"] < cfg.initial_cash),
                    alpha=0.08, color="#dc2626")
    ax.legend(); ax.set_title("누적 자산 (만원)"); ax.grid(alpha=0.3)

    # 2. 낙폭 (Drawdown)
    ax = axes[0, 1]
    dd = (pf["총자산"] / pf["총자산"].cummax() - 1) * 100
    ax.fill_between(pf["date"], dd, 0, alpha=0.6, color="#dc2626")
    ax.set_title("낙폭 (Drawdown %)"); ax.grid(alpha=0.3)

    # 3. 월별 수익률
    ax = axes[1, 0]
    trades["month"] = pd.to_datetime(trades["sell_date"]).dt.to_period("M")
    monthly = trades.groupby("month")["수익률(%)"].mean()
    colors  = ["#16a34a" if v >= 0 else "#dc2626" for v in monthly.values]
    ax.bar(range(len(monthly)), monthly.values, color=colors)
    ax.set_xticks(range(len(monthly)))
    ax.set_xticklabels([str(m) for m in monthly.index],
                       rotation=45, ha="right", fontsize=8)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("월별 평균 수익률 (%)"); ax.grid(alpha=0.3, axis="y")

    # 4. 보유 종목 수 추이
    ax = axes[1, 1]
    ax.fill_between(pf["date"], pf["보유종목수"],
                    alpha=0.5, color="#8b5cf6", step="post")
    ax.set_ylim(0, cfg.max_positions + 1)
    ax.set_title(f"동시 보유 종목 수 (최대 {cfg.max_positions})"); ax.grid(alpha=0.3)

    plt.tight_layout()
    path = os.path.join(RESULT_DIR, "backtest_result.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    print(f"\n  차트 저장: {path}")
    plt.close()


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",      default=None)
    parser.add_argument("--end",        default=None)
    parser.add_argument("--volume",     type=float, default=2.0)
    parser.add_argument("--day_return", type=float, default=0.5,
                        help="당일 최소 수익률 %% (기본 0.5)")
    parser.add_argument("--stop",       type=float, default=-7.0)
    parser.add_argument("--profit",     type=float, default=10.0)
    parser.add_argument("--hold",       type=int,   default=10)
    parser.add_argument("--max_pos",    type=int,   default=5,
                        help="동시 최대 보유 종목 수 (기본 5)")
    args = parser.parse_args()

    cfg = Cfg()
    cfg.volume_mult    = args.volume
    cfg.day_return_min = args.day_return / 100
    cfg.stop_loss      = args.stop   / 100
    cfg.take_profit    = args.profit / 100
    cfg.hold_days      = args.hold
    cfg.max_positions  = args.max_pos

    print("파라미터:")
    print(f"  거래량 배수      : {cfg.volume_mult}배 이상")
    print(f"  당일 최소 수익률 : +{cfg.day_return_min*100:.1f}%")
    print(f"  손절 / 익절      : {cfg.stop_loss*100:.0f}% / +{cfg.take_profit*100:.0f}%")
    print(f"  보유 기간        : {cfg.hold_days}일")
    print(f"  동시 보유 상한   : {cfg.max_positions}종목")
    print(f"  비용 (왕복)      : {(BUY_COMMISSION+SELL_COMMISSION+SELL_TAX+SLIPPAGE)*100:.3f}%")

    path = os.path.join(DATA_DIR, "ohlcv.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"데이터 없음: {path}\n먼저 collect.py를 실행하세요.")

    ohlcv = pd.read_csv(path, parse_dates=["date"])

    if args.start:
        ohlcv = ohlcv[ohlcv["date"] >= pd.to_datetime(args.start)]
    if args.end:
        ohlcv = ohlcv[ohlcv["date"] <= pd.to_datetime(args.end)]

    print(f"\n로드 완료: {len(ohlcv):,}행  ({ohlcv['ticker'].nunique()}개 종목)")

    ohlcv   = add_features(ohlcv, cfg)
    signals = generate_signals(ohlcv, cfg)
    print(f"신호 발생     : {len(signals)}건")

    if len(signals) == 0:
        print("파라미터 완화 시도: python backtest.py --volume 1.5")
        exit(0)

    trades, pf = simulate_portfolio(signals, ohlcv, cfg)
    print(f"실행 트레이드 : {len(trades)}건")

    print_summary(trades, pf, cfg)

    tp = os.path.join(RESULT_DIR, "trades.csv")
    pp = os.path.join(RESULT_DIR, "portfolio.csv")
    trades.to_csv(tp, index=False, encoding="utf-8-sig")
    pf.to_csv(pp, index=False, encoding="utf-8-sig")
    print(f"\n  트레이드 내역 : {tp}")
    print(f"  포트폴리오    : {pp}")

    save_charts(trades, pf, cfg)
    print("\n✅ 백테스트 완료")
