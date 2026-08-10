async function loadMarketData() {
  const btn = document.getElementById('mkt-refresh-btn');
  if (btn) { btn.textContent = '⟳ 로딩 중...'; btn.disabled = true; }
  ['mkt-kospi','mkt-kosdaq','mkt-nasdaq','mkt-sp500','mkt-dow','mkt-usdkrw','mkt-vix','mkt-feargreed','mkt-gold','mkt-oil','mkt-us10y','mkt-us20y','mkt-us30y'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = '<div class="mkt-loading">로딩 중...</div>';
  });

  try {
    const r   = await fetch('/api/market');
    if (!r.ok) throw new Error(`API HTTP ${r.status}`);
    const d   = await r.json();
    if (d._errors) {
      const errs = Object.entries(d._errors).filter(([,v])=>v).map(([k,v])=>`${k}: ${v}`);
      if (errs.length) console.warn('시장현황 일부 실패:', errs.join(' | '));
    }
    const now = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });

    const setCard = (id, html) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = html;
    };

    const noData = (id) => { const el = document.getElementById(id); if (el) el.innerHTML = '<div class="mkt-loading" style="color:#475569">데이터 없음</div>'; };
    d.kospi  ? setCard('mkt-kospi',  _mktCard('mkt-kospi',  '🇰🇷 코스피',  d.kospi.price.toLocaleString('ko-KR', {maximumFractionDigits:2}),  d.kospi.chg,  d.kospi.chgPct))  : noData('mkt-kospi');
    d.kosdaq ? setCard('mkt-kosdaq', _mktCard('mkt-kosdaq', '🇰🇷 코스닥',  d.kosdaq.price.toLocaleString('ko-KR', {maximumFractionDigits:2}), d.kosdaq.chg, d.kosdaq.chgPct)) : noData('mkt-kosdaq');
    d.nasdaq ? setCard('mkt-nasdaq', _mktCard('mkt-nasdaq', '🇺🇸 나스닥',  d.nasdaq.price.toLocaleString('en-US', {maximumFractionDigits:2}), d.nasdaq.chg, d.nasdaq.chgPct)) : noData('mkt-nasdaq');
    d.sp500  ? setCard('mkt-sp500',  _mktCard('mkt-sp500',  '🇺🇸 S&P 500', d.sp500.price.toLocaleString('en-US', {maximumFractionDigits:2}),  d.sp500.chg,  d.sp500.chgPct))  : noData('mkt-sp500');
    d.dow    ? setCard('mkt-dow',    _mktCard('mkt-dow',    '🇺🇸 다우존스', d.dow.price.toLocaleString('en-US', {maximumFractionDigits:2}),    d.dow.chg,    d.dow.chgPct))    : noData('mkt-dow');

    if (!d.usdkrw)    noData('mkt-usdkrw');
    if (!d.vix)       noData('mkt-vix');
    if (!d.feargreed) noData('mkt-feargreed');
    if (!d.gold)      noData('mkt-gold');
    if (!d.us10y)     noData('mkt-us10y');
    if (d.usdkrw) {
      const hist = (d.usdkrw.history || []).map(p => p.close);
      setCard('mkt-usdkrw', `
        <div class="mkt-name">💱 원/달러 환율</div>
        <div class="mkt-price">${d.usdkrw.price.toFixed(1)}<span style="font-size:14px;color:#94a3b8;margin-left:4px">₩</span></div>
        <div class="${d.usdkrw.chg >= 0 ? 'mkt-chg-up' : 'mkt-chg-dn'}">${d.usdkrw.chg >= 0 ? '+' : ''}${d.usdkrw.chg.toFixed(2)} (${d.usdkrw.chgPct >= 0 ? '+' : ''}${d.usdkrw.chgPct.toFixed(2)}%)</div>
        ${hist.length >= 5 ? _mktSparkline(hist) : ''}`);
    }

    if (d.vix) {
      const vp = d.vix.price;
      const vColor = vp >= 40 ? '#f87171' : vp >= 30 ? '#fb923c' : vp >= 20 ? '#fbbf24' : vp >= 15 ? '#a3e635' : '#34d399';
      const vixScales = [
        { range: '0–15',  label: '극도의 안정', color: '#34d399', tip: '시장 자만심 주의' },
        { range: '15–20', label: '안정',        color: '#a3e635', tip: '정상 범위' },
        { range: '20–30', label: '주의',        color: '#fbbf24', tip: '변동성 확대' },
        { range: '30–40', label: '고위험',      color: '#fb923c', tip: '공포 심화' },
        { range: '40+',   label: '극단적 공포', color: '#f87171', tip: '패닉 셀링 구간' },
      ];
      const curScale = vp >= 40 ? 4 : vp >= 30 ? 3 : vp >= 20 ? 2 : vp >= 15 ? 1 : 0;
      setCard('mkt-vix', `
        <div class="mkt-name">📉 VIX (변동성 지수)</div>
        <div class="mkt-price" style="color:${vColor}">${vp.toFixed(2)}</div>
        <div class="${d.vix.chg >= 0 ? 'mkt-chg-up' : 'mkt-chg-dn'};margin-bottom:6px">${d.vix.chg >= 0 ? '+' : ''}${d.vix.chg.toFixed(2)}</div>
        <div style="border-top:1px solid #1e293b;padding-top:8px">
          ${vixScales.map((s, i) => `
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <span style="width:8px;height:8px;border-radius:50%;background:${s.color};flex-shrink:0"></span>
            <span style="font-size:11px;color:${s.color};font-weight:${i===curScale?'700':'400'}">${s.range} ${s.label}</span>
          </div>`).join('')}
        </div>`);
    }

    if (d.gold) setCard('mkt-gold', `
      <div class="mkt-name">🥇 금 현물 (XAU/USD)</div>
      <div class="mkt-price">${d.gold.price.toLocaleString('en-US', {maximumFractionDigits:2})}<span style="font-size:14px;color:#94a3b8;margin-left:4px">$</span></div>
      <div class="${d.gold.chg >= 0 ? 'mkt-chg-up' : 'mkt-chg-dn'}">${d.gold.chg >= 0 ? '+' : ''}${d.gold.chg.toFixed(2)} (${d.gold.chgPct >= 0 ? '+' : ''}${d.gold.chgPct.toFixed(2)}%)</div>`);
    else noData('mkt-gold');

    if (d.oil) {
      const oColor = d.oil.price >= 90 ? '#f87171' : d.oil.price >= 70 ? '#fbbf24' : '#34d399';
      setCard('mkt-oil', `
        <div class="mkt-name">🛢️ WTI 원유 선물</div>
        <div class="mkt-price" style="color:${oColor}">${d.oil.price.toFixed(2)}<span style="font-size:14px;color:#94a3b8;margin-left:4px">$/배럴</span></div>
        <div class="${d.oil.chg >= 0 ? 'mkt-chg-up' : 'mkt-chg-dn'}">${d.oil.chg >= 0 ? '+' : ''}${d.oil.chg.toFixed(2)} (${d.oil.chgPct >= 0 ? '+' : ''}${d.oil.chgPct.toFixed(2)}%)</div>
        <div style="font-size:11px;color:#64748b;margin-top:4px">${d.oil.price >= 90 ? '⚠️ 고유가 구간' : d.oil.price >= 70 ? '보통 구간' : '저유가 구간'}</div>`);
    } else noData('mkt-oil');

    const renderYield = (id, label, data, highThreshold, midThreshold) => {
      if (!data) { noData(id); return; }
      const p = data.price;
      const color = p >= highThreshold ? '#f87171' : p >= midThreshold ? '#fbbf24' : '#34d399';
      const tag = p >= highThreshold ? '⚠️ 고금리' : p >= midThreshold ? '중립' : '저금리';
      // 스파크라인 (금리: 상승=위험 빨강, 하락=안정 초록)
      let sparkHtml = '';
      if (data.history && data.history.length >= 2) {
        const vals = data.history;
        const W = 240, H = 44, PAD = 2;
        const min = Math.min(...vals), max = Math.max(...vals);
        const range = max - min || 0.01;
        const sx = i => (i / (vals.length - 1)) * (W - PAD * 2) + PAD;
        const sy = v => H - PAD - ((v - min) / range) * (H - PAD * 2);
        const pts = vals.map((v, i) => `${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ');
        const lx = sx(vals.length - 1), ly = sy(vals[vals.length - 1]);
        const trend = vals[vals.length - 1] >= vals[0];
        const col = trend ? '#f87171' : '#34d399';
        sparkHtml = `<div style="margin-top:8px;border-top:1px solid #1e293b;padding-top:6px">
          <div style="font-size:10px;color:#475569;margin-bottom:3px">최근 1개월 추이</div>
          <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;overflow:visible">
            <polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.5" stroke-linejoin="round" opacity="0.9"/>
            <circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="3" fill="${col}"/>
            <text x="${PAD}" y="${H - PAD - 1}" fill="#475569" font-size="9">${min.toFixed(2)}%</text>
            <text x="${PAD}" y="${PAD + 8}" fill="#475569" font-size="9">${max.toFixed(2)}%</text>
          </svg>
        </div>`;
      }
      setCard(id, `
        <div class="mkt-name">🏦 ${label}</div>
        <div class="mkt-price" style="color:${color}">${p.toFixed(3)}<span style="font-size:14px;color:#94a3b8;margin-left:4px">%</span></div>
        <div class="${data.chg >= 0 ? 'mkt-chg-up' : 'mkt-chg-dn'}">${data.chg >= 0 ? '+' : ''}${data.chg.toFixed(3)}%p</div>
        <div style="font-size:11px;color:#64748b;margin-top:4px">${tag} 구간</div>
        ${sparkHtml}`);
    };
    renderYield('mkt-us10y', '미국 10년 국채금리', d.us10y, 4.5, 3.5);
    renderYield('mkt-us20y', '미국 20년 국채금리', d.us20y, 4.8, 4.0);
    renderYield('mkt-us30y', '미국 30년 국채금리', d.us30y, 5.0, 4.0);

    if (d.feargreed) {
      const v   = d.feargreed.value;
      const { label, color } = _fgLabel(v);
      const fgScales = [
        { range: '0–24',  label: '극단적 공포', color: '#f87171', tip: '매수 기회 구간' },
        { range: '25–44', label: '공포',        color: '#fb923c', tip: '시장 불안 심리' },
        { range: '45–55', label: '중립',        color: '#facc15', tip: '관망 구간' },
        { range: '56–75', label: '탐욕',        color: '#4ade80', tip: '과열 주의' },
        { range: '76–100',label: '극단적 탐욕', color: '#34d399', tip: '매도 고려 구간' },
      ];
      setCard('mkt-feargreed', `
        <div class="mkt-name">😱 공포탐욕지수 (CNN)</div>
        <div class="mkt-price" style="color:${color}">${v}<span style="font-size:14px;color:#94a3b8;margin-left:4px">/ 100</span></div>
        <div style="font-size:13px;font-weight:600;color:${color};margin-bottom:6px">${label}</div>
        <div class="mkt-fg-bar"><div class="mkt-fg-dot" style="left:${v}%"></div></div>
        <div style="display:flex;justify-content:space-between;font-size:10px;color:#475569;margin-bottom:10px"><span>공포</span><span>중립</span><span>탐욕</span></div>
        <div style="border-top:1px solid #1e293b;padding-top:8px">
          ${fgScales.map(s => `
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <span style="width:8px;height:8px;border-radius:50%;background:${s.color};flex-shrink:0;${v >= parseInt(s.range) && v <= parseInt(s.range.split('–')[1]) ? 'box-shadow:0 0 5px '+s.color : ''}"></span>
            <span style="font-size:11px;color:${s.color};font-weight:${label===s.label?'700':'400'}">${s.range} ${s.label}</span>
          </div>`).join('')}
        </div>`);
    }

    const updEl = document.getElementById('mkt-updated');
    if (updEl) updEl.textContent = `${now} 기준`;
  } catch (e) {
    ['mkt-kospi','mkt-kosdaq','mkt-nasdaq','mkt-sp500','mkt-dow','mkt-usdkrw','mkt-vix','mkt-feargreed','mkt-gold','mkt-oil','mkt-us10y','mkt-us20y','mkt-us30y'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = `<div class="mkt-loading" style="color:#f87171">로드 실패<br><span style="font-size:10px;color:#94a3b8">${e.message}</span></div>`;
    });
    console.error('시장현황 로드 실패:', e);
  }

  if (btn) { btn.textContent = '⟳ 새로고침'; btn.disabled = false; }
}


// ══════════════════════════════════════════════════════════
// 자동매매 탭
// ══════════════════════════════════════════════════════════
const AT_BUY_FEE  = 0.00015;
const AT_SELL_FEE = 0.00015 + 0.0018;

let _atJobs  = [];
let _abJobs  = [];
let _atAccount = null;
let _atRefreshTimer = null;
let _atPriceTimer = null;
let _atBalanceTimer = null;
let _atConfig = { profitPct: 20, stopPct: 4, autoRefreshSec: 0 };

function atLoadConfig() {
  try {
    const saved = JSON.parse(localStorage.getItem('at_config') || '{}');
    _atConfig = { profitPct: 20, stopPct: 4, autoRefreshSec: 0, ...saved };
  } catch (_) {}
  const profitEl = document.getElementById('at-cfg-profit-pct');
  const stopEl   = document.getElementById('at-cfg-stop-pct');
  const arEl     = document.getElementById('at-cfg-auto-refresh');
  if (profitEl) profitEl.value = _atConfig.profitPct;
  if (stopEl)   stopEl.value   = _atConfig.stopPct;
  if (arEl)     arEl.value     = _atConfig.autoRefreshSec;
  const rProfit = document.getElementById('at-rule-profit');
  const rStop   = document.getElementById('at-rule-stop');
  if (rProfit) rProfit.textContent = _atConfig.profitPct;
  if (rStop)   rStop.textContent   = _atConfig.stopPct;
}

function atSaveConfig() {
  _atConfig.profitPct      = parseFloat(document.getElementById('at-cfg-profit-pct')?.value) || 20;
  _atConfig.stopPct        = parseFloat(document.getElementById('at-cfg-stop-pct')?.value)   || 4;
  _atConfig.autoRefreshSec = parseInt(document.getElementById('at-cfg-auto-refresh')?.value)  || 0;
  localStorage.setItem('at_config', JSON.stringify(_atConfig));
  const rProfit = document.getElementById('at-rule-profit');
  const rStop   = document.getElementById('at-rule-stop');
  if (rProfit) rProfit.textContent = _atConfig.profitPct;
  if (rStop)   rStop.textContent   = _atConfig.stopPct;
  clearInterval(_atBalanceTimer);
  if (_atConfig.autoRefreshSec > 0) {
    _atBalanceTimer = setInterval(() => {
      if (document.querySelector('.tab-btn.active')?.getAttribute('onclick')?.includes('autotrade')) {
        atRefreshBalance();
      } else {
        clearInterval(_atBalanceTimer);
      }
    }, _atConfig.autoRefreshSec * 1000);
  }
  const msg = document.getElementById('at-cfg-msg');
  if (msg) { msg.textContent = '✓ 저장 완료'; msg.style.color = 'var(--green)'; setTimeout(() => { msg.textContent = ''; }, 2000); }
}

function atCheckDaemonStatus() {
  const label = document.getElementById('at-daemon-status-label');
  const updEl = document.getElementById('at-daemon-updated-at');
  if (!_atAccount) {
    if (label) { label.textContent = '데이터 없음'; label.style.color = 'var(--muted)'; }
    if (updEl) updEl.textContent = '잔고 새로고침 후 확인하세요';
    return;
  }
  const updatedAt = _atAccount.updated_at || '';
  if (!updatedAt) {
    if (label) { label.textContent = '갱신 시각 없음'; label.style.color = 'var(--muted)'; }
    return;
  }
  // 마지막 갱신으로부터 경과 시간 계산
  try {
    const last = new Date(updatedAt.replace(' ', 'T') + '+09:00');
    const diffMin = Math.floor((Date.now() - last.getTime()) / 60000);
    const isAlive = diffMin < 10;
    if (label) {
      label.textContent = isAlive ? '● 정상 동작 중' : '● 응답 없음 (10분 이상 경과)';
      label.style.color = isAlive ? 'var(--green)' : 'var(--red)';
    }
    if (updEl) updEl.textContent = `마지막 갱신: ${updatedAt} (${diffMin}분 전)`;
  } catch (_) {
    if (label) { label.textContent = updatedAt; label.style.color = 'var(--text)'; }
  }
}

