// ══════════════════════════════════════════════════════════
// 초단타(스캘핑) 자동매매 탭 — 코인/주식 공용
// API: /api/scalp-coin, /api/scalp-stock, /api/scalp-control (전체 킬스위치)
// ══════════════════════════════════════════════════════════

let _scRefreshTimer = null;
let _scAcTimer = null;
let _scControl = { coin_enabled: true, stock_enabled: true };
let _scJobs = { coin: [], stock: [] };

async function initScalp() {
  await scLoadControl();
  await scLoadJobs();
  clearInterval(_scRefreshTimer);
  _scRefreshTimer = setInterval(() => {
    if (document.querySelector('.tab-btn.active')?.getAttribute('onclick')?.includes('scalp')) {
      scLoadJobs();
    } else {
      clearInterval(_scRefreshTimer);
    }
  }, 10000);
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
}

function scRenderSummary() {
  const el = document.getElementById('sc-summary');
  if (!el) return;
  const all = [...(_scJobs.coin || []), ...(_scJobs.stock || [])];
  const pnl = all.reduce((s, j) => s + (j.realized_pnl_today || 0), 0);
  const trades = all.reduce((s, j) => s + (j.trades_today || 0), 0);
  const color = pnl >= 0 ? '#16a34a' : '#dc2626';
  el.innerHTML = `<span style="font-size:12px;color:var(--muted)">오늘 실현손익</span>
    <span style="font-size:18px;font-weight:800;color:${color};margin-left:8px">${pnl >= 0 ? '+' : ''}${Math.round(pnl).toLocaleString()}원</span>
    <span style="font-size:12px;color:var(--muted);margin-left:10px">거래 ${trades}회</span>`;
}

function scRenderJobs() {
  const el = document.getElementById('sc-job-list');
  if (!el) return;
  const all = [
    ...(_scJobs.coin  || []).map(j => ({ ...j, market: 'coin' })),
    ...(_scJobs.stock || []).map(j => ({ ...j, market: 'stock' })),
  ];
  if (!all.length) {
    el.innerHTML = `<div style="color:var(--muted);font-size:13px;padding:20px;text-align:center">등록된 스캘핑 잡이 없습니다</div>`;
    return;
  }

  el.innerHTML = all.map(j => {
    const isHolding    = j.phase === 'holding';
    const statusColor  = j.status === 'active' ? '#16a34a' : j.status === 'stopped' ? '#dc2626' : '#f59e0b';
    const statusLabel  = j.status === 'active' ? '실행중' : j.status === 'stopped' ? '정지됨' : '일시정지';
    const pnl          = j.realized_pnl_today || 0;
    const pnlColor     = pnl >= 0 ? '#16a34a' : '#dc2626';
    const marketBadge  = j.market === 'coin' ? '🪙 코인' : '📈 주식';
    const enteredLabel = isHolding && j.entered_at
      ? new Date(j.entered_at * 1000).toLocaleTimeString('ko-KR') : '';

    return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <div><b style="font-size:13px">${j.name}</b> <span style="font-size:11px;color:var(--muted)">${marketBadge} · ${j.ticker}</span></div>
        <span style="font-size:11px;font-weight:700;color:${statusColor}">${statusLabel}${isHolding ? ' · 보유중' : ''}</span>
      </div>
      ${isHolding ? `<div style="font-size:12px;color:var(--muted);margin-bottom:6px">진입가 ${Math.round(j.buy_price || 0).toLocaleString()}원 · ${enteredLabel} 진입</div>` : ''}
      ${j.status === 'stopped' && j.stop_reason ? `<div style="font-size:11px;color:#dc2626;margin-bottom:6px">${j.stop_reason}</div>` : ''}
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:12px;color:${pnlColor};font-weight:600">오늘 손익 ${pnl >= 0 ? '+' : ''}${Math.round(pnl).toLocaleString()}원 (${j.trades_today || 0}회)</span>
        <div style="display:flex;gap:6px">
          ${j.status !== 'stopped' ? `<button onclick="scToggleJob('${j.market}','${j.id}','${j.status === 'active' ? 'paused' : 'active'}')"
            style="padding:5px 10px;border-radius:6px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:11px;cursor:pointer">
            ${j.status === 'active' ? '⏸ 일시정지' : '▶ 시작'}</button>` : ''}
          <button onclick="scDeleteJob('${j.market}','${j.id}')"
            style="padding:5px 10px;border-radius:6px;border:1px solid #dc2626;background:transparent;color:#dc2626;font-size:11px;cursor:pointer">삭제</button>
        </div>
      </div>
    </div>`;
  }).join('');
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
