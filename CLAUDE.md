# stock-trader (Oracle VM 자동매매 백엔드)

## ⚠️ 핵심 아키텍처 — 수정 전 반드시 읽을 것

### 실행 주체 분리 원칙

```
[Oracle VM — 상시 실행]          [GitHub Actions — 보조/수동]
  coin-daemon.service  ──────────  coin_buy / coin_sell / coin_balance  (비활성)
  stock-daemon.service ──────────  monitor / profit_sell                (수동만)
                                   buy / close / signals / report       (스케줄)
```

**"VM daemon이 처리하는 잡은 GitHub Actions 크론을 절대 활성화하지 않는다."**

---

## Oracle VM 상시 실행 데몬

| 서비스 | 관리 | 실행 파일 | 역할 | 주기 |
|--------|------|-----------|------|------|
| `coin-daemon.service` | systemd | `daemon_coin.py` | 코인 매수/매도/그리드/잔고 | 30초, 24/7 |
| `stock-daemon.service` | systemd | `daemon_stock.py` | 주식 매수잡/수익매도/잔고갱신 | 30초, 08:00~20:00 KST 평일 |

```bash
# 상태 확인
sudo systemctl status coin-daemon stock-daemon

# 로그 확인
tail -f /home/ubuntu/stock-trader/daemon_coin.log
tail -f /home/ubuntu/stock-trader/daemon_stock.log

# 재시작
sudo systemctl restart stock-daemon
sudo systemctl restart coin-daemon
```

---

## GitHub Actions 잡 역할 분류

### ✅ 스케줄 자동 실행 (GitHub Actions 담당)

| 잡 | 크론 (UTC) | KST | 이유 |
|----|-----------|-----|------|
| `signals` | `50 23 * * 0-4` | 08:50 Mon-Fri | 1일 1회, 복잡한 신호 계산 |
| `buy` | `5 0 * * 1-5` | 09:05 Mon-Fri | 1일 1회 매수 |
| `close` | `20 6 * * 1-5` | 15:20 Mon-Fri | 마감 청산 |
| `report` | `35 6 * * 1-5` | 15:35 Mon-Fri | 일간 리포트 |
| `cleanup_logs` | `0 23 * * 0-4` | 08:00 Mon-Fri | 로그 정리 + git 커밋 |
| `factor_rebalance` | `10 0 1-7 * 1` | 09:10 월1회 | 팩터 리밸런싱 |

### 🚫 크론 비활성화 — VM daemon 대체 (수동만 가능)

| 잡 | 이전 크론 | 비활성화 이유 |
|----|----------|--------------|
| `coin_buy` | `*/5 * * * *` (self-hosted) | `coin-daemon.service` 가 30초마다 처리 |
| `coin_sell` | `*/5 * * * *` (self-hosted) | `coin-daemon.service` 가 30초마다 처리 |
| `coin_balance` | `*/5 * * * *` 의존 | `coin-daemon.service` 가 30초마다 처리 |
| `monitor` | `*/30 0-6 * * 1-5` | `stock-daemon.service` 가 30초마다 처리 |
| `profit_sell` | — | `stock-daemon.service` 가 30초마다 처리 |

### 🛠️ 수동 전용 (workflow_dispatch only)

`balance`, `test_today`, `profit_sell`, `monitor`, `monitor_036030`,
`force_done_submitted`, `coin_buy`, `coin_sell`, `coin_balance`

---

## PM2 프로세스

| 이름 | 역할 | 상태 |
|------|------|------|
| `coin-runner` | Upbit API HTTP 서버 (port 3000) — Vercel 프론트엔드 fallback | online |
| `cycle-runner` | (구) 주식 사이클 데몬 — 사이클 트레이딩 기능 자체가 삭제되어 대상 스크립트(`job_cycle_cloud.py`) 없음 | **stopped** |

> **`cycle-runner` 재시작 금지** — 대상 스크립트가 삭제되어 실행 불가 (VM에서 `pm2 delete cycle-runner` 로 정리 권장)

---

## 주요 Python 파일 역할

| 파일 | 호출 주체 | 역할 |
|------|----------|------|
| `daemon_coin.py` | coin-daemon.service | 코인 전체 루프 |
| `daemon_stock.py` | stock-daemon.service | 주식 전체 루프 |
| `job_coin_buy.py` | daemon_coin + 수동 GHA | 코인 매수 |
| `job_coin_sell.py` | daemon_coin + 수동 GHA | 코인 매도 |
| `job_profit_buy_cloud.py` | stock-daemon | 주식 조건부 매수 |
| `job_profit_sell_cloud.py` | stock-daemon | 주식 수익매도 (auto_sell은 KRX 09:00~15:30만) |
| `job_balance.py` | stock-daemon (1분마다, `BALANCE_EVERY=2`×30초) + 수동 GHA | KIS 잔고 Gist 갱신. 실패 시 내부 3회 재시도(2026-08-15 추가) |
| `job_signals.py` | GHA signals | 매수 신호 생성 |
| `job_buy.py` | GHA buy | 신호 기반 매수 |
| `job_close.py` | GHA close | 마감 청산 |

---

## 수정 시 체크리스트

1. **새 잡 추가 시**: daemon이 처리하면 GHA 크론 비활성화 상태 유지
2. **크론 활성화 전**: 위 "크론 비활성화" 표와 대조 — 중복 확인 필수
3. **daemon 파일 수정 후**: `sudo systemctl restart <daemon>` 필수
4. **GHA self-hosted 잡**: Oracle VM에서 실행됨 — daemon과 동일 코드 실행 위험

---

## 운영 로그 (Gist 직접수정 · VM 수동조치 — git log에 안 남는 것들)

**코드 변경은 git 커밋 메시지에 남으니 `git log`로 확인하면 됨.** 이 표는
**Gist 파일을 스크립트로 직접 PATCH했거나, 정식 배포 절차 없이 VM에서 수동으로
뭔가 처리한 경우**만 기록한다 — 다른 머신(Mac/Windows)에서 작업을 이어받는
세션이 git pull만으로는 놓치는 변경사항을 알 수 있도록 하기 위함
(2026-08-12: Mac 세션과 Windows 세션이 같은 버그를 동시에 각자 고쳐서 병합
충돌이 났던 사례가 있었음 — 코드는 git으로 해결됐지만, Gist/VM 직접 조치는
이런 로그가 없으면 서로 알 방법이 없음).

**작업 시작 시**: `git log`뿐 아니라 이 표도 확인할 것. 아래 항목이 이미
반영된 걸로 보이면(예: Gist 조회해서 확인) 중복 조치하지 말 것.

**새 항목 추가 형식**: `[YYYY-MM-DD HH:MM KST] 파일/대상 — 내용 (관련 커밋 있으면 해시)`

- [2026-08-12 09:57] `scalp_auto_config.json` (Gist) — `test_fixed_qty_until`을 2026-08-14로 연장 (모니터링 기간 중 1주씩만 매수)
- [2026-08-12 09:57] `scalp_auto_config.json` (Gist) — `stop_loss_pct_premarket` 3.6 추가 (NXT 프리마켓 전용 손절폭, quiet_hour×2). 관련: `a697edd`
- [2026-08-12 11:31] `scalp_stock_jobs.json` (Gist) — 유령 포지션 2건(148780 비큐AI, 226320 잇츠한불) `phase=watching`/`status=done`으로 수동 초기화. 근본 원인 수정: `205757e`
- [2026-08-16 10:2x] `scalp_auto_config.json` (Gist) — coin.min_volume_surge_ratio
  1.2→1.1, coin.max_day_chg_pct 5→7 (완화). 코인 자동발굴 빈도가 8/15~16
  이틀 연속 낮아서(8/15 20시간 공백, 8/16 오전 중 1건뿐) 사용자 승인 후
  테스트로 완화. 코드/킬스위치는 이상 없음 확인됨 — 순수 시장조건 필터가
  너무 빡빡했을 가능성 테스트. position_size(20,000원/건)는 변경 안 함.
  며칠 관찰 후 발굴 빈도·승률 재점검 필요.
- [2026-08-12 14:1x] `job_profit_sell_cloud.py` — VM `stock-daemon` scp 배포 + 재시작 (git push `b3c9ad1` 이후)
- [2026-08-15 09:3x] `trader_trades.json` → `trader_trades_coin.json`/`trader_trades_stock.json` — `migrate_trader_trades.py` VM 실행으로 기존 100건(전부 코인) 분리 이관 + `backfill_stock_trades.py 20260813 20260814`로 파일분리 이전 누락된 주식 체결 82건 백필. 관련: `35817f8`, `04ccaa4`
- [2026-08-20 21:xx] **초단타 잡상태 유실 재시도스팸 + 체결 중복기록 버그**(Mac 세션) —
  비트맥스(377030) 급락손절 후 15분간 34회 재시도 스팸 실측(청산 자체는 성공,
  KIS 계좌 잔고로 확인함 — 금전 피해 없음). 원인 2가지: (1) `job_scalp_stock.py`/
  `job_scalp_coin.py`의 `main()` 끝 잡 리스트 저장이 실패 여부 확인을 안 해서
  Gist 동시쓰기 충돌(409/403) 시 방금 청산한 상태가 유실 → 다음 사이클이 낡은
  상태로 재시도. (2) `job_balance.py`의 체결 중복확인이 여러 데몬 공유 30초
  캐시 때문에 같은 체결을 12번 중복 기록(`trader_trades_stock.json` 100건
  슬롯 중 11개를 헛되이 차지). 코드 수정: `3a2c14b`(재시도+에러로그 추가,
  캐시우회+pending버퍼 대조). **Gist `trader_trades_stock.json`의 기존 중복행
  11개는 스크립트로 직접 삭제**(git 비대상 — 실제 체결행 1개는 보존).
  내일 Win PC 세션은 이 커밋이 이미 VM에 배포·재시작까지 완료됐다는 전제로
  시작할 것 (`sudo systemctl restart scalp-daemon stock-daemon` 완료됨).
- [2026-08-21 03:xx] **⚠️ 심각 — 코인 자동매도 봇이 사용자 직접매매 코인까지
  임의로 매도한 사고**(Mac 세션, 사용자 리포트로 발견) — 사용자가 업비트
  앱에서 직접 매수한 리플(XRP)이 새벽에 봇에 의해 "자동 익절"로 팔림.
  원인: `job_coin_sell.py`의 `auto_sell_by_rule()`이 봇이 산 코인인지 사용자
  직접매매 코인인지 구분 없이 **계좌 전체 잔고**에 +20%/-4% 룰을 적용하고
  있었음(같은 사고로 ARX도 동시에 손절 시도 중이었음 — 최소주문금액 미달로
  실패해서 다행히 미체결). **`auto_sell_by_rule()` 호출 자체를 비활성화**
  (코드는 남겨둠, 필요시 "봇이 산 것만" 구분하도록 재설계 후 재활성화 검토).
  관련: `a940d12`. VM `coin-daemon` 재시작 완료, 재시작 후 로그로 더 이상
  안 뜨는 것 확인함.
  - 같은 근본원인(계좌 전체 스캔)이 `job_coin_grid.py`의 `_sync_orphan_coins()`
    (그리드 재초기화 시 "고아 코인" 자동 매도 등록)에도 있어서, 사용자 요청으로
    선제적으로 같이 수정함(`022e3a1`) — `job["grid_owned_qty"]`라는 새 장부
    필드(그리드 자신의 매수/매도 체결마다 증감)를 기준 상한으로 삼아, 계좌
    잔고가 아무리 많아도 그리드 장부를 넘는 수량(=사용자 직접매매분)은 절대
    건드리지 않도록 함. 현재 등록된 그리드 잡이 0개라 당장 동작 변화는 없음.
  - **내일 Win PC 세션 확인사항**: 코인 자동매도가 이제 `coin_sell_jobs.json`에
    사용자가 직접 등록한 잡만 처리함 — 계좌에 +20%/-4% 도달한 코인이 있어도
    더 이상 자동으로 안 팔림(의도된 변경, 되돌리지 말 것). 그리드 매매를 새로
    등록해서 쓸 경우 `grid_owned_qty` 장부가 정상적으로 쌓이는지(첫 매수체결
    로그에 이 필드 관련 코드 있음) 한 번 확인해보면 좋음.
- [2026-08-22 19:4x] **⚠️ 그리드 손절(stop_loss_on_escape)이 auto_reinit_minutes
  없으면 영구히 발동 안 하던 버그 — 사용자가 여러 번 반복 리포트, 실사고 발생**
  (Mac 세션). 리플 그리드가 하단 8%+ 이탈한 채 2시간 45분 넘게 손절 없이
  방치되는 걸 사용자가 직접 발견 — `_check_auto_reinit()`이 `auto_reinit_minutes`
  설정된 잡에서만 호출돼서, `stop_loss_on_escape=true`만 켜둔 잡은 이탈감지
  자체가 시작 안 됐음.
  - **코인**(`job_coin_grid.py`, 커밋 `a7b6d19`): 이탈감지(`_track_escape`)를
    공용 함수로 분리해 `auto_reinit_minutes` 설정 여부와 무관하게 항상 호출.
    손절 판단(`_check_stop_loss_on_escape`)도 독립 평가(대기시간: `auto_reinit_minutes`
    있으면 그 값, 없으면 기본 15분 `STOP_LOSS_DEFAULT_WAIT_MIN`). 자동 재설정
    (범위 이동)은 기존처럼 `auto_reinit_minutes` 명시 설정 시에만 동작 유지.
    `auto_reinit_minutes` 최소값도 10→5분으로 완화(사용자 요청, 프론트엔드
    `tab-cointrade.js` 동일 수정 — 5분 미만 입력 시 예전엔 경고 없이 조용히
    무시됐던 것도 같이 고침). **사용자가 리플 그리드 잡을 직접 삭제한 뒤
    VM 배포·`coin-daemon` 재시작 완료.**
  - **주식**(`job_stock_grid.py`, 커밋 `2b6acb4`): 완전히 동일한 버그가 있어서
    같은 방식으로 수정(`_check_out_of_range()` 내부에서 손절 평가를
    `auto_reinit_minutes` 게이트 밖으로 분리). 현재 등록된 주식 그리드 잡
    3개 전부 `status=stopped`라 배포 시점에 즉시 실행되는 건 없었음 — VM
    배포·`stock-daemon` 재시작 완료.
  - **검증**: 둘 다 `unittest.mock`으로 `upbit_api`/`kis_api`의 주문·취소·잔고조회
    (+ 주식은 `is_any_market_open`)를 전부 모킹해서 8개 시나리오(핵심 버그
    재현·수정 확인, 대기시간 미달시 오작동 없음, `stop_loss_on_escape=False`
    존중, 반대방향 돌파 제외, 범위복귀 처리, 기존 `auto_reinit_minutes` 조합
    유지, 장마감시 보류(주식만)) 전부 통과 — 실제 주문 없이 로직만 검증.
  - **내일 Win PC 세션 확인사항**: `STOP_LOSS_DEFAULT_WAIT_MIN=15`(양쪽 파일
    동일 상수)가 새로 생김 — 그리드 잡에 `stop_loss_on_escape`만 켜고
    `auto_reinit_minutes`를 안 쓰면 이제 15분 후 자동으로 손절이 실행됨(예전엔
    전혀 안 됐음, 의도된 변경). `auto_reinit_minutes` 최소값이 5분으로
    낮아졌으니 5~9분 사이 값을 쓰는 잡이 있으면 정상 동작하는지 한 번 확인.
