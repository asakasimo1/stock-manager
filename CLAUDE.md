# ⚠️⚠️⚠️ 이 저장소는 폐기됨 — 절대 사용/롤백 금지 (2026-09-01) ⚠️⚠️⚠️

**이 저장소(`asakasimo1/stock-trader`)는 `git subtree`로 이력 그대로
`asakasimo1/stock-manager`의 `trader/` 하위로 통합 이관 완료했고, 사용자가
곧 GitHub에서 직접 삭제할 예정이다.**

- 최신 코드/문서는 전부 `asakasimo1/stock-manager` 저장소의 `trader/`
  디렉토리에 있음 — 여기가 아니라 그쪽을 봐야 함.
- Oracle VM의 실거래 데몬 3개(coin-daemon/stock-daemon/scalp-daemon)는 이미
  `/home/ubuntu/stock-manager/trader`를 보도록 재배선 완료 — 이 저장소가
  가리키던 `/home/ubuntu/stock-trader`는 더 이상 어떤 서비스도 참조하지 않음
  (롤백 안전망으로 삭제만 안 하고 남겨둔 상태).
- GitHub Actions self-hosted runner(`vnic-trader`)도 `stock-manager`로
  재등록 완료 — 이 저장소엔 더 이상 붙어있지 않음.
- **다른 머신(Mac 등) 세션 주의**: 이 저장소의 로컬 클론이 남아있어서 "여기서
  고쳐줘/다시 배포해줘" 같은 요청이 와도, 실제 운영 중인 곳은
  `stock-manager/trader/`이므로 그쪽에서 작업할 것 — 이 저장소를 고쳐봐야
  아무 것도 반영되지 않음.

---

# stock-trader (Oracle VM 자동매매 백엔드) — 이하 원문 (역사적 기록용)

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
| `coin-runner` | Upbit API HTTP 서버 (port 3000) — Vercel 프론트엔드 fallback + 국내주식 분봉 캔들 캐시(`/api/stock-candles`, 2026-09-01 추가) | online |
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
- [2026-08-28 13:3x] `stock_grid_jobs.json` (Gist) — 두산에너빌리티 잡의
  `lower_price`/`upper_price`를 87,178/89,820 → 87,100/89,800으로 스크립트로
  직접 수정. 원인은 `initialize_grid()`가 반올림 전 요청값을 그대로
  저장해서 실제 격자 레벨(호가단위 반올림됨)과 어긋났던 버그(커밋
  `54d828c`로 코드는 수정됨, 이 잡은 그 수정 이전에 이미 초기화돼 있던
  상태라 다음 reinit 전까지 안 맞을 상태였음 — 사용자가 화면에서
  "하한보다 매수 대기 가격이 더 낮다"고 바로 리포트해서 데이터도 즉시
  맞춰줌). 실제 주문(격자별 order_no)은 건드리지 않음, 표시값만 보정.
- [2026-08-28 14:1x] 두산에너빌리티 034020 — **KIS 실주문 직접 조작 +
  Gist 수동 보정**. 격자편집을 짧은 간격(96초, 이후 한 번 더 5분여 뒤)으로
  반복 저장해 재초기화가 연달아 3번 발생 → 87,900원 초기 시장가매수 +
  87,800원 격자매수가 거의 동시에 체결돼 실제로 2주 보유 상태가 됨.
  세 번째 재초기화 때 `_extract_held_inventory()`가 87,800원 주문을
  **이미 체결됐는데도 미체결로 오판**(pending 캐시 staleness로 추정,
  근본원인은 미조사)해 이월하지 않고 버려서, 87,900원 몫(sell_waiting)만
  추적되고 87,800원 몫은 grids 배열에서 완전히 누락 — idle+수량잔존
  패턴도 아니어서 방금 추가한 자동복구(커밋 `761d2d9`)로도 못 잡는
  케이스였음. 사용자 요청으로 **직접 개입**: 기존 87,900원 몫 1주
  매도주문(0015177300) 취소 → 2주 합산 매도주문(0016458600, 88,500원)
  신규 등록 → Gist의 87900 레벨 항목을 `qty=2, last_buy_price=87850(2주
  평균), order_no=0016458600, state=sell_waiting`로 수동 보정(처음에
  state를 idle로 잘못 남겨서 자동복구 로직이 중복주문 낼 뻔한 것도
  바로 재수정함). 조작 후 미체결 목록 재조회로 중복 주문 없음 확인함.
  **후속 조치 필요**: 이번 건과 같은 "체결됐는데 pending으로 오판"
  패턴의 근본원인(캐시 무효화 타이밍?) 미조사 상태 — 재발 시 조사 필요.
  같은 시점에 격자편집 재저장 시 재초기화가 반복 발동하던 문제는 60초
  쿨다운 추가로 별도 차단함(프론트: stock-manager 레포 api/coin.js).
- [2026-09-01 08:3x] 두산에너빌리티 034020 — **고아 물량(장부 밖) 재발, KIS 실주문
  + Gist 수동 보정**. 사용자 리포트("매도가 왜 안 걸려있지")로 발견: 실계좌엔
  1주(평단 82,800원, 매도가능) 보유 중인데 `stock_grid_jobs.json`의 활성 격자
  2개(82,000/82,800원)가 전부 `buy_waiting`(신규 매수 대기, 미체결)이라 이
  1주를 추적하는 격자가 아예 없었음(idle+qty>0 자동복구 대상도 아닌, 8/28보다
  더 빠진 케이스 — 근본원인 미조사). 조치: 83,600원에 SELL 1주 지정가 신규
  등록(주문번호 0001942600) → Gist의 idle 상태이던 83,600원 격자 항목을
  `state=sell_waiting, qty=1, last_buy_price=82800, last_sell_price=83600,
  order_no=0001942600`로 갱신(82,000/82,800원 격자의 기존 매수 대기 주문은
  건드리지 않음 — 별개 사이클). 조치 후 미체결 목록 재조회로 신규 매도주문
  정상 등록 확인함. **후속 조치 필요**: 이 물량이 어느 시점에 장부에서
  빠졌는지(체결감지 타이밍? 8/31 09:46 재초기화 시 `_extract_held_inventory`
  누락?) 근본원인 미조사 상태 — 재발 시 조사 필요.
- [2026-09-01 09:xx] **stock-manager 그리드 매매 분봉 차트 미표시(재발 빈도
  높음) — 근본원인 2가지 확인 후 coin-runner로 이전**. 사용자가 스크린샷으로
  리포트: 두산에너빌리티 그리드 차트에 캔들이 안 보임(가격선/마커만 표시).
  원인 (1) `stock-manager/api/quote.js`가 Vercel 서버리스에서 KIS 분봉을
  인메모리로만 캐싱해서, 트래픽이 뜸해 인스턴스가 죽을 때마다(콜드스타트)
  캐시가 사라져 KIS를 처음부터 재수집 — Vercel 함수 기본 타임아웃(10초)에
  자주 걸림. (2) **더 결정적인 버그**: KIS 분봉조회(FHKST03010200)의
  FID_INPUT_HOUR_1엔 날짜 개념이 없어 항상 "오늘" 기준으로 해석되는데,
  장 시작 직후처럼 당일 데이터가 한 페이지(30건)를 못 채우면 KIS가 전일
  데이터로 패딩해서 줌 — 그 전일 시:분을 다음 페이지 앵커로 그대로 재사용하면
  "오늘 그 시각"으로 재해석되어 미래 시각을 조회하게 되고, KIS가 그 미래
  슬롯을 마지막 종가로 채운 평평한 가짜 봉들로 채워 응답 → 실제 데이터가
  차트 좌측 끝 좁은 폭으로 밀려나 사실상 안 보임. 장 시작 직후(KRX 09:00,
  NXT 08:00) 매번 100% 재현되는 버그라 "재발 빈도가 매우 높다"던 사용자
  체감과 일치. **조치**: KIS 페이징 수집 로직 전체를 `coin-runner/
  stock_candles.js`(신규)로 이전 — PM2 상시구동이라 콜드스타트 자체가
  없어지고, 날짜 경계를 인지하도록 재작성(미래시각 패딩 행 버림 + 페이지
  내 날짜가 바뀌면 그 페이지로 페이징 중단). `coin-runner/server.js`에
  `GET /api/stock-candles?ticker=&interval=1m|10m|1h`(x-api-key 인증) 추가,
  `coin-runner/.env`에 `KIS_APP_KEY`/`KIS_APP_SECRET` 추가(stock-trader와 동일
  앱키 재사용 — 토큰 24시간 캐시라 발급 경합 없음, 실측으로 확인됨).
  `stock-manager/api/quote.js`의 해당 분기는 이 VM 엔드포인트로 프록시만
  하도록 축소(관련 KIS 직접호출 코드 전체 삭제). Vercel 환경변수
  `ORACLE_CANDLES_URL` 추가(기존 `ORACLE_DATA_KEY` 인증키 재사용). VM
  `pm2 restart coin-runner --update-env` + Vercel `vercel --prod` 배포 완료,
  1m/10m/1h 전부 실측으로 미래시각 없음 확인.
  **일/주/월봉(1d/1w/1M) 분기는 그대로 Vercel에 남음** — 네이버 fchart 단일
  호출이라 이 문제 대상 아님.
