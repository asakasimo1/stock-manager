# stock_analyzer (Vercel 프론트엔드)

## ⚠️ 핵심 아키텍처 — 반드시 먼저 읽을 것

### 이 프로젝트는 UI 전용이다. 실제 매매는 Oracle VM에서 실행된다.

```
[브라우저] ←→ [Vercel - stock_analyzer]
                    ↕ Gist (상태 읽기/쓰기)
[Oracle VM] → daemon_coin.py → Upbit API  ← 실제 매매 실행
```

### 코인 자동매매 문제 발생 시

**Vercel 코드를 먼저 보지 말 것. Oracle VM부터 확인.**

```bash
ssh ubuntu@158.180.84.109
sudo systemctl status coin-daemon
tail -f /home/ubuntu/stock-trader/daemon_coin.log
```

Oracle VM 코드 위치: `/home/ubuntu/stock-trader/`
Oracle VM 코드 저장소: `github.com/asakasimo1/stock-trader`

### 이 저장소(stock_analyzer)의 역할

| 파일 | 역할 |
|------|------|
| `js/common.js` | 설정, 캐시(_fetchBinData/_fetchGistData), 탭전환(switchTab), 공통 유틸 |
| `js/tab-dashboard.js` | 대시보드 — 브리핑, 캘린더, 계좌폴링, 주식상세모달 |
| `js/tab-portfolio.js` | 포트폴리오 — KPI, 도넛차트, 자산배분 |
| `js/tab-stocks.js` | 개별주 CRUD + IPO 관리 |
| `js/tab-etf.js` | ETF CRUD, 거래내역, 배당금, DRIP |
| `js/tab-market.js` | 시장현황 |
| `js/tab-autotrade.js` | 주식 자동매매 (buy/sell/grid) |
| `js/tab-cointrade.js` | 코인 자동매매 (buy/sell/grid) |
| `js/tab-watchlist.js` | 브리핑 관심종목 관리 |
| `api/coin.js` | Gist CRUD + coin-runner 보조 트리거 |
| `api/data.js` | 대시보드 데이터 / KIS 잔고 / watchlist / coin-runner 트리거 프록시 |
| `api/coin-price.js` | Upbit 현재가 CORS 프록시 (UI 표시용) |
| `api/quote.js` | KIS 현재가 프록시 |

### ⚡ JS 파일 편집 가이드 (토큰 절약)

수정 전 해당 탭 파일만 읽으면 됨. 공통 유틸(캐시/설정)은 `common.js`.

| 수정 대상 | 읽을 파일 |
|-----------|-----------|
| 브리핑/캘린더/IPO 표시 | `tab-dashboard.js` |
| 포트폴리오 KPI/차트 | `tab-portfolio.js` |
| 개별주 추가/편집/IPO 관리 | `tab-stocks.js` |
| ETF/배당금/DRIP | `tab-etf.js` |
| 시장현황 카드 | `tab-market.js` |
| 주식 자동매매 설정/잡 | `tab-autotrade.js` |
| 코인 자동매매 설정/잡 | `tab-cointrade.js` |
| 브리핑 관심종목 | `tab-watchlist.js` |
| 캐시/설정/탭전환/공통함수 | `common.js` |

### ⚠️ Vercel Hobby 플랜 제한

- **서버리스 함수 12개 상한** — `builds` 배열이 12개 꽉 참 (analyze/data/ipo/etf/quote/dividend/transactions/market/stocks/stock/investor/coin)
- 새 API 기능은 **`api/data.js`에 `?mode=xxx`** 파라미터로 추가, `vercel.json` routes에 경로만 추가
- `data.js`는 `_gist-cache.js` 임포트 없이 인라인 Gist 캐시(`_gistCache`, `_gistCacheAt`) 사용

### Vercel 배포

```bash
cd /Users/macbook/projects/stock-manager/frontend
vercel --prod
```

### 주의

- `api/coin.js`의 `handleCoinRunner`는 **보조 수단** (Oracle VM 다운 시 앱에서 수동 트리거용)
- 자동매매 로직 버그는 **stock-trader 저장소의 Python 코드**에서 수정해야 함
- Vercel Hobby 플랜 → 1분 미만 cron 불가 (Oracle VM daemon이 대체)
