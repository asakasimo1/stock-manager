
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

// ══════════════════════════════════════════════════════════
// 그리드 매매 현황 — 가격 래더 차트 (코인/주식 그리드 탭 공유)
// ══════════════════════════════════════════════════════════
// 색상은 dataviz 가이드로 검증됨 — 매수(앱 --primary #3D5AFE)/매도(신규
// --state-sell, 라이트#eb6834·다크#d95926)는 CVD ΔE 31.8/26.8·일반시각
// 40.0/31.8(모두 8/15 기준 통과), --border(idle)는 무채색이라 대상 아님.
// 현재가 기준선은 카테고리 색과 겹치는 걸 피하려고 일부러 색상 대신
// 텍스트 잉크+점선으로 표현(참조선은 "계열"이 아니라 구조적 주석이라
// 색상 채널을 새로 하나 더 쓰지 않는 게 맞음 — violet(#8b5cf6)을 넣어
// 봤더니 --primary와 일반시각 ΔE 11.1로 실패해서 폐기).
const GRID_LADDER_MIN_ROW_GAP = 20;   // 라벨 세로 최소 간격(px, 겹침 방지)
const GRID_LADDER_ROW_H       = 24;   // 격자 1개당 배정 높이(px)

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

// 라벨 겹침 방지: 현재가 라인을 고정 기준점 삼아 위쪽 라벨은 위로, 아래쪽
// 라벨은 아래로 밀어내며 최소 간격을 보장(leader line으로 실제 tick과 연결).
// 현재가가 없으면(curY=null) 단순 위→아래 단조 push만 적용.
// 매도대기(현재가보다 위)와 매수대기(현재가보다 아래)가 현재가 라인 바로
// 옆에 몰릴 때, 라벨 텍스트가 점선 위에 겹쳐 찍히던 버그의 수정.
function _gridLadderLayoutLabels(items, yOf, curY) {
  const withY = items.map(it => ({ ...it, trueY: yOf(it.price) }));

  if (curY == null) {
    const sorted = [...withY].sort((a, b) => a.trueY - b.trueY);
    let prevY = -Infinity;
    return sorted.map(it => {
      const labelY = Math.max(it.trueY, prevY + GRID_LADDER_MIN_ROW_GAP);
      prevY = labelY;
      return { ...it, labelY };
    });
  }

  const above = withY.filter(it => it.trueY <= curY).sort((a, b) => b.trueY - a.trueY); // curY에 가까운 것부터
  const below = withY.filter(it => it.trueY >  curY).sort((a, b) => a.trueY - b.trueY);

  const laidOutAbove = [];
  let ceiling = curY - GRID_LADDER_MIN_ROW_GAP;
  for (const it of above) {
    const labelY = Math.min(it.trueY, ceiling);
    laidOutAbove.push({ ...it, labelY });
    ceiling = labelY - GRID_LADDER_MIN_ROW_GAP;
  }

  const laidOutBelow = [];
  let floor = curY + GRID_LADDER_MIN_ROW_GAP;
  for (const it of below) {
    const labelY = Math.max(it.trueY, floor);
    laidOutBelow.push({ ...it, labelY });
    floor = labelY + GRID_LADDER_MIN_ROW_GAP;
  }

  return [...laidOutAbove.reverse(), ...laidOutBelow];
}

function _escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

// job 하나의 가격 래더 SVG 차트 HTML을 반환.
// curPrice: 현재가(숫자, 없으면 기준선 생략) / qtyField: 'coin_qty' | 'qty'
function renderGridLadderChart(job, curPrice, qtyField) {
  const levels = _gridLevelsNormalize(job, qtyField);
  if (!levels.length) return '';

  const lower = Number(job.lower_price) || 0;
  const upper = Number(job.upper_price) || 0;
  const active = levels.filter(l => l.state !== 'idle');

  // 가격 범위: 설정 범위 + 모든 격자 + 현재가(범위 이탈 시 차트가 자동으로
  // 늘어나 이탈 상태가 시각적으로도 드러남) 를 전부 포함하도록 확장.
  const allPrices = [lower, upper, ...levels.map(l => l.price)];
  if (curPrice != null && isFinite(curPrice)) allPrices.push(curPrice);
  let minP = Math.min(...allPrices);
  let maxP = Math.max(...allPrices);
  const span = maxP - minP;
  const pad = span > 0 ? span * 0.08 : Math.max(1, minP * 0.02);
  minP -= pad; maxP += pad;

  const W = 420;
  const topPad = 16, botPad = 16;
  const H = Math.max(220, Math.min(480, levels.length * GRID_LADDER_ROW_H + topPad + botPad));
  const plotH = H - topPad - botPad;
  const yOf = p => topPad + plotH - ((p - minP) / (maxP - minP || 1)) * plotH;

  const axisX  = 8;     // 세로 축 라인
  const tickX2 = 26;    // idle/기본 tick 끝
  const labelX = 34;    // 라벨 시작 x

  const hasCur = curPrice != null && isFinite(curPrice);
  const curY   = hasCur ? yOf(curPrice) : null;
  const laidOut = _gridLadderLayoutLabels(active, yOf, curY);

  // idle: 짧고 옅은 tick만
  const idleTicks = levels.filter(l => l.state === 'idle').map(l => {
    const y = yOf(l.price);
    return `<line x1="${axisX}" y1="${y}" x2="${tickX2}" y2="${y}"
      stroke="var(--border)" stroke-width="2" stroke-linecap="round"><title>대기중 · ${l.price.toLocaleString()}원</title></line>`;
  }).join('');

  // 매수/매도 대기: 굵은 tick + 리더라인(라벨이 밀렸을 때만) + 라벨
  const activeMarks = laidOut.map(l => {
    const color = l.state === 'buy_waiting' ? 'var(--primary)' : 'var(--state-sell)';
    const label = l.state === 'buy_waiting' ? '매수대기' : '매도대기';
    const priceStr  = Math.round(l.price).toLocaleString() + '원';
    const amountStr = Math.round(l.amount).toLocaleString() + '원';
    const needsLeader = Math.abs(l.labelY - l.trueY) > 1;
    return `
      <g>
        <line x1="${axisX}" y1="${l.trueY}" x2="${tickX2}" y2="${l.trueY}"
          stroke="${color}" stroke-width="3" stroke-linecap="round"></line>
        <circle cx="${tickX2}" cy="${l.trueY}" r="4" fill="${color}"
          stroke="var(--surface)" stroke-width="2"></circle>
        ${needsLeader ? `<line x1="${tickX2}" y1="${l.trueY}" x2="${labelX - 4}" y2="${l.labelY}"
          stroke="${color}" stroke-width="1" stroke-dasharray="1.5,2" opacity="0.55"></line>` : ''}
        <text x="${labelX}" y="${l.labelY + 4}" font-size="12" font-weight="700" fill="${color}">${_escapeHtml(label)}</text>
        <text x="${labelX + 62}" y="${l.labelY + 4}" font-size="12" fill="var(--text)">${_escapeHtml(priceStr)}</text>
        <text x="${labelX + 132}" y="${l.labelY + 4}" font-size="11" fill="var(--muted)">(${_escapeHtml(amountStr)})</text>
        <rect x="0" y="${l.labelY - 10}" width="${W}" height="20" fill="transparent">
          <title>${_escapeHtml(label)} · ${_escapeHtml(priceStr)} · ${_escapeHtml(amountStr)}</title>
        </rect>
      </g>`;
  }).join('');

  // 설정 범위(상한/하한) 경계선 — 라인은 항상 표시. 텍스트는 활성 라벨/현재가와
  // 같은 줄에 겹칠 만큼 가까우면 생략(라인만으로 위치는 여전히 보임, 중복
  // 텍스트로 붐비는 걸 방지) — 흔한 경우: 하한가에 매수대기 격자가 걸려있음.
  const occupiedYs = laidOut.map(l => l.labelY).concat(hasCur ? [curY] : []);
  const nearOccupied = y => occupiedYs.some(oy => Math.abs(oy - y) < GRID_LADDER_MIN_ROW_GAP);
  const lowerY = yOf(lower), upperY = yOf(upper);
  const rangeGuides = `
    <line x1="${axisX}" y1="${lowerY}" x2="${W}" y2="${lowerY}" stroke="var(--border)" stroke-width="1"></line>
    <line x1="${axisX}" y1="${upperY}" x2="${W}" y2="${upperY}" stroke="var(--border)" stroke-width="1"></line>
    ${nearOccupied(lowerY) ? '' : `<text x="${W}" y="${lowerY - 4}" font-size="10" fill="var(--muted)" text-anchor="end">하한 ${lower.toLocaleString()}원</text>`}
    ${nearOccupied(upperY) ? '' : `<text x="${W}" y="${upperY + 12}" font-size="10" fill="var(--muted)" text-anchor="end">상한 ${upper.toLocaleString()}원</text>`}`;

  // 현재가 기준선 — 중립 잉크 + 점선 (카테고리 색과 절대 겹치지 않도록).
  // 라인 자체는 항상 정확한 가격 위치(curY)에 그림 — 라벨 레이아웃 시스템이
  // curY를 고정 장애물로 취급해 근처 활성 라벨을 밀어내므로, 점선이 텍스트
  // 위에 겹쳐 찍히는 일이 없다.
  let curLine = '';
  if (hasCur) {
    const isEscaped = curPrice < lower || curPrice > upper;
    curLine = `
      <line x1="${axisX}" y1="${curY}" x2="${W}" y2="${curY}"
        stroke="var(--text)" stroke-width="2" stroke-dasharray="5,3"></line>
      <rect x="${W - 96}" y="${curY - 10}" width="96" height="20" rx="5"
        fill="var(--surface)" stroke="var(--text)" stroke-width="1.2"></rect>
      <text x="${W - 48}" y="${curY + 4}" font-size="11" font-weight="700" fill="var(--text)" text-anchor="middle">
        현재 ${Math.round(curPrice).toLocaleString()}원${isEscaped ? ' ⚠️' : ''}
      </text>`;
  }

  return `
    <div style="margin-bottom:8px">
      <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" style="display:block" role="img"
        aria-label="그리드 매매 현황 가격 래더 차트">
        <line x1="${axisX}" y1="${topPad}" x2="${axisX}" y2="${H - botPad}" stroke="var(--border)" stroke-width="1.5"></line>
        ${rangeGuides}
        ${idleTicks}
        ${activeMarks}
        ${curLine}
      </svg>
      <div style="display:flex;gap:14px;align-items:center;font-size:10px;color:var(--muted);margin-top:2px;padding-left:8px">
        <span style="display:inline-flex;align-items:center;gap:4px"><span style="width:10px;height:3px;background:var(--primary);border-radius:2px;display:inline-block"></span>매수대기</span>
        <span style="display:inline-flex;align-items:center;gap:4px"><span style="width:10px;height:3px;background:var(--state-sell);border-radius:2px;display:inline-block"></span>매도대기</span>
        <span style="display:inline-flex;align-items:center;gap:4px"><span style="width:10px;height:3px;background:var(--border);border-radius:2px;display:inline-block"></span>대기중</span>
        <span style="display:inline-flex;align-items:center;gap:4px"><span style="width:10px;height:0;border-top:2px dashed var(--text);display:inline-block"></span>현재가</span>
      </div>
    </div>`;
}

