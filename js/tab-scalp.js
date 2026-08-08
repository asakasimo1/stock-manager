// ══════════════════════════════════════════════════════════
// 초단타(스캘핑) 자동매매 탭 — 코인/주식 공용
// API: /api/scalp-coin, /api/scalp-stock, /api/scalp-control (전체 킬스위치)
// ══════════════════════════════════════════════════════════

let _scRefreshTimer = null;
let _scPriceTimer = null;
let _scAcTimer = null;
let _scControl = { coin_enabled: true, stock_enabled: true };
let _scJobs = { coin: [], stock: [] };
let _scAutoConfig = { coin: {}, stock: {} };
let _scAutoMarket = 'coin';
let _scLivePrices = {};   // ticker -> { price, chgPct }

function _scOnTab() {
  return document.querySelector('.tab-btn.active')?.getAttribute('onclick')?.includes('scalp');
}

async function initScalp() {
  await scLoadControl();
  await scLoadAutoConfig();
  await scLoadJobs();
  clearInterval(_scRefreshTimer);
  _scRefreshTimer = setInterval(() => {
    if (_scOnTab()) scLoadJobs(); else clearInterval(_scRefreshTimer);
  }, 10000);

  // 보유중 포지션의 현재가·평가손익은 더 짧은 주기로 갱신 (체결 즉시 확인 가능하도록)
  clearInterval(_scPriceTimer);
  _scPriceTimer = setInterval(() => {
    if (_scOnTab()) scRefreshLivePrices(); else clearInterval(_scPriceTimer);
  }, 5000);
}

// ── 전체 정지 킬스위치 ────────────────────────────────────
async function scLoadControl() {
  try {
    const r = await fetch('/api/scalp-control');
    _scControl = await r.json();
  } catch (_) {}
  scRenderControl();
}

function scRenderControl() {
  const el = document.getElementById('sc-control');
  if (!el) return;
  const mk = (key, label) => {
    const on = _scControl[key] !== false;
    return `<button onclick="scToggleControl('${key}', ${!on})"
      style="flex:1;padding:12px;border-radius:10px;border:2px solid ${on ? '#16a34a' : '#dc2626'};
             background:${on ? '#16a34a18' : '#dc262618'};color:${on ? '#16a34a' : '#dc2626'};
             font-weight:700;font-size:13px;cursor:pointer">
      ${on ? '🟢 실행 허용' : '🛑 정지됨'} · ${label}
    </button>`;
  };
  el.innerHTML = `<div style="display:flex;gap:10px">${mk('coin_enabled', '코인')}${mk('stock_enabled', '주식')}</div>
    <div style="font-size:11px;color:var(--muted);margin-top:6px">정지 시 신규 진입 중단 + 보유 중인 포지션은 즉시 시장가로 청산됩니다</div>`;
}

async function scToggleControl(key, value) {
  const label = key === 'coin_enabled' ? '코인' : '주식';
  if (!value && !confirm(`${label} 스캘핑을 정지할까요?\n보유 중인 포지션은 즉시 시장가로 청산됩니다.`)) return;
  try {
    const r = await fetch('/api/scalp-control', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }),
    });
    _scControl = await r.json();
  } catch (e) { alert('저장 실패: ' + e.message); }
  scRenderControl();
}

// ── 자동 종목 발굴 설정 ───────────────────────────────────
async function scLoadAutoConfig() {
  try {
    const r = await fetch('/api/scalp-auto-config');
    _scAutoConfig = await r.json();
  } catch (_) {}
  scRenderAutoConfig();
}

function scAutoMarketChange(market) {
  _scAutoMarket = market;
  scRenderAutoConfig();
}

function scRenderAutoConfig() {
  const el = document.getElementById('sc-auto-config');
  if (!el) return;
  const cfg = _scAutoConfig[_scAutoMarket] || {};
  const on = !!cfg.enabled;
  const sizeLabel = _scAutoMarket === 'coin' ? '1회 매수 금액 (원)' : '1회 매수 금액 (원)';
  const sizeVal = _scAutoMarket === 'coin' ? (cfg.krw_amount ?? 10000) : (cfg.amount ?? 500000);
  const sizeId = _scAutoMarket === 'coin' ? 'sac-krw-amount' : 'sac-amount';

  el.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <button onclick="scAutoMarketChange('coin')" style="flex:1;padding:8px;border-radius:8px;cursor:pointer;
        border:1.5px solid ${_scAutoMarket === 'coin' ? '#7c3aed' : 'var(--border)'};
        background:${_scAutoMarket === 'coin' ? '#7c3aed18' : 'var(--bg)'};color:var(--text);font-size:12px;font-weight:600">🪙 코인</button>
      <button onclick="scAutoMarketChange('stock')" style="flex:1;padding:8px;border-radius:8px;cursor:pointer;
        border:1.5px solid ${_scAutoMarket === 'stock' ? '#7c3aed' : 'var(--border)'};
        background:${_scAutoMarket === 'stock' ? '#7c3aed18' : 'var(--bg)'};color:var(--text);font-size:12px;font-weight:600">📈 주식</button>
    </div>

    <label style="display:flex;align-items:center;gap:8px;margin-bottom:12px;cursor:pointer">
      <input id="sac-enabled" type="checkbox" ${on ? 'checked' : ''} style="width:16px;height:16px" />
      <span style="font-size:13px;font-weight:600">자동 포착 활성화 — 켜면 데몬이 직접 급등 종목을 스캔해서 진입합니다</span>
    </label>

    <div style="background:#7c3aed0d;border:1px solid #7c3aed33;border-radius:10px;padding:12px;margin-bottom:14px">
      <label style="display:flex;align-items:center;gap:8px;margin-bottom:8px;cursor:pointer">
        <input id="sac-surge-enabled" type="checkbox" ${(cfg.surge_enabled ?? true) ? 'checked' : ''} style="width:15px;height:15px" />
        <span style="font-size:12px;font-weight:700;color:#7c3aed">🔍 급등 포착 — 아래 두 조건을 동시에 만족해야 후보로 인정</span>
      </label>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">① 급등 판단 구간 (초)</div>
          <input id="sac-discovery-momentum-sec" type="number" min="10" step="10" value="${cfg.discovery_momentum_sec ?? 60}"
            style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
        </div>
        <div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">① 급등 판단 상승률 (%)</div>
          <input id="sac-min-discovery-momentum" type="number" min="0.1" step="0.1" value="${cfg.min_discovery_momentum_pct ?? 0.4}"
            style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
        </div>
        <div style="grid-column:1 / -1">
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">② 거래량 증가 배수 (평소 대비 배)</div>
          <input id="sac-min-volume-surge" type="number" min="1" step="0.1" value="${cfg.min_volume_surge_ratio ?? 1.3}"
            style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
        </div>
      </div>
      <div style="font-size:11px;color:var(--muted);margin-top:8px">① 최근 N초간 이만큼 올랐는지 · ② 최근 거래량이 평소(최근 2분 평균)보다 몇 배 늘었는지 — 두 조건을 모두 만족하는 종목만 후보로 잡습니다</div>
    </div>

    <div style="background:#f59e0b0d;border:1px solid #f59e0b33;border-radius:10px;padding:12px;margin-bottom:14px">
      <label style="display:flex;align-items:center;gap:8px;margin-bottom:8px;cursor:pointer">
        <input id="sac-reversal-enabled" type="checkbox" ${cfg.reversal_enabled ? 'checked' : ''} style="width:15px;height:15px" />
        <span style="font-size:12px;font-weight:700;color:#f59e0b">📉 급락후반등 포착 — 빠르게 급락하다가 급 양전하는 대상</span>
      </label>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">① 하락 판단 구간 (초)</div>
          <input id="sac-decline-lookback" type="number" min="30" step="30" value="${cfg.decline_lookback_sec ?? 300}"
            style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
        </div>
        <div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">① 하락 판단 하락폭 (%)</div>
          <input id="sac-min-decline" type="number" min="0.1" step="0.1" value="${cfg.min_decline_pct ?? 2.0}"
            style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
        </div>
        <div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">② 반등 판단 구간 (초)</div>
          <input id="sac-rebound-lookback" type="number" min="10" step="10" value="${cfg.rebound_lookback_sec ?? 30}"
            style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
        </div>
        <div>
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">② 반등 판단 상승률 (%)</div>
          <input id="sac-min-rebound" type="number" min="0.1" step="0.1" value="${cfg.min_rebound_pct ?? 0.4}"
            style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
        </div>
      </div>
      <div style="font-size:11px;color:var(--muted);margin-top:8px">① 최근 N초간 이만큼 하락했는지 · ② 그 이후 최근 M초간 이만큼 반등했는지 — 둘 다 만족하는 종목만 후보로 잡습니다 (급등 포착과 별개로 독립 작동)</div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px">
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">${sizeLabel}</div>
        <input id="${sizeId}" type="number" min="1000" step="1000" value="${sizeVal}"
          style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
      </div>
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">동시 포착 개수</div>
        <input id="sac-max-concurrent" type="number" min="1" max="3" step="1" value="${cfg.max_concurrent ?? 2}"
          style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
      </div>
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">진입 모멘텀 (%)</div>
        <input id="sac-entry-momentum" type="number" min="0.1" step="0.1" value="${cfg.entry_momentum_pct ?? 0.4}"
          style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
      </div>
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">관측 구간 (초)</div>
        <input id="sac-lookback" type="number" min="10" step="5" value="${cfg.lookback_sec ?? 30}"
          style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
      </div>
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">익절 기준 (%)</div>
        <input id="sac-take-profit" type="number" min="0.1" step="0.1" value="${cfg.take_profit_pct ?? 0.6}"
          style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
      </div>
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">손절 기준 (%)</div>
        <input id="sac-stop-loss" type="number" min="0.1" step="0.1" value="${cfg.stop_loss_pct ?? 0.4}"
          style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
      </div>
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">시간초과 판단 시점 (초)</div>
        <input id="sac-time-stop" type="number" min="30" step="10" value="${cfg.time_stop_sec ?? 180}"
          style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
      </div>
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">시간초과 손절 기준 (%)</div>
        <input id="sac-time-stop-loss" type="number" min="0.1" step="0.1" value="${cfg.time_stop_loss_pct ?? 0.5}"
          style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
      </div>
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">당일 과열 컷 (%)</div>
        <input id="sac-max-day-chg" type="number" min="1" step="0.5" value="${cfg.max_day_chg_pct ?? 5.0}"
          style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
      </div>
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">watching 포기 시간 (초)</div>
        <input id="sac-watch-timeout" type="number" min="60" step="30" value="${cfg.watch_timeout_sec ?? 300}"
          style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
      </div>
    </div>
    <div style="font-size:11px;color:var(--muted);margin:-4px 0 4px">watching 포기 시간: 이 시간 안에 진입 조건을 못 채우면 포기하고 슬롯을 반환합니다 — 안 그러면 조용한 종목이 포착 슬롯을 계속 차지해 새 후보를 못 찾습니다</div>
    <div style="font-size:11px;color:var(--muted);margin:0 0 10px">시간초과 손절 기준: 시간초과 판단 시점에 손실이 이 값을 넘을 때만 청산합니다 — 넘지 않으면(수익권이거나 손실이 작으면) 무조건 정리하지 않고 계속 지켜봅니다</div>
    <div style="margin-bottom:10px">
      <div style="font-size:11px;color:var(--muted);margin-bottom:4px">최소 유동성 — 이 이하로 거래대금이 적은 종목은 슬리피지 우려로 후보에서 제외 (원)</div>
      <input id="sac-min-liquidity" type="number" min="0" step="1000000"
        value="${cfg.min_liquidity ?? (_scAutoMarket === 'coin' ? 50000000 : 100000000)}"
        style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
    </div>
    <div style="margin-bottom:14px">
      <div style="font-size:11px;color:var(--muted);margin-bottom:4px">자동발굴 일일 손실 한도 (원) — 도달 시 그날은 신규 발굴 중단</div>
      <input id="sac-daily-loss" type="number" min="0" step="1000" value="${Math.abs(cfg.max_daily_loss_krw ?? 30000)}"
        style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font-size:13px" />
    </div>
    <button onclick="scSaveAutoConfig()"
      style="width:100%;background:#7c3aed;color:#fff;border:none;border-radius:10px;padding:12px;font-size:14px;font-weight:700;cursor:pointer">
      ${_scAutoMarket === 'coin' ? '🪙 코인' : '📈 주식'} 자동 포착 설정 저장
    </button>`;
}

async function scSaveAutoConfig() {
  const market = _scAutoMarket;
  const sizeId = market === 'coin' ? 'sac-krw-amount' : 'sac-amount';
  const sizeKey = market === 'coin' ? 'krw_amount' : 'amount';
  const body = {
    market,
    enabled: document.getElementById('sac-enabled').checked,
    [sizeKey]: parseFloat(document.getElementById(sizeId).value) || 0,
    max_concurrent: parseInt(document.getElementById('sac-max-concurrent').value, 10) || 1,
    entry_momentum_pct: parseFloat(document.getElementById('sac-entry-momentum').value) || 0.4,
    lookback_sec: parseInt(document.getElementById('sac-lookback').value, 10) || 30,
    take_profit_pct: parseFloat(document.getElementById('sac-take-profit').value) || 0.6,
    stop_loss_pct: parseFloat(document.getElementById('sac-stop-loss').value) || 0.4,
    time_stop_sec: parseInt(document.getElementById('sac-time-stop').value, 10) || 180,
    time_stop_loss_pct: parseFloat(document.getElementById('sac-time-stop-loss').value) || 0.5,
    max_day_chg_pct: parseFloat(document.getElementById('sac-max-day-chg').value) || 5.0,
    watch_timeout_sec: parseInt(document.getElementById('sac-watch-timeout').value, 10) || 300,
    surge_enabled: document.getElementById('sac-surge-enabled').checked,
    discovery_momentum_sec: parseInt(document.getElementById('sac-discovery-momentum-sec').value, 10) || 60,
    min_discovery_momentum_pct: parseFloat(document.getElementById('sac-min-discovery-momentum').value) || 0.4,
    min_volume_surge_ratio: parseFloat(document.getElementById('sac-min-volume-surge').value) || 1.3,
    reversal_enabled: document.getElementById('sac-reversal-enabled').checked,
    decline_lookback_sec: parseInt(document.getElementById('sac-decline-lookback').value, 10) || 300,
    min_decline_pct: parseFloat(document.getElementById('sac-min-decline').value) || 2.0,
    rebound_lookback_sec: parseInt(document.getElementById('sac-rebound-lookback').value, 10) || 30,
    min_rebound_pct: parseFloat(document.getElementById('sac-min-rebound').value) || 0.4,
    min_liquidity: parseFloat(document.getElementById('sac-min-liquidity').value) || 0,
    max_daily_loss_krw: -Math.abs(parseFloat(document.getElementById('sac-daily-loss').value) || 30000),
  };
  try {
    const r = await fetch('/api/scalp-auto-config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); alert('저장 실패: ' + (e.error || r.status)); return; }
    _scAutoConfig = await r.json();
    scRenderAutoConfig();
    alert('저장되었습니다');
  } catch (e) { alert('저장 실패: ' + e.message); }
}

// ── 잡 등록 폼 ────────────────────────────────────────────
function scMarketChange() {
  const m = document.querySelector('input[name="sc-market"]:checked')?.value;
  document.getElementById('sc-coin-fields').style.display  = m === 'coin'  ? 'block' : 'none';
  document.getElementById('sc-stock-fields').style.display = m === 'stock' ? 'block' : 'none';
  document.getElementById('sc-name').value = '';
  document.getElementById('sc-ticker').value = '';
}

function onScNameInput(v) {
  const m = document.querySelector('input[name="sc-market"]:checked')?.value;
  if (m === 'coin') {
    _renderAcList('sc-ac-list', _filterCoins(v), (c) => {
      document.getElementById('sc-name').value = c.name;
      document.getElementById('sc-ticker').value = c.ticker;
      document.getElementById('sc-ac-list').style.display = 'none';
    });
    return;
  }
  clearTimeout(_scAcTimer);
  if (!v.trim()) { hideScAc(); return; }
  _scAcTimer = setTimeout(async () => {
    try {
      const r = await fetch(`/api/stock?q=${encodeURIComponent(v.trim())}`);
      const d = await r.json();
      const items = (d.items || []).map(it => ({ name: it.name, ticker: it.ticker, symbol: it.market || '' }));
      _renderAcList('sc-ac-list', items, (c) => {
        document.getElementById('sc-name').value = c.name;
        document.getElementById('sc-ticker').value = c.ticker;
        document.getElementById('sc-ac-list').style.display = 'none';
      });
    } catch (_) {}
  }, 220);
}

function hideScAc() {
  setTimeout(() => { const el = document.getElementById('sc-ac-list'); if (el) el.style.display = 'none'; }, 150);
}

async function scRegister() {
  const market = document.querySelector('input[name="sc-market"]:checked')?.value;
  const ticker = document.getElementById('sc-ticker').value.trim();
  const name   = document.getElementById('sc-name').value.trim();
  if (!ticker) { alert('종목을 검색해서 선택하세요'); return; }

  const body = {
    ticker, name,
    entry_momentum_pct: parseFloat(document.getElementById('sc-entry-momentum').value) || 0.4,
    lookback_sec:       parseInt(document.getElementById('sc-lookback').value, 10) || 30,
    max_day_chg_pct:    parseFloat(document.getElementById('sc-max-day-chg').value) || 5.0,
    take_profit_pct:    parseFloat(document.getElementById('sc-take-profit').value) || 0.6,
    stop_loss_pct:      parseFloat(document.getElementById('sc-stop-loss').value) || 0.4,
    time_stop_sec:      parseInt(document.getElementById('sc-time-stop').value, 10) || 180,
    time_stop_loss_pct: parseFloat(document.getElementById('sc-time-stop-loss').value) || 0.5,
    max_daily_loss_krw: -Math.abs(parseFloat(document.getElementById('sc-daily-loss').value) || 20000),
  };

  if (market === 'coin') {
    body.krw_amount = parseFloat(document.getElementById('sc-krw-amount').value) || 0;
    if (body.krw_amount < 5000) { alert('코인 매수 금액은 5,000원 이상이어야 합니다'); return; }
  } else {
    body.amount = parseFloat(document.getElementById('sc-stock-amount').value) || 0;
    if (body.amount < 1000) { alert('매수 금액을 입력하세요'); return; }
  }

  const url = market === 'coin' ? '/api/scalp-coin' : '/api/scalp-stock';
  try {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) { const e = await r.json().catch(() => ({})); alert('등록 실패: ' + (e.error || r.status)); return; }
    document.getElementById('sc-name').value = '';
    document.getElementById('sc-ticker').value = '';
    await scLoadJobs();
  } catch (e) { alert('등록 실패: ' + e.message); }
}

// ── 잡 목록 ───────────────────────────────────────────────
async function scLoadJobs() {
  try {
    const [rc, rs] = await Promise.all([fetch('/api/scalp-coin'), fetch('/api/scalp-stock')]);
    _scJobs.coin  = await rc.json();
    _scJobs.stock = await rs.json();
  } catch (_) {}
  scRenderJobs();
  scRenderSummary();
  scRefreshLivePrices();
}

// ── 보유중 포지션 실시간 현재가·평가손익 ──────────────────
async function scRefreshLivePrices() {
  const holdingCoin  = (_scJobs.coin  || []).filter(j => j.phase === 'holding');
  const holdingStock = (_scJobs.stock || []).filter(j => j.phase === 'holding');
  if (!holdingCoin.length && !holdingStock.length) return;

  const tasks = [];

  if (holdingCoin.length) {
    const markets = [...new Set(holdingCoin.map(j => j.ticker))].join(',');
    tasks.push(
      fetch(`/api/coin-price?markets=${encodeURIComponent(markets)}`)
        .then(r => r.json())
        .then(arr => {
          (arr || []).forEach(d => {
            _scLivePrices[d.market] = { price: d.trade_price, chgPct: (d.signed_change_rate || 0) * 100 };
          });
        })
        .catch(() => {})
    );
  }

  holdingStock.forEach(j => {
    tasks.push(
      fetch(`/api/quote?ticker=${encodeURIComponent(j.ticker)}`)
        .then(r => r.json())
        .then(d => {
          if (d.price) _scLivePrices[j.ticker] = { price: d.price, chgPct: d.chgPct };
        })
        .catch(() => {})
    );
  });

  await Promise.all(tasks);
  scRenderJobs();
}

function scRenderSummary() {
  const el = document.getElementById('sc-summary');
  if (!el) return;
  const all = [...(_scJobs.coin || []), ...(_scJobs.stock || [])];
  const pnl = all.reduce((s, j) => s + (j.realized_pnl_today || 0), 0);
  const trades = all.reduce((s, j) => s + (j.trades_today || 0), 0);
  const holding = all.filter(j => j.phase === 'holding').length;
  const color = pnl >= 0 ? '#16a34a' : '#dc2626';
  el.innerHTML = `<span style="font-size:12px;color:var(--muted)">오늘 실현손익</span>
    <span style="font-size:18px;font-weight:800;color:${color};margin-left:8px">${pnl >= 0 ? '+' : ''}${Math.round(pnl).toLocaleString()}원</span>
    <span style="font-size:12px;color:var(--muted);margin-left:10px">거래 ${trades}회</span>
    ${holding > 0 ? `<span style="font-size:12px;color:#16a34a;font-weight:700;margin-left:10px">● 현재 보유중 ${holding}건</span>` : ''}`;
}

function scRenderJobs() {
  const elActive  = document.getElementById('sc-active-list');
  const elHistory = document.getElementById('sc-history-list');
  if (!elActive || !elHistory) return;

  const all = [
    ...(_scJobs.coin  || []).map(j => ({ ...j, market: 'coin' })),
    ...(_scJobs.stock || []).map(j => ({ ...j, market: 'stock' })),
  ];

  const active = all.filter(j => j.status === 'active' || j.status === 'paused');

  // 보유중 → 실행중 → 일시정지 순 (지금 매매 중인 것이 가장 먼저 보이도록)
  active.sort((a, b) => (a.phase === 'holding' ? 0 : a.status === 'active' ? 1 : 2)
                      - (b.phase === 'holding' ? 0 : b.status === 'active' ? 1 : 2));

  elActive.innerHTML = active.length
    ? active.map(_scJobCardHtml).join('')
    : `<div style="color:var(--muted);font-size:13px;padding:20px;text-align:center">진행중인 스캘핑 잡이 없습니다</div>`;

  scRenderHistory(all);
}

// ── 초단타내역: 매수가/매도가/손익/시간 1줄 표시 (오늘 최대 5건 + 더보기, 지난 날짜는 일일 요약으로 집계) ──
let _scHistExpanded = false;

function scToggleHistoryMore() {
  _scHistExpanded = !_scHistExpanded;
  scRenderHistory([
    ...(_scJobs.coin  || []).map(j => ({ ...j, market: 'coin' })),
    ...(_scJobs.stock || []).map(j => ({ ...j, market: 'stock' })),
  ]);
}

function _scFlattenTrades(jobs) {
  const rows = [];
  jobs.forEach(j => (j.trade_log || []).forEach(t => rows.push({ ...t, ticker: j.ticker, name: j.name, market: j.market })));
  rows.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  return rows;
}

function _scTradeRowHtml(r) {
  const color = (r.pnl || 0) >= 0 ? '#16a34a' : '#dc2626';
  const marketIcon = r.market === 'coin' ? '🪙' : '📈';
  return `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 4px;border-top:1px solid var(--border);font-size:12px">
    <span style="color:var(--muted);flex:none">${r.time}</span>
    <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${marketIcon} ${r.name}</span>
    <span style="color:var(--muted);white-space:nowrap;flex:none">${Math.round(r.buy_price).toLocaleString()}→${Math.round(r.sell_price).toLocaleString()}원</span>
    <b style="color:${color};white-space:nowrap;flex:none">${(r.pnl || 0) >= 0 ? '+' : ''}${Math.round(r.pnl || 0).toLocaleString()}원</b>
  </div>`;
}

function scRenderHistory(all) {
  const elHistory = document.getElementById('sc-history-list');
  if (!elHistory) return;

  const rows  = _scFlattenTrades(all);
  const today = new Date(Date.now() + 9 * 3600000).toISOString().slice(0, 10);
  const todayRows = rows.filter(r => r.date === today);
  const pastRows  = rows.filter(r => r.date !== today && withinLastDays(r.date));

  // 지난 날짜는 날짜별로 묶어서 "거래 N종목 · 일일수익금 M원" 한 줄 요약으로
  const byDate = {};
  pastRows.forEach(r => {
    const d = byDate[r.date] || (byDate[r.date] = { tickers: new Set(), pnl: 0 });
    d.tickers.add(r.ticker);
    d.pnl += (r.pnl || 0);
  });
  const dateKeys = Object.keys(byDate).sort((a, b) => b.localeCompare(a));

  const visibleToday = _scHistExpanded ? todayRows : todayRows.slice(0, 5);
  const todayHtml = visibleToday.map(_scTradeRowHtml).join('');
  const moreHtml = todayRows.length > 5
    ? `<div onclick="scToggleHistoryMore()" style="text-align:center;padding:8px;font-size:12px;color:var(--muted);cursor:pointer">${_scHistExpanded ? '접기 ▲' : `더보기 (${todayRows.length - 5}건) ▼`}</div>`
    : '';

  const dailyHtml = dateKeys.map(d => {
    const s = byDate[d];
    const color = s.pnl >= 0 ? '#16a34a' : '#dc2626';
    const md = d.slice(5).replace('-', '/');
    return `<div style="display:flex;justify-content:space-between;padding:8px 4px;border-top:1px solid var(--border);font-size:12px;color:var(--muted)">
      <span>${md} · 거래 ${s.tickers.size}종목</span>
      <b style="color:${color}">일일수익금 ${s.pnl >= 0 ? '+' : ''}${Math.round(s.pnl).toLocaleString()}원</b>
    </div>`;
  }).join('');

  elHistory.innerHTML = (todayHtml + moreHtml + dailyHtml)
    || `<div style="color:var(--muted);font-size:13px;padding:12px 0;text-align:center">최근 거래 내역이 없습니다</div>`;
  setHistoryCount('sc', todayRows.length + dateKeys.length);
}

function _scJobCardHtml(j) {
    const isHolding    = j.phase === 'holding';
    const statusColor  = j.status === 'active' ? '#16a34a' : j.status === 'stopped' ? '#dc2626' : j.status === 'done' ? 'var(--muted)' : '#f59e0b';
    const statusLabel  = j.status === 'active' ? '실행중' : j.status === 'stopped' ? '정지됨' : j.status === 'done' ? '완료(1회성)' : '일시정지';
    const pnl          = j.realized_pnl_today || 0;
    const pnlColor     = pnl >= 0 ? '#16a34a' : '#dc2626';
    const marketBadge  = j.market === 'coin' ? '🪙 코인' : '📈 주식';
    const autoBadge    = j.source === 'auto'
      ? (j.discovery_mode === 'reversal'
          ? ' <span style="color:#f59e0b;font-weight:700">📉 급락반등포착</span>'
          : ' <span style="color:#7c3aed;font-weight:700">🔍 급등포착</span>')
      : '';
    const enteredLabel = isHolding && j.entered_at
      ? new Date(j.entered_at * 1000).toLocaleTimeString('ko-KR') : '';

    let holdingLine = '';
    if (isHolding) {
      const live = _scLivePrices[j.ticker];
      const buyPrice = j.buy_price || 0;
      if (live && buyPrice > 0) {
        const upnlPct = (live.price - buyPrice) / buyPrice * 100;
        const upnlColor = upnlPct >= 0 ? '#16a34a' : '#dc2626';
        holdingLine = `<div style="font-size:12px;margin-bottom:6px">
          진입가 ${Math.round(buyPrice).toLocaleString()}원 → 현재가 ${Math.round(live.price).toLocaleString()}원
          <b style="color:${upnlColor}">(${upnlPct >= 0 ? '+' : ''}${upnlPct.toFixed(2)}%)</b>
          <span style="color:var(--muted)"> · ${enteredLabel} 진입</span>
        </div>`;
      } else {
        holdingLine = `<div style="font-size:12px;color:var(--muted);margin-bottom:6px">진입가 ${Math.round(buyPrice).toLocaleString()}원 · ${enteredLabel} 진입 · 현재가 조회 중...</div>`;
      }
    }

    return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <div><b style="font-size:13px">${j.name}</b> <span style="font-size:11px;color:var(--muted)">${marketBadge} · ${j.ticker}</span>${autoBadge}</div>
        <span style="font-size:11px;font-weight:700;color:${statusColor}">${statusLabel}${isHolding ? ' · 보유중' : ''}</span>
      </div>
      ${holdingLine}
      ${j.stop_reason && !isHolding ? `<div style="font-size:11px;color:${j.status === 'stopped' ? '#dc2626' : 'var(--muted)'};margin-bottom:6px">${j.stop_reason}</div>` : ''}
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:12px;color:${pnlColor};font-weight:600">오늘 손익 ${pnl >= 0 ? '+' : ''}${Math.round(pnl).toLocaleString()}원 (${j.trades_today || 0}회)</span>
        <div style="display:flex;gap:6px">
          ${j.status !== 'stopped' && j.status !== 'done' ? `<button onclick="scToggleJob('${j.market}','${j.id}','${j.status === 'active' ? 'paused' : 'active'}')"
            style="padding:5px 10px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:11px;cursor:pointer">
            ${j.status === 'active' ? '⏸ 일시정지' : '▶ 시작'}</button>` : ''}
          <button onclick="scDeleteJob('${j.market}','${j.id}')"
            style="padding:5px 10px;border-radius:6px;border:1px solid #dc2626;background:transparent;color:#dc2626;font-size:11px;cursor:pointer">삭제</button>
        </div>
      </div>
    </div>`;
}

async function scToggleJob(market, id, status) {
  const url = (market === 'coin' ? '/api/scalp-coin' : '/api/scalp-stock') + `?id=${encodeURIComponent(id)}`;
  try {
    await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
    await scLoadJobs();
  } catch (e) { alert('변경 실패: ' + e.message); }
}

async function scDeleteJob(market, id) {
  if (!confirm('이 스캘핑 잡을 삭제할까요?')) return;
  const url = (market === 'coin' ? '/api/scalp-coin' : '/api/scalp-stock') + `?id=${encodeURIComponent(id)}`;
  try {
    const r = await fetch(url, { method: 'DELETE' });
    if (!r.ok) { const e = await r.json().catch(() => ({})); alert(e.error || '삭제 실패'); return; }
    await scLoadJobs();
  } catch (e) { alert('삭제 실패: ' + e.message); }
}
