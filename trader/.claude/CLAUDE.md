# trader (구 stock-trader 프로젝트, 2026-09-01 stock-manager로 통합)

## ⚠️ 핵심 아키텍처 — 반드시 먼저 읽을 것

### 실제 매매 실행은 100% Oracle VM에서 이루어진다

```
Oracle VM (158.180.84.109)
└── systemd: coin-daemon.service (상시 실행)
    └── daemon_coin.py  ← 30초마다 폴링
        ├── job_coin_buy.py   (매수 조건 체크 → Upbit 주문)
        └── job_coin_sell.py  (매도 체크 → Upbit 주문)
```

- **상태 저장**: GitHub Gist (`coin_sell_jobs.json`, `coin_buy_jobs.json`)
- **거래소**: Upbit (`upbit_api.py`)
- **KIS 주식 데몬**: `daemon_stock.py` (주식 매매)

### 문제 발생 시 첫 번째 확인 장소

```bash
# Oracle VM 접속
ssh ubuntu@158.180.84.109

# 데몬 상태 확인
sudo systemctl status coin-daemon

# 실시간 로그
tail -f /home/ubuntu/stock-manager/trader/daemon_coin.log
```

### Oracle VM 경로

| 항목 | 경로 |
|------|------|
| 코드 | `/home/ubuntu/stock-manager/trader/` |
| 환경변수 | `/home/ubuntu/stock-manager/trader/.env` |
| 코인 데몬 로그 | `/home/ubuntu/stock-manager/trader/daemon_coin.log` |
| 서비스 파일 | `/etc/systemd/system/coin-daemon.service` |

### 코드 업데이트 방법

```bash
# Oracle VM에서 (trader/는 이제 독립 저장소가 아니라 stock-manager의 하위
# 디렉토리라 git pull은 stock-manager 루트에서 해야 함 — 또는 그냥
# trader/deploy/deploy_pull.sh 실행)
bash /home/ubuntu/stock-manager/trader/deploy/deploy_pull.sh
```

## 로컬 경로

| 역할 | 경로 |
|------|------|
| 이 저장소 (trader) | `/Users/macbook/projects/stock-manager/trader/` |
| 프론트엔드 (frontend) | `/Users/macbook/projects/stock-manager/frontend/` |

## Vercel(frontend)과의 관계

Vercel은 **UI 프론트엔드 전용**이다. 매매 실행 역할이 아님.

- `api/coin.js` → Gist CRUD + coin-runner(보조 트리거)
- coin-runner는 Oracle VM이 다운됐을 때 앱에서 수동 트리거하는 보조 수단
- **자동 매매의 주체는 항상 Oracle VM의 daemon_coin.py**

## 환경변수 (.env)

```
UPBIT_ACCESS_KEY=...
UPBIT_SECRET_KEY=...
GIST_ID=...
GH_TOKEN=...
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ACCOUNT_NO=...
```
