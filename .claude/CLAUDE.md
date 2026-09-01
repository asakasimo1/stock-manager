# stock_analyzer (Vercel 프론트엔드 + trader/ 실거래 백엔드)

## ⚠️ 핵심 아키텍처 — 반드시 먼저 읽을 것

### 2026-09-01: stock-trader 저장소를 이 저장소의 `trader/` 하위로 통합 이관함

이전엔 이 저장소가 UI 전용이고 실제 매매 코드는 별도 저장소
(`asakasimo1/stock-trader`)에 있었는데, `git subtree`(이력 보존)로 그
저장소 전체를 이 저장소의 `trader/` 디렉토리 밑으로 흡수 이관했다.
**`asakasimo1/stock-trader` 저장소는 삭제 예정/삭제됨** — 더 이상 참조하지
말 것. Vercel 배포(`vercel.json`의 `builds`)는 트리 최상단 파일들만
명시적으로 지정하므로 `trader/`가 추가돼도 프론트 빌드엔 영향 없음.

```
[브라우저] ←→ [Vercel - stock_analyzer (이 저장소 최상단)]
                    ↕ Gist (상태 읽기/쓰기)
[Oracle VM] → trader/daemon_coin.py (이 저장소의 trader/ 하위 코드) → Upbit API  ← 실제 매매 실행
```

### 코인 자동매매 문제 발생 시

**Vercel 코드(최상단 js/api)를 먼저 보지 말 것. Oracle VM부터 확인.**

```bash
ssh ubuntu@158.180.84.109
sudo systemctl status coin-daemon
tail -f /home/ubuntu/stock-manager/trader/daemon_coin.log
```

Oracle VM 코드 위치: `/home/ubuntu/stock-manager/trader/`
(구 경로 `/home/ubuntu/stock-trader/`는 더 이상 어떤 서비스도 참조 안 함 —
롤백 안전망으로만 남겨둔 것, 조만간 정리 예정)
저장소: `github.com/asakasimo1/stock-manager` (이 저장소 자체 — 더 이상 별도
저장소 아님)

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

**`vercel --prod`를 직접 쓰지 말고 반드시 `./deploy.sh`를 쓸 것** —
`hsk-stockmanager.vercel.app` 별칭을 최신 배포로 자동 재지정해줌
(안 하면 이 별칭만 며칠씩 뒤처짐, 2026-08-15 발견).

```bash
cd /Users/macbook/projects/stock-manager/frontend
./deploy.sh
```

### 주의

- `api/coin.js`의 `handleCoinRunner`는 **보조 수단** (Oracle VM 다운 시에만 수동
  트리거 — `coin-runner.yml`의 5분 자동 cron은 2026-09-01 제거함, VM 데몬과
  같은 Gist 잡을 동시처리하며 Upbit 중복주문 위험이 있었기 때문. 이제
  `workflow_dispatch`로만 실행됨)
- 자동매매 로직 버그는 **이 저장소의 `trader/` 하위 Python 코드**에서 수정해야
  함(예전엔 별도 stock-trader 저장소였음 — 2026-09-01 통합됨)
- Vercel Hobby 플랜 → 1분 미만 cron 불가 (Oracle VM daemon이 대체)

---

## 운영 로그 (Gist 직접수정 · Vercel 별칭 재지정 등 — git log에 안 남는 것들)

**코드 변경은 git 커밋 메시지에 남으니 `git log`로 확인.** 이 표는 **Gist를
스크립트로 직접 수정했거나, `vercel alias set` 등 git에 안 남는 수동 조치를
한 경우**만 기록 — 다른 머신(Mac/Windows) 세션이 git pull만으로는 놓치는
변경사항을 알 수 있도록 하기 위함(stock-trader 저장소에서 Mac/Windows 세션이
같은 버그를 동시에 각자 고쳐서 병합 충돌 났던 사례가 있어, 이 저장소도 동일한
이유로 로그를 둠).

**참고**: `hsk-stockmanager.vercel.app`는 수동 별칭이라 새 배포마다 자동으로
안 따라감 — 2026-08-15부터 `./deploy.sh`가 배포 직후 자동으로 재지정해주므로
**반드시 `./deploy.sh`로 배포할 것**(`vercel --prod` 직접 실행 금지). 그 전에
수동 배포했다면 `vercel alias set <최신배포URL> hsk-stockmanager.vercel.app`로
직접 재지정.

**새 항목 추가 형식**: `[YYYY-MM-DD HH:MM KST] 대상 — 내용 (관련 커밋 있으면 해시)`

- [2026-08-12] `hsk-stockmanager.vercel.app` — `tab-autotrade.js` 자동완성 수정
  배포(`755a67a`) 후 최신 배포로 별칭 재지정.
