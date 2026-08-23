
// ══════════════════════════════════════════════════════════
// 설정
// ══════════════════════════════════════════════════════════
const SETTINGS_KEY = 'app-settings-v1';
const TAB_LABELS = {
  dashboard:'📊 대시보드', portfolio:'📈 포트폴리오', market:'📊 시장현황',
  etf:'💹 ETF', stocks:'📊 개별주', autotrade:'🤖 자동매매', cointrade:'🪙 자동코인매매', scalp:'⚡ 초단타',
};
let _settings = { hiddenTabs:[], darkMode:false, defaultTab:'dashboard', autoRefreshSec:0 };
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

