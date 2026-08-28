
// ══════════════════════════════════════════════════════════
// 설정
// ══════════════════════════════════════════════════════════
const SETTINGS_KEY = 'app-settings-v1';
const TAB_LABELS = {
  dashboard:'📊 대시보드', portfolio:'📈 포트폴리오', market:'📊 시장현황',
  etf:'💹 ETF', stocks:'📊 개별주', autotrade:'🤖 자동매매', cointrade:'🪙 자동코인매매', scalp:'⚡ 초단타',
};
let _settings = { hiddenTabs:[], darkMode:true, defaultTab:'dashboard', autoRefreshSec:0 };
let _autoRefreshTimer = null;

function _loadSettings() {
  try {
    const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || 'null');
    if (s) _settings = { ..._settings, ...s };
  } catch(_) {}
}

function _saveSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(_settings)); } catch(_) {}
}

function _applySettingsState() {
  document.body.classList.toggle('dark', !!_settings.darkMode);
  _applyTabVisibility();
  clearInterval(_autoRefreshTimer);
  if (_settings.autoRefreshSec > 0) {
    _autoRefreshTimer = setInterval(() => {
      const cur = document.querySelector('.tab-btn.active')
        ?.getAttribute('onclick')?.match(/switchTab\('(\w+)'\)/)?.[1];
      if (cur === 'dashboard') initDashboard();
    }, _settings.autoRefreshSec * 1000);
  }
}

function _applyTabVisibility() {
  TAB_ORDER.forEach(tab => {
    const btn = document.querySelector(`.tab-btn[onclick="switchTab('${tab}')"]`);
    if (btn) btn.style.display = _settings.hiddenTabs.includes(tab) ? 'none' : '';
  });
  const activeBtn = document.querySelector('.tab-btn.active');
  if (activeBtn && activeBtn.style.display === 'none') {
    const first = TAB_ORDER.find(t => !_settings.hiddenTabs.includes(t));
    if (first) switchTab(first);
  }
}

function applySettings() {
  const darkEl = document.getElementById('set-dark');
  if (darkEl) _settings.darkMode = darkEl.checked;
  const dtEl = document.getElementById('set-default-tab');
  if (dtEl) _settings.defaultTab = dtEl.value;
  const arEl = document.getElementById('set-auto-refresh');
  if (arEl) _settings.autoRefreshSec = +arEl.value;
  _settings.hiddenTabs = [];
  document.querySelectorAll('.set-tab-toggle').forEach(cb => {
    if (!cb.checked) _settings.hiddenTabs.push(cb.dataset.tab);
  });
  _saveSettings();
  _applySettingsState();
}

const SETTINGS_PW_KEY = 'settings-pw-v1';
const DEFAULT_PW_HASH = '1234';

function _hashPw(pw) { return pw; }

function openSettingsWithAuth() {
  const stored = localStorage.getItem(SETTINGS_PW_KEY);
  if (!stored) {
    openSettings();
    return;
  }
  const el = document.getElementById('pw-overlay');
  if (el) {
    document.getElementById('pw-input').value = '';
    document.getElementById('pw-error').style.display = 'none';
    el.classList.add('open');
    setTimeout(() => document.getElementById('pw-input').focus(), 100);
  }
}

function checkSettingsPw() {
  const stored = localStorage.getItem(SETTINGS_PW_KEY) || DEFAULT_PW_HASH;
  const input = document.getElementById('pw-input').value;
  if (_hashPw(input) === stored) {
    closePwModal();
    openSettings();
  } else {
    document.getElementById('pw-error').style.display = 'block';
    document.getElementById('pw-input').value = '';
    document.getElementById('pw-input').focus();
  }
}

function closePwModal() {
  const el = document.getElementById('pw-overlay');
  if (el) el.classList.remove('open');
}

function openSettings() {
  const darkEl = document.getElementById('set-dark');
  if (darkEl) darkEl.checked = !!_settings.darkMode;
  const dtEl = document.getElementById('set-default-tab');
  if (dtEl) dtEl.value = _settings.defaultTab || 'dashboard';
  const arEl = document.getElementById('set-auto-refresh');
  if (arEl) arEl.value = String(_settings.autoRefreshSec || 0);
  const list = document.getElementById('set-tabs-list');
  if (list) {
    list.innerHTML = TAB_ORDER.map(tab => `
      <div class="settings-row">
        <div class="settings-row-label">${TAB_LABELS[tab]}</div>
        <label class="toggle">
          <input type="checkbox" class="set-tab-toggle" data-tab="${tab}"
            ${_settings.hiddenTabs.includes(tab) ? '' : 'checked'}
            onchange="applySettings()">
          <span class="toggle-slider"></span>
        </label>
      </div>`).join('');
  }
  document.getElementById('settings-overlay').classList.add('open');
}

function closeSettings() {
  document.getElementById('settings-overlay').classList.remove('open');
}

function saveSettingsPw() {
  const n = document.getElementById('set-pw-new').value;
  const c = document.getElementById('set-pw-confirm').value;
  const msg = document.getElementById('set-pw-msg');
  if (!n) {
    localStorage.removeItem(SETTINGS_PW_KEY);
    msg.style.display = 'block'; msg.style.color = 'var(--green)'; msg.textContent = '비밀번호가 제거되었습니다.';
    document.getElementById('set-pw-new').value = '';
    document.getElementById('set-pw-confirm').value = '';
    return;
  }
  if (n !== c) {
    msg.style.display = 'block'; msg.style.color = 'var(--red)'; msg.textContent = '비밀번호가 일치하지 않습니다.';
    return;
  }
  localStorage.setItem(SETTINGS_PW_KEY, _hashPw(n));
  msg.style.display = 'block'; msg.style.color = 'var(--green)'; msg.textContent = '비밀번호가 저장되었습니다.';
  document.getElementById('set-pw-new').value = '';
  document.getElementById('set-pw-confirm').value = '';
}

_loadSettings();

// ══════════════════════════════════════════════════════════
// 완료/취소 내역 리스트 공용 유틸 — 접기/펼치기 + 최근 N일 필터
// (자동매매/자동코인매매/초단타 탭의 내역 섹션이 공유)
// ══════════════════════════════════════════════════════════
const HISTORY_DAYS = 3;
const _histCollapsed = {};   // key -> bool, 명시적으로 펼치기 전엔 기본 접힘

function withinLastDays(dateStr, days = HISTORY_DAYS) {
  if (!dateStr) return true;   // 날짜 정보 없으면 걸러내지 않음 (구버전 데이터 호환)
  const d = new Date(String(dateStr).replace(' ', 'T'));
  if (isNaN(d.getTime())) return true;
  return (Date.now() - d.getTime()) <= days * 86400000;
}

function applyHistoryCollapse(key) {
  const collapsed = _histCollapsed[key] !== false;  // 기본값: 접힘
  const body = document.getElementById(`hist-body-${key}`);
  const icon = document.getElementById(`hist-icon-${key}`);
  if (body) body.style.display = collapsed ? 'none' : 'block';
  if (icon) icon.textContent = collapsed ? '▶' : '▼';
}

function toggleHistoryCollapse(key) {
  _histCollapsed[key] = _histCollapsed[key] === false;  // 토글
  applyHistoryCollapse(key);
}

function setHistoryCount(key, count) {
  const el = document.getElementById(`hist-count-${key}`);
  if (el) el.textContent = `${count}건 · 최근 ${HISTORY_DAYS}일`;
  applyHistoryCollapse(key);
}

// ══════════════════════════════════════════════════════════
// 탭 전환
// ══════════════════════════════════════════════════════════
const TAB_ORDER = ['dashboard', 'portfolio', 'market', 'etf', 'stocks', 'autotrade', 'cointrade', 'scalp'];

function switchTab(name) {
  document.querySelectorAll('.tab-page').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  const btn = document.querySelector(`.tab-btn[onclick="switchTab('${name}')"]`);
  if (btn) {
    btn.classList.add('active');
    btn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }
  if (name === 'dashboard') initDashboard();
  if (name === 'portfolio') initPortfolio();
  if (name === 'ipo') loadIpoRecords();
  if (name === 'market') loadMarketData();
  if (name === 'etf') loadEtfRecords();
  if (name === 'stocks') loadStockRecords();
  if (name === 'autotrade') initAutoTrade();
  if (name === 'cointrade') initCoinTrade();
  if (name === 'scalp') initScalp();
}

// ── 탭 스와이프 (모바일) ──────────────────────────────────
(function() {
  let _sx = 0, _sy = 0, _multi = false;
  document.addEventListener('touchstart', e => {
    if (e.touches.length > 1) { _multi = true; return; }  // 핀치 줌 등 멀티터치 무시
    _multi = false;
    _sx = e.touches[0].clientX;
    _sy = e.touches[0].clientY;
  }, { passive: true });
  document.addEventListener('touchcancel', () => { _multi = true; }, { passive: true });
  document.addEventListener('touchend', e => {
    if (_multi) return;                          // 멀티터치 후 탭 전환 차단
    if (e.changedTouches.length !== 1) return;  // 손가락이 1개일 때만 스와이프 판정
    const t = e.target;
    // 버튼·입력·링크·인터랙티브 요소에서 끝난 경우 탭 전환 차단
    
    if (t.closest('button, a, input, select, textarea')) return;
    const dx = e.changedTouches[0].clientX - _sx;
    const dy = e.changedTouches[0].clientY - _sy;
    if (Math.abs(dx) < 80 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    const active = document.querySelector('.tab-btn.active');
    if (!active) return;
    const cur = active.getAttribute('onclick').match(/switchTab\('(\w+)'\)/)?.[1];
    const visible = TAB_ORDER.filter(t => !(_settings.hiddenTabs||[]).includes(t));
    const idx = visible.indexOf(cur);
    if (dx < 0 && idx < visible.length - 1) switchTab(visible[idx + 1]);
    if (dx > 0 && idx > 0) switchTab(visible[idx - 1]);
  }, { passive: true });
})();

// ══════════════════════════════════════════════════════════
// /api/data 전역 공유 캐시 (5분 TTL) — 탭 전환 시 중복 Gist 호출 방지
// ══════════════════════════════════════════════════════════
let _sharedGistData = null;
let _sharedGistAt   = 0;
const GIST_CACHE_MS = 5 * 60 * 1000;
let _gistFetchPromise = null; // 동시 호출 dedup

async function _fetchGistData(force = false) {
  const now = Date.now();
  if (!force && _sharedGistData && now - _sharedGistAt < GIST_CACHE_MS) return _sharedGistData;
  // 캐시가 만료됐지만 데이터 있음 → stale 즉시 반환 + 백그라운드 갱신
  if (!force && _sharedGistData) {
    if (!_gistFetchPromise) {
      _gistFetchPromise = fetch('/api/data')
        .then(r => r.ok ? r.json() : Promise.reject())
        .then(d => { _sharedGistData = d; _sharedGistAt = Date.now(); })
        .catch(() => {})
        .finally(() => { _gistFetchPromise = null; });
    }
    return _sharedGistData;
  }
  if (_gistFetchPromise) return _gistFetchPromise;
  _gistFetchPromise = fetch('/api/data')
    .then(r => r.ok ? r.json() : { briefing: [], picks: [], signals: [] })
    .then(d => { _sharedGistData = d; _sharedGistAt = Date.now(); return d; })
    .catch(() => _sharedGistData || { briefing: [], picks: [], signals: [] })
    .finally(() => { _gistFetchPromise = null; });
  return _gistFetchPromise;
}

// ══════════════════════════════════════════════════════════
// JSONBin 번들 캐시 (5분 TTL) — 탭 전환 시 중복 API 호출 방지
// ══════════════════════════════════════════════════════════
let _binData = null, _binDataAt = 0, _binFetchPromise = null;
const BIN_CACHE_MS = 5 * 60 * 1000;

async function _fetchBinData(force = false) {
  const now = Date.now();
  if (!force && _binData && now - _binDataAt < BIN_CACHE_MS) return _binData;
  // 캐시가 만료됐지만 데이터 있음 → stale 즉시 반환 + 백그라운드 갱신
  if (!force && _binData) {
    if (!_binFetchPromise) {
      _binFetchPromise = fetch(`/api/etf?bundle=1&_t=${now}`)
        .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status}`)))
        .then(d => { _binData = d; _binDataAt = Date.now(); })
        .catch(() => {})
        .finally(() => { _binFetchPromise = null; });
    }
    return _binData;
  }
  if (_binFetchPromise) return _binFetchPromise;
  _binFetchPromise = fetch(`/api/etf?bundle=1&_t=${now}`)
    .then(r => r.ok ? r.json() : Promise.reject(new Error(`${r.status}`)))
    .then(d => { _binData = d; _binDataAt = Date.now(); return d; })
    .catch(() => _binData || {})
    .finally(() => { _binFetchPromise = null; });
  return _binFetchPromise;
}

function _invalidateBinCache() { _binData = null; _binDataAt = 0; }

// 동시 현재가 요청 개수 제한 (semaphore)
function _makeSemaphore(limit) {
  let running = 0;
  const queue = [];
  return function acquire() {
    return new Promise(resolve => {
      const run = () => { running++; resolve(() => { running--; if (queue.length) queue.shift()(); }); };
      running < limit ? run() : queue.push(run);
    });
  };
}
const _priceSem = _makeSemaphore(5); // 동시 최대 5개 현재가 API 요청

// 페이지 로드 직후 캐시 워밍 (첫 탭 진입 시 대기시간 제거)
setTimeout(() => { _fetchGistData(); _fetchBinData(); }, 0);

// ══════════════════════════════════════════════════════════
// 대시보드 데이터 로드
// ══════════════════════════════════════════════════════════
let _dashData = null;
let _ipoRecords = [];   // ← Script 1에서 선언: initDashboard·markSubscribed 모두 이 변수 공유

// ══════════════════════════════════════════════════════════
// 그리드 격자개수 → 하한/상한 자동계산 (코인/주식 그리드 탭 공유)
// ══════════════════════════════════════════════════════════
// 2026-08-28 사용자 요청 — "격자 갯수를 편집하는 항목이 없는 게 문제,
// 격자와 간격%를 자동으로 계산하여 상한가·하한가가 자동으로 업데이트
// 되도록". 현재가를 기준으로 위/아래 절반씩 격자를 배치(자동 재초기화
// 로직의 n_each 대칭 배치와 동일한 방식) — 총 격자개수 gridCount는
// 하한~상한 사이 레벨 총 개수(build_levels 결과 길이)와 근사적으로
// 일치하도록 계산(간격 step 개수 = gridCount-1을 위/아래로 절반씩 배분).
function _gridCalcRange(curPrice, gridCount, pct) {
  const n = Number(gridCount), p = Number(pct);
  if (!curPrice || !isFinite(curPrice) || !n || n < 2 || !p || p <= 0) return null;
  const steps = n - 1;
  const below = Math.floor(steps / 2);
  const above = steps - below;
  const ratio = 1 + p / 100;
  return {
    lower: curPrice / Math.pow(ratio, below),
    upper: curPrice * Math.pow(ratio, above),
  };
}

// KRX 호가단위(2023.1 개편 이후 KOSPI/KOSDAQ 공통) — kis_api.py의
// _PRICE_UNITS/round_price와 반드시 동일한 표를 써야 함. 실제 주식은
// 10원 단위로 거래되지 않는데(예: 87,178원) 프론트 계산값을 그대로
// 보여주고 있었음(2026-08-28 사용자 리포트: "상한 하한 가격은 100원
// 단위로 버림 혹은 올림 되어야 할 것 같네, 실제 10원단위 거래가 되질
// 않잖아") — 하한은 내림, 상한은 올림해서 사용자가 요청한 범위를
// 절대 좁히지 않으면서 실제 호가에 맞춘다. 코인(Upbit)은 호가단위
// 체계가 전혀 달라 이 테이블을 적용하면 안 됨 — 주식 전용.
const KRX_PRICE_UNITS = [
  [500000, 1000], [200000, 500], [50000, 100], [20000, 50], [5000, 10], [2000, 5], [0, 1],
];
function _krxPriceUnit(price) {
  for (const [threshold, unit] of KRX_PRICE_UNITS) {
    if (price >= threshold) return unit;
  }
  return 1;
}
function _krxFloorPrice(price) {
  const unit = _krxPriceUnit(price);
  return Math.floor(price / unit) * unit;
}
function _krxCeilPrice(price) {
  const unit = _krxPriceUnit(price);
  return Math.ceil(price / unit) * unit;
}

// 그리드 범위 이탈 공용 헬퍼 (코인/주식 그리드 탭 공유)
// ══════════════════════════════════════════════════════════
// 코인(job_coin_grid.py)은 2026-08-22 리팩터로 escaped_at 필드를 쓰고,
// 주식(job_stock_grid.py)은 같은 날 같은 버그를 out_of_range_since 필드를
// 유지한 채로 고쳤음 — 필드명이 갈라져 있어 둘 다 확인한다. 백엔드의
// wait_min = auto_reinit_minutes || 15(STOP_LOSS_DEFAULT_WAIT_MIN)와
// 반드시 같은 상수를 써야 함 — 다르면 UI 카운트다운이 실제 손절/재설정
// 타이밍과 어긋난다.
const GRID_STOP_LOSS_DEFAULT_WAIT_MIN = 15;

function _gridEscapeInfo(job) {
  const sinceStr = job.escaped_at || job.out_of_range_since;
  if (!sinceStr) return null;

  // "YYYY-MM-DD HH:MM" (KST, 타임존 표기 없음) → 명시적으로 KST로 파싱
  const since = new Date(sinceStr.replace(' ', 'T') + ':00+09:00');
  if (isNaN(since.getTime())) return null;

  const elapsedMin = Math.max(0, Math.floor((Date.now() - since.getTime()) / 60000));
  const timeLabel  = sinceStr.slice(11);  // "HH:MM"
  const autoMin    = job.auto_reinit_minutes;
  const waitMin    = autoMin || GRID_STOP_LOSS_DEFAULT_WAIT_MIN;

  return { since, sinceStr, timeLabel, elapsedMin, autoMin, waitMin };
}

// ══════════════════════════════════════════════════════════
// 그리드 매매 현황 — 10분봉 캔들차트 + 가격선 오버레이 (코인/주식 공유)
// ══════════════════════════════════════════════════════════
// 색상은 dataviz 가이드로 검증됨 — 매수(앱 --primary #3D5AFE)/매도(신규
// --state-sell, 라이트#eb6834·다크#d95926)는 CVD ΔE 31.8/26.8·일반시각
// 40.0/31.8(모두 8/15 기준 통과), --border(idle)는 무채색이라 대상 아님.
// 현재가 기준선은 카테고리 색과 겹치는 걸 피하려고 일부러 색상 대신
// 텍스트 잉크+점선으로 표현(참조선은 "계열"이 아니라 구조적 주석이라
// 색상 채널을 새로 하나 더 쓰지 않는 게 맞음 — violet(#8b5cf6)을 넣어
// 봤더니 --primary와 일반시각 ΔE 11.1로 실패해서 폐기).

// job.grids[] + job 설정(krw_per_grid, grid_pct)을 가격/금액 기준으로 정규화.
// qtyField: 코인은 'coin_qty', 주식은 'qty' — 그 외 필드명은 전부 동일.
function _gridLevelsNormalize(job, qtyField) {
  const grids = Array.isArray(job.grids) ? job.grids : [];
  return grids.map(g => {
    if (g.state === 'buy_waiting') {
      return { price: g.level, state: 'buy_waiting', amount: job.krw_per_grid || 0 };
    }
    if (g.state === 'sell_waiting') {
      const sp = g.last_sell_price || Math.round(g.level * (1 + (job.grid_pct || 0) / 100));
      const amount = Math.round((g[qtyField] || 0) * sp);
      return { price: sp, state: 'sell_waiting', amount };
    }
    return { price: g.level, state: 'idle', amount: 0 };
  });
}

// ── 10분봉 캔들차트 + 그리드 가격선 오버레이 ─────────────────────────
// (SVG 래더는 폐기 — lightweight-charts의 createPriceLine으로 실제 캔들
// 위에 매수/매도 대기가를 직접 겹쳐 그리는 게 "시간 흐름 속 현재가 맥락 +
// 그리드 기준가"를 한 화면에서 훨씬 조화롭게 보여줌).
// 시간대별 캔들 캐시 TTL — 분봉은 자주 바뀌니 짧게, 일/주/월봉은 하루~수시간에
// 한 번만 바뀌는 데이터라 길게 잡아도 무방(오히려 불필요한 재조회를 줄여줌).
// 코인(Upbit)은 어느 시간대든 단일 콜이라 부담 적고, 주식 1분/10분/1시간봉은
// KIS 1분봉 페이징(최대 5~14콜)이 필요한 무거운 호출이라 짧은 TTL이 곧 방어막.
const GRID_CHART_CACHE_MS = { '1m': 60000, '10m': 150000, '1h': 150000, '1d': 1800000, '1w': 10800000, '1M': 21600000 };
const GRID_TIMEFRAMES = [
  { key: '1m',  label: '1분' },
  { key: '10m', label: '10분' },
  { key: '1h',  label: '1시간' },
  { key: '1d',  label: '일' },
  { key: '1w',  label: '주' },
  { key: '1M',  label: '월' },
];
const _gridChartCandleCache = {};   // "ticker:timeframe" → {candles, ts}
// 서버(api/quote.js)의 분봉 캐시는 서버리스 인스턴스 메모리에 있어 배포·
// 콜드스타트로 아무 때나 리셋될 수 있음 — 리셋되면 "최근 30분치"부터 다시
// 점진적으로 채워지는데, 그 사이 클라이언트가 재조회하면 화면에 보이던
// 차트 범위가 도로 좁아져 버림(2026-08-28 사용자 리포트 1차: "시간이 꽤
// 흘렀는데도 차트 기간이 안 늘어난다"). 처음엔 브라우저 메모리에만
// 누적했는데, "새로고침을 자주 할 텐데 그럼 또 초기화되지 않냐"는 2차
// 지적이 맞아서 — localStorage에 영구 저장해 새로고침·탭 재방문에도
// 누적분이 유지되도록 한다(기기별 저장이라 다른 기기/브라우저에선 다시
// 쌓이지만, 같은 브라우저로 계속 보는 일반적인 사용 패턴엔 충분).
const _gridChartCandleHistory = {}; // "ticker:timeframe" → 누적 병합된 candles[] (localStorage와 동기화된 메모리 캐시)
const _GRID_INTRADAY_TF = ['1m', '10m', '1h'];
const _GRID_CANDLE_LS_PREFIX = 'gridCandleHist:';

function _gridCandleHistLoad(cacheKey) {
  if (_gridChartCandleHistory[cacheKey]) return _gridChartCandleHistory[cacheKey];
  try {
    const raw = localStorage.getItem(_GRID_CANDLE_LS_PREFIX + cacheKey);
    const parsed = raw ? JSON.parse(raw) : null;
    if (Array.isArray(parsed)) { _gridChartCandleHistory[cacheKey] = parsed; return parsed; }
  } catch (_) {}
  return [];
}

function _gridCandleHistSave(cacheKey, candles) {
  _gridChartCandleHistory[cacheKey] = candles;
  try { localStorage.setItem(_GRID_CANDLE_LS_PREFIX + cacheKey, JSON.stringify(candles)); } catch (_) {}
}
const _gridChartInstances   = {};   // containerId → lightweight-charts 인스턴스(재렌더 시 정리용)
const _gridChartToken       = {};   // containerId → 최신 렌더 호출 토큰(경쟁 상태 방지)
const _gridChartTimeframe   = {};   // containerId → 현재 선택된 시간대(기본 '10m')
const _gridChartLastArgs    = {};   // containerId → 마지막 렌더 인자(시간대 전환 시 즉시 재렌더용)

async function _fetchGridCandles(ticker, isCoin, timeframe) {
  const tf = timeframe || '10m';
  const cacheKey = `${ticker}:${tf}`;
  const cached = _gridChartCandleCache[cacheKey];
  const ttl = GRID_CHART_CACHE_MS[tf] || 150000;
  if (cached && (Date.now() - cached.ts) < ttl) return cached.candles;
  try {
    let url;
    if (isCoin) {
      const periodMap = { '1d': 'day', '1w': 'week', '1M': 'month' };
      const unitMap  = { '1m': 1, '10m': 10, '1h': 60 };
      const countMap = { '1m': 120, '10m': 36, '1h': 48 }; // 1시간봉: 최근 48시간(2일)
      url = periodMap[tf]
        ? `/api/coin-price?candles=1&market=${encodeURIComponent(ticker)}&period=${periodMap[tf]}&count=${tf === '1M' ? 60 : 100}`
        : `/api/coin-price?candles=1&market=${encodeURIComponent(ticker)}&unit=${unitMap[tf] || 10}&count=${countMap[tf] || 36}`;
    } else {
      url = `/api/quote?ticker=${encodeURIComponent(ticker)}&chart=1&interval=${tf}`;
    }
    const r = await fetch(url);
    const d = await r.json();
    const fresh = Array.isArray(d.candles) ? d.candles : [];

    let candles = fresh;
    if (_GRID_INTRADAY_TF.includes(tf) && fresh.length) {
      const merged = new Map();
      for (const c of _gridCandleHistLoad(cacheKey)) merged.set(c.time, c);
      for (const c of fresh) merged.set(c.time, c); // 진행 중인 봉은 최신값으로 덮어씀
      candles = [...merged.values()].sort((a, b) => a.time - b.time);
      if (candles.length > 300) candles = candles.slice(-300); // 무한 누적 방지
      _gridCandleHistSave(cacheKey, candles);
    }

    _gridChartCandleCache[cacheKey] = { candles, ts: Date.now() };
    return candles;
  } catch (_) {
    return cached?.candles || [];
  }
}

// 시간대 버튼 클릭 시 호출 — 선택 상태만 바꾸고 마지막 렌더 인자로 즉시 재렌더.
function _switchGridChartTimeframe(containerId, tf) {
  _gridChartTimeframe[containerId] = tf;
  const args = _gridChartLastArgs[containerId];
  if (!args) return;
  renderGridChart(containerId, args.job, args.curPrice, args.qtyField, args.ticker, args.isCoin);
}

function _gridChartTfBarHtml(containerId, activeTf) {
  return GRID_TIMEFRAMES.map(tf => {
    const active = tf.key === activeTf;
    return `<button onclick="_switchGridChartTimeframe('${containerId}','${tf.key}')"
      style="padding:2px 9px;border-radius:5px;font-size:11px;cursor:pointer;
      border:1px solid ${active ? 'var(--primary)' : 'var(--border)'};
      background:${active ? 'var(--primary)' : 'none'};
      color:${active ? '#fff' : 'var(--muted)'};font-weight:${active ? 700 : 400}">${tf.label}</button>`;
  }).join('');
}

// job.trade_history(주식 그리드 당일 체결 내역, tab-autotrade.js
// atCalcDayProfit과 동일 필드 — 한 행 = 한 사이클의 매수+매도)를 캔들
// 시간축에 맞춰 마커로 변환. 기존엔 매수/매도 "대기가" 라인만 그려서
// 실제 체결이 어디서 일어났는지가 차트에 전혀 표시되지 않았음(2026-08-28
// 사용자 리포트: "86000원에 매도 체결됐는데 차트에 안 보였다" — 가려진
// 게 아니라 애초에 안 그리고 있었음). 코인 그리드 잡은 프론트에 체결
// 내역을 안 내려줘서(trade_history 없음) 이 함수는 자연히 빈 배열을 반환
// — 주식 그리드에만 해당.
function _buildFillMarkers(job, candles, buyColor, sellColor) {
  const hist = Array.isArray(job.trade_history) ? job.trade_history : [];
  if (!hist.length || !candles.length) return [];
  const isDateAxis = typeof candles[0].time === 'string';

  const toCandleTime = (dateStr, timeStr) => {
    if (!dateStr) return null;
    if (isDateAxis) return candles.some(c => c.time === dateStr) ? dateStr : null; // 일/주/월봉
    const epoch = Math.floor(Date.parse(`${dateStr}T${(timeStr || '00:00:00')}+09:00`) / 1000);
    if (!isFinite(epoch)) return null;
    const first = candles[0].time, last = candles[candles.length - 1].time;
    if (epoch < first - 600 || epoch > last + 600) return null; // 현재 보이는 구간 밖 체결은 스킵
    let best = null, bestDiff = Infinity;
    for (const c of candles) {
      const diff = Math.abs(c.time - epoch);
      if (diff < bestDiff) { bestDiff = diff; best = c.time; }
    }
    return best;
  };

  const markers = [];
  for (const h of hist) {
    if (h.sell_price != null) {
      const t = toCandleTime(h.date, h.time);
      if (t != null) markers.push({
        time: t, position: 'aboveBar', color: sellColor, shape: 'arrowDown',
        text: `매도 ${Math.round(h.sell_price).toLocaleString()}`,
      });
    }
    // 매수 체결(같은 사이클의 시작) — buy_time은 같은 날짜 기준(HH:MM:SS)으로
    // 가정, 그리드 특성상 매수·매도가 다른 날 걸쳐 일어날 수 있어 근사치임
    if (h.buy_price != null && h.buy_time) {
      const t = toCandleTime(h.date, h.buy_time);
      if (t != null) markers.push({
        time: t, position: 'belowBar', color: buyColor, shape: 'arrowUp',
        text: `매수 ${Math.round(h.buy_price).toLocaleString()}`,
      });
    }
  }

  markers.sort((a, b) => {
    const av = typeof a.time === 'number' ? a.time : Date.parse(a.time);
    const bv = typeof b.time === 'number' ? b.time : Date.parse(b.time);
    return av - bv;
  });
  return markers;
}

// containerId 엘리먼트에 job 하나의 10분봉(최근 6시간) + 매수/매도 대기가
// 오버레이 차트를 그린다. curPrice: 현재가(숫자, 없으면 기준선 생략).
// qtyField: 'coin_qty' | 'qty'. ticker: Upbit market 코드 또는 KIS 종목코드.
async function renderGridChart(containerId, job, curPrice, qtyField, ticker, isCoin) {
  if (typeof LightweightCharts === 'undefined') return;
  _gridChartLastArgs[containerId] = { job, curPrice, qtyField, ticker, isCoin }; // 시간대 전환 버튼용

  // 초기 로딩 시 agRenderJobs/ctRenderGridJobs가 동기 1회 + 가격 폴링 완료 후
  // 1회, 총 2번 거의 동시에 호출되는 경우가 있다 — 둘 다 아래 await 시점에
  // 컨테이너에 아직 인스턴스가 없는 걸 보고 통과해버려서 같은 컨테이너에
  // 차트가 2개 겹쳐 그려짐(실측: 첫 진입 시 2개, 탭 이동 후 재진입하면 1개
  // — 그때는 호출이 1번만 일어나서). 호출마다 토큰을 발급하고, await 이후
  // 가장 최근 토큰이 아니면 그리지 않고 조용히 포기한다.
  const myToken = (_gridChartToken[containerId] = (_gridChartToken[containerId] || 0) + 1);

  const timeframe = _gridChartTimeframe[containerId] || '10m';
  const candles = await _fetchGridCandles(ticker, isCoin, timeframe);
  if (_gridChartToken[containerId] !== myToken) return; // 더 최신 호출이 있었음 — 이 호출은 폐기

  const outer = document.getElementById(containerId);
  if (!outer || !document.body.contains(outer)) return; // 그 사이 탭 이동 등으로 DOM에서 사라졌을 수 있음

  // 시간대 버튼 바 + 실제 차트를 별도 하위 div로 분리 — 버튼 바는 매번 다시
  // 그려도(활성 상태 갱신) 차트 쪽만 인스턴스를 정리/재생성하면 되게 한다.
  let barEl = outer.querySelector('.grid-chart-tf-bar');
  let chartEl = outer.querySelector('.grid-chart-inner');
  if (!barEl || !chartEl) {
    outer.innerHTML = `<div class="grid-chart-tf-bar" style="display:flex;gap:4px;margin-bottom:6px"></div><div class="grid-chart-inner" style="height:220px"></div>`;
    barEl = outer.querySelector('.grid-chart-tf-bar');
    chartEl = outer.querySelector('.grid-chart-inner');
  }
  barEl.innerHTML = _gridChartTfBarHtml(containerId, timeframe);

  if (_gridChartInstances[containerId]) {
    try { _gridChartInstances[containerId].remove(); } catch (_) {}
    delete _gridChartInstances[containerId];
  }
  chartEl.innerHTML = ''; // 방어적으로 잔여 DOM 제거

  const cs = getComputedStyle(document.body);
  const buyColor    = cs.getPropertyValue('--state-buy').trim()  || '#3D5AFE';
  const sellColor   = cs.getPropertyValue('--state-sell').trim() || '#eb6834';
  const borderColor = cs.getPropertyValue('--border').trim()     || '#EBEBEB';
  const textColor    = cs.getPropertyValue('--muted').trim()      || '#9EA3B0';
  const isDark = document.body.classList.contains('dark');
  // 가격선 축 라벨(상한/매도/현재/매수/하한)이 기본적으로 line color를 그대로
  // 불투명 배경으로 채워서 다크모드 네온 톤에서 꽉 막힌 사각형처럼 보였음
  // (2026-08-27 사용자 리포트 "차트내 텍스트박스도 투명도 조치해줘") —
  // 다크모드에서만 axisLabelColor를 반투명으로 분리해 라인 자체 색은 유지.
  const _alphaColor = (c, a) => {
    c = (c || '').trim();
    if (c.startsWith('rgba')) return c.replace(/[\d.]+\)\s*$/, a + ')');
    if (c.startsWith('rgb('))  return c.replace('rgb(', 'rgba(').replace(/\)\s*$/, `,${a})`);
    if (c.startsWith('#')) {
      let h = c.slice(1);
      if (h.length === 3) h = h.split('').map(x => x + x).join('');
      const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
      return `rgba(${r},${g},${b},${a})`;
    }
    return c;
  };
  const _labelStyle = (color) => isDark
    ? { axisLabelColor: _alphaColor(color, .55), axisLabelTextColor: '#fff' }
    : {};

  // lightweight-charts는 숫자 타임스탬프를 기본적으로 UTC 기준으로 축에
  // 표시한다(브라우저 로컬시간이 아님) — 그래서 KST 13:40 캔들이 "04:40"으로
  // 보여 "새벽 시간대가 나온다"는 문제가 생겼음(실측: 값 자체는 정확한 KST
  // 절대시각의 UTC epoch였고, 표시 포맷팅만 UTC였음). tickMarkFormatter/
  // timeFormatter로 Asia/Seoul 기준 표기를 강제한다. 일/주/월봉은 서버가
  // 이미 'YYYY-MM-DD' 캘린더 날짜 문자열로 주므로(시간대 변환 불필요) 분기.
  const _kstFmt = (opts) => (time) => new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', ...opts }).format(new Date(time * 1000));
  const _kstTimeLabel = _kstFmt({ hour: '2-digit', minute: '2-digit', hour12: false });
  const _kstDayLabel  = _kstFmt({ day: 'numeric' });
  const _dateStrParts = (s) => { const [y, mo, d] = s.split('-'); return { y, mo: Number(mo), d: Number(d) }; };

  const chart = LightweightCharts.createChart(chartEl, {
    layout: { background: { type: LightweightCharts.ColorType.Solid, color: 'transparent' }, textColor },
    grid: { vertLines: { color: borderColor }, horzLines: { color: borderColor } },
    timeScale: {
      borderColor, timeVisible: true, secondsVisible: false,
      // 상한/매도/현재/매수/하한 가격선 라벨이 우측 축에 붙어서 그려지는데,
      // 값들이 서로 가까우면(특히 매수=하한처럼 겹치는 경우) 라벨이 쌓이면서
      // 폭이 넓어져 최근 캔들(맨 오른쪽)을 그대로 가려버림(2026-08-26 사용자
      // 리포트). rightOffset으로 마지막 캔들 뒤에 빈 공간을 둬서 라벨이 그
      // 여백 위에만 걸치고 실제 캔들은 안 가리게 함.
      rightOffset: 8,
      tickMarkFormatter: (time, tickMarkType) => {
        if (typeof time === 'string') {
          const { y, mo, d } = _dateStrParts(time);
          return tickMarkType === LightweightCharts.TickMarkType.Year ? y : `${mo}/${d}`;
        }
        const isDayMark = tickMarkType === LightweightCharts.TickMarkType.DayOfMonth
          || tickMarkType === LightweightCharts.TickMarkType.Month
          || tickMarkType === LightweightCharts.TickMarkType.Year;
        return (isDayMark ? _kstDayLabel : _kstTimeLabel)(time);
      },
    },
    localization: {
      timeFormatter: (time) => {
        if (typeof time === 'string') { const { y, mo, d } = _dateStrParts(time); return `${y}.${mo}.${d}`; }
        return `${_kstDayLabel(time)}일 ${_kstTimeLabel(time)}`;
      },
    },
    rightPriceScale: { borderColor },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    width: chartEl.clientWidth,
    height: 220,
  });
  _gridChartInstances[containerId] = chart;

  const series = chart.addCandlestickSeries({
    upColor: '#16a34a', downColor: '#dc2626',
    borderUpColor: '#16a34a', borderDownColor: '#dc2626',
    wickUpColor: '#16a34a', wickDownColor: '#dc2626',
    priceLineVisible: false,  // 현재가는 아래에서 별도 라인으로 직접 표시(더 실시간)
    lastValueVisible: false,  // 캔들 라이브러리 기본 "마지막 종가" 배지 — 10분봉 마감가라
                               // 실시간 폴링 현재가("현재" 점선)와 다른 값이 동시에 떠서
                               // 혼동을 줌(사용자 실측: "빨간바탕 흰색 2013이 뭐냐" 질문).
                               // 실시간 현재가는 이미 아래 "현재" 라인이 대신하므로 끈다.
    priceFormat: { type: 'price', precision: 0, minMove: 1 },  // 원화는 소수점 단위가
                               // 없는데 라이브러리 기본값이 소수 둘째자리(.00)까지
                               // 표시해서 축·툴팁이 지저분했음 — 정수 단위로 고정.
  });
  if (candles.length) series.setData(candles);

  const fillMarkers = _buildFillMarkers(job, candles, buyColor, sellColor);
  if (fillMarkers.length) series.setMarkers(fillMarkers);

  // 매수/매도 대기 기준가 — 실제 캔들 위에 바로 겹쳐서 "지금 가격 흐름 대비
  // 내 주문이 어디 걸려있는지"가 한눈에 보이도록. 금액은 매수대기는 격자당
  // 금액(job.krw_per_grid)으로 항상 동일하고, 매도대기는 레벨마다 달라서
  // 표시 여부가 갈렸었는데 — 통일성을 위해 둘 다 라벨 없이 가격선만 표시
  // (금액은 위 정보줄의 "격자당 X원"으로 충분).
  const levels = _gridLevelsNormalize(job, qtyField);
  for (const l of levels) {
    if (l.state === 'idle') continue;
    const isBuy = l.state === 'buy_waiting';
    series.createPriceLine({
      price: l.price,
      color: isBuy ? buyColor : sellColor,
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: isBuy ? '매수' : '매도',
      ..._labelStyle(isBuy ? buyColor : sellColor),
    });
  }

  // 설정 범위 상/하한 — 옅은 점선 가이드
  const lower = Number(job.lower_price) || 0, upper = Number(job.upper_price) || 0;
  series.createPriceLine({ price: lower, color: borderColor, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '하한', ..._labelStyle(borderColor) });
  series.createPriceLine({ price: upper, color: borderColor, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '상한', ..._labelStyle(borderColor) });

  // 현재가 — 중립 잉크 + 점선(카테고리 색과 겹치지 않도록, SVG 래더 때와 동일 원칙).
  // 캔들의 마지막 종가보다 최신일 수 있어(현재가 폴링이 10분봉보다 촘촘함)
  // series 기본 lastValue 라인 대신 이걸 켠다.
  if (curPrice != null && isFinite(curPrice)) {
    const isEscaped = curPrice < lower || curPrice > upper;
    series.createPriceLine({
      price: curPrice,
      color: textColor,
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: `현재${isEscaped ? ' ⚠️' : ''}`,
      ..._labelStyle(textColor),
    });
  }

  // createPriceLine으로 얹은 값들은 기본적으로 세로축 자동 스케일 계산에
  // 포함되지 않는다 — 그래서 예전엔 범위 이탈 시 현재가가 캔들 범위 밖으로
  // 잘려 안 보이는 버그가 있었음(헤드리스 크롬 스크린샷으로 실측 후 발견).
  // 그때 하한/상한까지 항상 강제 포함시켜 고쳤는데, 이번엔 반대로 캔들이
  // 실제론 좁은 폭에서만 움직여도 축이 늘 그리드 전체 범위(수만원 폭)만큼
  // 넓게 늘어나 캔들이 위아래로 눌려 보이는 문제가 생김(2026-08-28 사용자
  // 리포트 — "위아래 간격이 너무 좁아서 확인이 어렵다"). 하한/상한은 캔들
  // 데이터가 아예 없을 때의 폴백으로만 쓰고, 평소엔 현재가·활성 격자가만
  // 강제 포함해서 축이 캔들 움직임에 맞춰 유연하게 좁아지도록 한다.
  const activeLevelPrices = levels.filter(l => l.state !== 'idle').map(l => l.price);
  const refPrices = [...activeLevelPrices];
  if (curPrice != null && isFinite(curPrice)) refPrices.push(curPrice);
  series.applyOptions({
    autoscaleInfoProvider: original => {
      let res = original();
      if (res && res.priceRange) {
        res.priceRange.minValue = Math.min(res.priceRange.minValue, ...refPrices);
        res.priceRange.maxValue = Math.max(res.priceRange.maxValue, ...refPrices);
      } else {
        // 캔들 데이터가 아예 없으면(로딩 실패 등) 하한/상한까지 포함해 범위 구성
        res = { priceRange: { minValue: Math.min(lower, ...refPrices), maxValue: Math.max(upper, ...refPrices) } };
      }
      return res;
    },
  });

  chart.timeScale().fitContent();
}

