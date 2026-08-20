// ETF 탭 현재가 TTL 캐시 (3분) — 탭 재방문 시 API 호출 스킵
const _etfPriceCache     = {};
const ETF_PRICE_CACHE_TTL = 3 * 60 * 1000;

function _applyEtfPriceCache() {
  const now = Date.now();
  _etfRecords.forEach(r => {
    const c = r.ticker && _etfPriceCache[r.ticker];
    if (c && now - c.at < ETF_PRICE_CACHE_TTL) {
      r.current_price = c.price;
      r.price_chg_pct = c.chgPct ?? r.price_chg_pct;
    }
  });
}

async function loadEtfRecords() {
  try {
    const d = await _fetchBinData();
    _etfRecords   = d.etf          ?? [];
    _transactions = d.transactions ?? [];
  } catch {
    _etfRecords = [];
    _transactions = [];
  }
  if (ensureEtfIds(_etfRecords)) await saveEtfRecords();

  _applyEtfPriceCache(); // 캐시 가격 즉시 적용 → Phase 1 렌더
  renderEtfCards();
  loadDivRecords();
  refreshAllEtfPrices(true); // 탭 진입 시 현재가 업데이트 (캐시 유효 시 스킵)

  // 20분 자동 현재가 업데이트 — 탭이 ETF일 때만 반복
  clearInterval(_etfAutoRefreshTimer);
  _etfAutoRefreshTimer = setInterval(async () => {
    const etfTab = document.getElementById('tab-etf');
    if (!etfTab || !etfTab.classList.contains('active')) return;
    await refreshAllEtfPrices(true); // silent=true: 버튼 UI 건드리지 않음
  }, ETF_AUTO_REFRESH_MS);
}

async function saveEtfRecords() {
  await fetch('/api/etf', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ records: _etfRecords }),
  });
  _invalidateBinCache();
}

const TAX_RATE = 0.154; // 배당소득세 14% + 지방소득세 1.4%

function genSparkline(r, W=300, H=52) {
  const pnlPct  = (r.avg_price > 0 && r.current_price)
    ? (r.current_price - r.avg_price) / r.avg_price : 0;
  const isUp    = pnlPct >= 0;
  const color   = isUp ? '#34d399' : '#f87171';
  const fillClr = isUp ? 'rgba(52,211,153,.10)' : 'rgba(248,113,113,.10)';
  const N = 14;
  // 티커 기반 결정론적 랜덤 시드
  let seed = (r.ticker || 'ETF').split('').reduce((a,c) => a + c.charCodeAt(0), 0) * 31 + (r.id || 1);
  const rand = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 0xffffffff; };
  const pts = [];
  let v = H * 0.5;
  const trendSlope = Math.max(-0.4, Math.min(0.4, pnlPct)) * H;
  for (let i = 0; i < N; i++) {
    const prog  = i / (N - 1);
    const noise = (rand() - 0.5) * H * 0.35;
    v = H * 0.65 - trendSlope * prog + noise;
    pts.push([W * prog, Math.max(4, Math.min(H - 4, v))]);
  }
  const d = pts.map((p,i) => (i===0?'M':'L') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
  const last = pts[pts.length-1];
  const fillD = `${d} L${W},${H} L0,${H} Z`;
  return `<svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="display:block">
    <path d="${fillD}" fill="${fillClr}" stroke="none"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="3" fill="${color}"/>
  </svg>`;
}

// ── 인라인 카드 차트 ──────────────────────────────────────
const _inlineChartInstances = {}; // ticker → LightweightCharts instance

function _destroyInlineCharts(tickers) {
  tickers.forEach(t => {
    if (_inlineChartInstances[t]) {
      try { _inlineChartInstances[t].remove(); } catch (_) {}
      delete _inlineChartInstances[t];
    }
  });
}

async function renderInlineCharts(records) {
  if (typeof LightweightCharts === 'undefined') return;
  const tickers = records.map(r => r.ticker).filter(Boolean);
  _destroyInlineCharts(tickers);

  await Promise.all(records.map(async r => {
    if (!r.ticker) return;
    const wrap = document.getElementById(`inline-chart-wrap-${r.ticker}`);
    if (!wrap) return;

    try {
      const res = await fetch(`/api/quote?ticker=${r.ticker}&chart=1&count=30`);
      if (!res.ok) return;
      const { candles } = await res.json();
      if (!candles || candles.length < 2) return;

      // 수익 여부로 색상 결정
      const isUp = r.current_price && r.avg_price ? r.current_price >= r.avg_price : true;
      const lineColor = isUp ? '#34d399' : '#f87171';
      const topColor  = isUp ? 'rgba(52,211,153,0.15)' : 'rgba(248,113,113,0.15)';

      const chart = LightweightCharts.createChart(wrap, {
        width:  wrap.clientWidth || 200,
        height: 80,
        layout: { background: { type: LightweightCharts.ColorType.Solid, color: 'transparent' }, textColor: 'transparent' },
        grid:   { vertLines: { visible: false }, horzLines: { visible: false } },
        rightPriceScale: { visible: false },
        leftPriceScale:  { visible: false },
        timeScale:       { visible: false, borderVisible: false },
        crosshair:       { mode: LightweightCharts.CrosshairMode.Hidden },
        handleScroll:    false,
        handleScale:     false,
        kineticScroll:   { touch: false, mouse: false },
      });

      const series = chart.addAreaSeries({
        lineColor, topColor, bottomColor: 'transparent',
        lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false,
      });
      series.setData(candles.map(c => ({ time: c.time, value: c.close })));
      chart.timeScale().fitContent();

      _inlineChartInstances[r.ticker] = chart;
    } catch (_) {}
  }));
}

function renderEtfCards() {
  const list = document.getElementById('etf-card-list');
  if (!list) return;

  const buyTotal  = _etfRecords.reduce((s, r) => s + (r.qty || 0) * (r.avg_price || 0), 0);
  const evalTotal = _etfRecords.reduce((s, r) => s + (r.qty || 0) * (r.current_price || 0), 0);
  const pnl       = evalTotal - buyTotal;
  const pnlPct    = buyTotal > 0 ? pnl / buyTotal * 100 : null;
  const pnlClass  = pnl >= 0 ? 'up' : 'dn';
  const pnlSign   = pnl >= 0 ? '+' : '';
  const fmtMoney  = v => v ? v.toLocaleString() + '원' : '-';
  const fmtMoneyK = v => {
    if (!v) return '-';
    if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(1) + '억원';
    if (Math.abs(v) >= 1e4) return Math.round(v / 1e4).toLocaleString() + '만원';
    return v.toLocaleString() + '원';
  };

  // ── 배당 합계 계산 ────────────────────────────────────
  // 연간 배당(세전) = 현재가 × 연간배당률 × 보유수량
  const divPreTotal  = _etfRecords.reduce((s, r) => {
    if (!r.current_price || !r.annual_div_rate || r.div_cycle === '무배당') return s;
    return s + r.current_price * (r.annual_div_rate / 100) * (r.qty || 0);
  }, 0);
  const divPostTotal  = divPreTotal * (1 - TAX_RATE);
  const divMonthly    = divPostTotal / 12;
  const divEtfCount   = _etfRecords.filter(r => r.annual_div_rate && r.div_cycle !== '무배당').length;

  document.getElementById('etf-k-count').textContent = _etfRecords.length + '개';
  document.getElementById('etf-k-buy').textContent   = fmtMoneyK(buyTotal);
  document.getElementById('etf-k-eval').textContent  = fmtMoneyK(evalTotal);
  const pnlEl = document.getElementById('etf-k-pnl');
  pnlEl.className = 'etf-kpi-val ' + pnlClass;
  pnlEl.textContent = pnlPct !== null
    ? `${pnlSign}${pnlPct.toFixed(1)}% ${pnlSign}${fmtMoneyK(Math.abs(pnl))}`
    : '-';

  const divPreEl  = document.getElementById('etf-k-div-pre');
  const divPostEl = document.getElementById('etf-k-div-post');
  const divMonEl  = document.getElementById('etf-k-div-monthly');
  if (divPreEl)  divPreEl.textContent  = divPreTotal  ? Math.round(divPreTotal).toLocaleString()  + '원' : '-';
  if (divPostEl) divPostEl.textContent = divPostTotal ? Math.round(divPostTotal).toLocaleString() + '원' : '-';
  if (divMonEl)  divMonEl.textContent  = divMonthly   ? Math.round(divMonthly).toLocaleString()  + '원' : '-';
  const subEl = document.getElementById('etf-k-div-pre-sub');
  if (subEl) subEl.textContent = divEtfCount ? `${divEtfCount}개 종목 합산` : '';
  const monSubEl = document.getElementById('etf-k-div-monthly-sub');
  if (monSubEl) monSubEl.textContent = divMonthly ? `연간 ÷ 12개월` : '';
  const divYieldEl    = document.getElementById('etf-k-div-yield');
  const divYieldSubEl = document.getElementById('etf-k-div-yield-sub');
  if (divYieldEl) {
    const divYieldPct = buyTotal > 0 && divPostTotal > 0 ? divPostTotal / buyTotal * 100 : null;
    divYieldEl.textContent = divYieldPct !== null ? divYieldPct.toFixed(2) + '%' : '-';
  }
  if (divYieldSubEl) divYieldSubEl.textContent = (buyTotal > 0 && divPostTotal > 0) ? '세후배당 ÷ 총매입금' : '';

  if (!_etfRecords.length) {
    list.innerHTML = '<div style="text-align:center;color:var(--muted);padding:48px 20px">등록된 ETF가 없습니다.<br><small>위 버튼으로 추가해보세요.</small></div>';
    return;
  }

  list.innerHTML = _etfRecords.map(r => {
    const buy_   = (r.qty || 0) * (r.avg_price || 0);
    const eval_  = (r.qty || 0) * (r.current_price || 0);
    const profit = eval_ - buy_;
    const pPct   = buy_ > 0 ? profit / buy_ * 100 : null;
    const pc     = profit >= 0 ? 'up' : 'dn';
    const ps     = profit >= 0 ? '+' : '';
    const chgCls = (r.price_chg_pct || 0) >= 0 ? 'up' : 'dn';
    const chgSgn = (r.price_chg_pct || 0) >= 0 ? '+' : '';
    const hasDIV = r.div_cycle && r.div_cycle !== '무배당';

    // 개별 배당 계산 (세전/세후)
    const annDivPerShare  = (r.current_price && r.annual_div_rate)
      ? r.current_price * (r.annual_div_rate / 100) : 0;
    const annDivPre   = Math.round(annDivPerShare * (r.qty || 0));
    const annDivPost  = Math.round(annDivPre  * (1 - TAX_RATE));
    const recDivPre   = Math.round((r.recent_div || 0) * (r.qty || 0));
    const recDivPost  = Math.round(recDivPre * (1 - TAX_RATE));

    const pnlStr  = pPct !== null
      ? `${ps}${profit.toLocaleString()}원 (${ps}${pPct.toFixed(2)}%)`
      : (buy_ ? `${ps}${profit.toLocaleString()}원` : '-');
    const rdStr   = r.recent_div ? r.recent_div.toLocaleString() + '원' : '-';
    const rateStr = (r.recent_div_rate || r.annual_div_rate)
      ? `${r.recent_div_rate || 0}% / ${r.annual_div_rate || 0}%` : '-';

    return `<div class="etf-card" onclick="toggleEtfExpand(${r.id})">
      <!-- ── 카드 페이스 ── -->
      <div class="etf2-face">
        <div class="etf2-top">
          <div style="flex:1;min-width:0">
            <div class="etf2-name">${r.name || '종목명 없음'}</div>
            <div class="etf2-badges">
              ${r.ticker ? `<span class="etf-ticker-badge">${r.ticker}</span>` : ''}
              ${r.div_cycle ? `<span class="etf-ticker-badge" style="background:#052e16;color:#34d399;border:1px solid #166534">${r.div_cycle}</span>` : ''}
            </div>
          </div>
          <div class="etf-head-actions" onclick="event.stopPropagation()">
            ${r.ticker ? `<button onclick="openPriceChart('${r.ticker}','${r.name}')" title="차트">📈</button>` : ''}
            <button onclick="openDivScheduleModal(${r.id})" title="배당 일정">📅</button>
            <button onclick="openDivModal(${r.id})" title="배당 입력">💰</button>
            <button onclick="editEtf(${r.id})">✏️</button>
            <button class="del" onclick="deleteEtf(${r.id})">🗑️</button>
          </div>
        </div>
        <div class="etf2-price-area">
          ${r.current_price
            ? `<span class="etf2-price">${r.current_price.toLocaleString()}원</span>`
            : `<span class="etf2-price" style="color:var(--muted)">-</span>`}
          ${r.price_chg_pct != null
            ? `<span class="etf2-chg ${chgCls}">${chgSgn}${r.price_chg_pct}%</span>`
            : ''}
        </div>
        <div class="etf2-metrics">
          <div class="etf2-metric">
            <div class="etf2-metric-label">매입가</div>
            <div class="etf2-metric-val">${(r.avg_price || 0).toLocaleString()}원</div>
          </div>
          <div class="etf2-metric">
            <div class="etf2-metric-label">분배율</div>
            <div class="etf2-metric-val">${r.annual_div_rate ? r.annual_div_rate + '%' : '-'}</div>
          </div>
          <div class="etf2-metric">
            <div class="etf2-metric-label">수량</div>
            <div class="etf2-metric-val">${(r.qty || 0).toLocaleString()}주</div>
          </div>
        </div>
        <div class="etf2-chart-wrap" id="inline-chart-wrap-${r.ticker}"></div>
        <div class="etf2-footer">
          <div class="etf2-footer-item">
            <span class="etf2-footer-label">보유금액</span>
            <span class="etf2-footer-val">${eval_ ? eval_.toLocaleString() + '원' : '-'}</span>
          </div>
          <div class="etf2-footer-item">
            <span class="etf2-footer-label">평가손익</span>
            <span class="etf2-footer-val ${pc}">${ps}${profit.toLocaleString()}원${pPct !== null ? ` (${ps}${pPct.toFixed(1)}%)` : ''}</span>
          </div>
        </div>
        <div class="etf2-expand-hint">▾ 상세 보기</div>
      </div>
      <div class="etf-card-detail" id="etf-detail-${r.id}">
        <div class="etf-card-body">
          <div class="etf-col">
            <div class="etf-col-title">배당 정보</div>
            ${r.div_cycle ? `<div class="etf-row"><span class="etf-row-label">배당주기</span><span class="etf-row-val">${r.div_cycle}</span></div>` : ''}
            ${hasDIV && r.div_months ? `<div class="etf-row"><span class="etf-row-label">배당월</span><span class="etf-row-val">${r.div_months}</span></div>` : ''}
            ${hasDIV && r.recent_div ? `<div class="etf-row"><span class="etf-row-label">주당 최근 배당</span><span class="etf-row-val">${r.recent_div.toLocaleString()}원</span></div>` : ''}
            ${hasDIV ? `<div class="etf-row"><span class="etf-row-label">최근/연간 배당률</span><span class="etf-row-val">${r.recent_div_rate || 0}% / ${r.annual_div_rate || 0}%</span></div>` : ''}
            ${!hasDIV ? `<div class="etf-row"><span class="etf-row-val" style="color:var(--muted)">${r.div_cycle || '배당 정보 없음'}</span></div>` : ''}
          </div>
          <div class="etf-col">
            <div class="etf-col-title">보유 정보</div>
            <div class="etf-row"><span class="etf-row-label">보유수량</span><span class="etf-row-val">${(r.qty || 0).toLocaleString()}주</span></div>
            <div class="etf-row"><span class="etf-row-label">평균매수가</span><span class="etf-row-val">${(r.avg_price || 0).toLocaleString()}원</span></div>
            <div class="etf-row"><span class="etf-row-label">매입금액</span><span class="etf-row-val">${buy_.toLocaleString()}원</span></div>
            <div class="etf-row"><span class="etf-row-label">평가금액</span><span class="etf-row-val">${eval_.toLocaleString()}원</span></div>
            ${r.ref_date ? `<div class="etf-row"><span class="etf-row-label">기준일</span><span class="etf-row-val">${r.ref_date}</span></div>` : ''}
          </div>
        </div>
        ${hasDIV && (recDivPre || annDivPre) ? `
        <div style="padding:8px 14px;border-top:1px solid var(--border);background:#f0fdf4;display:flex;gap:20px;flex-wrap:wrap;align-items:center">
          <span style="font-size:10px;font-weight:700;color:#15803d;text-transform:uppercase;letter-spacing:.05em">배당 수령 예상</span>
          ${recDivPre ? `
          <div style="display:flex;flex-direction:column;gap:1px">
            <span style="font-size:10px;color:var(--muted)">최근 1회 — 세전 ${recDivPre.toLocaleString()}원</span>
            <span style="font-size:12px;font-weight:700;color:#16a34a">세후 ${recDivPost.toLocaleString()}원</span>
          </div>` : ''}
          ${annDivPre ? `
          <div style="display:flex;flex-direction:column;gap:1px">
            <span style="font-size:10px;color:var(--muted)">연간 합계 — 세전 ${annDivPre.toLocaleString()}원</span>
            <span style="font-size:12px;font-weight:700;color:#16a34a">세후 ${annDivPost.toLocaleString()}원</span>
          </div>` : ''}
        </div>` : ''}
        <div class="etf-tx-section" onclick="event.stopPropagation()">
          <div class="etf-tx-header">
            <span class="etf-tx-title">거래 내역</span>
            <button class="etf-tx-btn" onclick="openTransModal(${r.id})">+ 거래 추가</button>
          </div>
          <div id="etf-tx-${r.id}">
            ${((_transactions || []).filter(t => t.etf_id == r.id).sort((a,b) => b.date > a.date ? 1 : -1).map(t => `
              <div class="etf-tx-row">
                <span class="etf-tx-date">${t.date}</span>
                <span class="etf-tx-type-${t.type}">${t.type === 'buy' ? '매수' : '매도'}</span>
                <span class="etf-tx-qty">${t.type === 'buy' ? '+' : '-'}${(t.qty_change||0).toLocaleString()}주${t.price ? ' @' + t.price.toLocaleString() + '원' : ''}</span>
                <button class="div-del-btn" onclick="deleteTransaction(${t.id})">삭제</button>
              </div>`).join('') || '<div style="font-size:11px;color:var(--muted);padding:4px 0">거래 내역 없음 — [+ 거래 추가]로 매수/매도를 기록하세요</div>')}
          </div>
        </div>
        <div class="etf-pnl-bar">
          <div class="etf-pnl-group">
            <span class="etf-pnl-label">평가손익</span>
            <span class="etf-pnl-val ${pc}">${ps}${profit.toLocaleString()}원</span>
          </div>
          ${pPct !== null ? `<div class="etf-pnl-group"><span class="etf-pnl-label">수익률</span><span class="etf-pnl-val ${pc}">${ps}${pPct.toFixed(2)}%</span></div>` : ''}
        </div>
      </div>
    </div>`;
  }).join('');
  renderInlineCharts(_etfRecords);
}

// ── 주가 차트 모달 ────────────────────────────────────────
let _priceChart = null;
let _priceSeries = null;
let _chartType = 'line'; // 'line' | 'bar'
let _chartCandles = [];
let _chartTicker = '';
let _chartName = '';

function openPriceChart(ticker, name) {
  if (!ticker) return;
  _chartTicker = ticker;
  _chartName = name;
  const modal = document.getElementById('price-chart-modal');
  modal.style.display = 'flex';
  document.getElementById('chart-modal-title').textContent = name || ticker;
  document.getElementById('chart-modal-ticker').textContent = ticker;
  _loadChartData();
}

function closePriceChart() {
  document.getElementById('price-chart-modal').style.display = 'none';
  if (_priceChart) { _priceChart.remove(); _priceChart = null; _priceSeries = null; }
}

async function _loadChartData() {
  const container = document.getElementById('price-chart-container');
  const loading   = document.getElementById('chart-loading');
  const errorEl   = document.getElementById('chart-error');
  container.style.display = 'none';
  loading.style.display   = 'block';
  errorEl.style.display   = 'none';
  if (_priceChart) { _priceChart.remove(); _priceChart = null; _priceSeries = null; }

  try {
    const r = await fetch(`/api/quote?ticker=${_chartTicker}&chart=1&count=60`);
    const d = await r.json();
    if (!r.ok || !d.candles?.length) throw new Error(d.error || '데이터 없음');
    _chartCandles = d.candles;
    loading.style.display   = 'none';
    container.style.display = 'block';
    _renderChart();
  } catch (e) {
    loading.style.display  = 'none';
    errorEl.style.display  = 'block';
    errorEl.textContent    = '차트 데이터를 불러오지 못했습니다: ' + e.message;
  }
}

function _renderChart() {
  const container = document.getElementById('price-chart-container');
  if (_priceChart) { _priceChart.remove(); _priceChart = null; _priceSeries = null; }

  _priceChart = LightweightCharts.createChart(container, {
    layout:     { background: { color: 'var(--surface, #ffffff)' === 'var(--surface, #ffffff)' ? getComputedStyle(document.documentElement).getPropertyValue('--surface').trim() || '#ffffff' : '#ffffff', type: LightweightCharts.ColorType.Solid }, textColor: '#9EA3B0' },
    grid:       { vertLines: { color: '#ebebeb' }, horzLines: { color: '#ebebeb' } },
    timeScale:  { borderColor: '#ebebeb', timeVisible: true },
    rightPriceScale: { borderColor: '#ebebeb' },
    crosshair:  { mode: LightweightCharts.CrosshairMode.Normal },
    width:  container.clientWidth,
    height: 320,
  });

  if (_chartType === 'bar') {
    _priceSeries = _priceChart.addCandlestickSeries({
      upColor:   '#16a34a', downColor: '#dc2626',
      borderUpColor: '#16a34a', borderDownColor: '#dc2626',
      wickUpColor:   '#16a34a', wickDownColor:   '#dc2626',
    });
    _priceSeries.setData(_chartCandles);
  } else {
    _priceSeries = _priceChart.addAreaSeries({
      lineColor:   '#3D5AFE',
      topColor:    'rgba(61,90,254,0.18)',
      bottomColor: 'rgba(61,90,254,0)',
      lineWidth: 2,
    });
    _priceSeries.setData(_chartCandles.map(c => ({ time: c.time, value: c.close })));
  }

  _priceChart.timeScale().fitContent();
  _updateChartToggleUI();

  window.addEventListener('resize', _onChartResize);
}

function _onChartResize() {
  const container = document.getElementById('price-chart-container');
  if (_priceChart && container) _priceChart.applyOptions({ width: container.clientWidth });
}

function setChartType(type) {
  if (_chartType === type) return;
  _chartType = type;
  if (_chartCandles.length) _renderChart();
}

function _updateChartToggleUI() {
  const lineBtn = document.getElementById('chart-btn-line');
  const barBtn  = document.getElementById('chart-btn-bar');
  if (!lineBtn || !barBtn) return;
  [lineBtn, barBtn].forEach((btn, i) => {
    const isActive = (i === 0 && _chartType === 'line') || (i === 1 && _chartType === 'bar');
    btn.style.background = isActive ? '#3D5AFE' : '';
    btn.style.color = isActive ? '#fff' : '';
  });
}

function toggleEtfExpand(id) {
  const el = document.getElementById('etf-detail-' + id);
  if (!el) return;
  el.classList.toggle('open');
}

function openEtfModal(id) {
  const r = (id != null) ? _etfRecords.find(x => x.id == id) : null;
  document.getElementById('etf-modal-title').textContent = r ? 'ETF 수정' : 'ETF 추가';
  document.getElementById('ef-id').value               = r ? r.id : '';
  document.getElementById('ef-ref-date').value         = r?.ref_date      || new Date().toISOString().slice(0, 10);
  document.getElementById('ef-name').value             = r?.name          || '';
  document.getElementById('ef-ticker').value           = r?.ticker        || '';
  document.getElementById('ef-price').value            = r?.current_price != null ? r.current_price : '';
  document.getElementById('ef-chg').value              = r?.price_chg_pct != null ? r.price_chg_pct : '';
  document.getElementById('ef-div-cycle').value        = r?.div_cycle     || '';
  document.getElementById('ef-div-months').value       = r?.div_months    || '';
  document.getElementById('ef-recent-div').value       = r?.recent_div    || '';
  document.getElementById('ef-recent-div-date').value  = r?.recent_div_date || '';
  // recent_div_rate: 저장된 값 표시 (readonly — calcRecentDivRateFromInput으로만 갱신)
  document.getElementById('ef-recent-div-rate').value  = r?.recent_div_rate != null ? r.recent_div_rate : '';
  // 수동 고정 배지
  const badge = document.getElementById('ef-manual-badge');
  if (badge) badge.style.display = r?.recent_div_manual ? 'inline' : 'none';
  document.getElementById('ef-annual-div-rate').value  = r?.annual_div_rate != null ? r.annual_div_rate : '';
  document.getElementById('ef-qty').value              = r?.qty           || '';
  document.getElementById('ef-avg-price').value        = r?.avg_price     || '';
  calcEtfPreview();
  document.getElementById('etf-modal').style.display = 'block';
  // 티커가 있으면 최신 현재가 자동 조회
  const ticker = r?.ticker?.trim();
  if (ticker) fetchAndFillQuote(ticker);
}

function closeEtfModal() {
  document.getElementById('etf-modal').style.display = 'none';
}

function calcEtfPreview() {
  const qty   = parseInt(document.getElementById('ef-qty').value)       || 0;
  const avg   = parseInt(document.getElementById('ef-avg-price').value) || 0;
  const price = parseInt(document.getElementById('ef-price').value)     || 0;
  const prev  = document.getElementById('ef-preview');
  if (!qty || !avg) { prev.style.display = 'none'; return; }
  const buy_  = qty * avg;
  const eval_ = qty * price;
  const rate  = buy_ > 0 ? (eval_ - buy_) / buy_ * 100 : null;
  const rSign = (rate || 0) >= 0 ? '+' : '';
  const rCol  = (rate || 0) >= 0 ? 'var(--green)' : 'var(--red)';
  document.getElementById('ef-prev-buy').textContent   = buy_.toLocaleString() + '원';
  document.getElementById('ef-prev-eval').textContent  = eval_ ? eval_.toLocaleString() + '원' : '-';
  document.getElementById('ef-prev-rate').style.color  = rCol;
  document.getElementById('ef-prev-rate').textContent  = rate !== null ? rSign + rate.toFixed(2) + '%' : '-';
  prev.style.display = 'block';
}

async function saveEtfRecord() {
  const name = document.getElementById('ef-name').value.trim();
  const qty  = parseInt(document.getElementById('ef-qty').value)       || 0;
  const avg  = parseInt(document.getElementById('ef-avg-price').value) || 0;
  if (!name)       { alert('ETF명을 입력하세요'); return; }
  if (!qty || !avg){ alert('보유수량과 평균매수가를 입력하세요'); return; }
  const btn = document.querySelector('[onclick="saveEtfRecord()"]');
  if (btn?.disabled) return;
  if (btn) { btn.disabled = true; btn.textContent = '저장 중...'; }
  try {

  const idVal = document.getElementById('ef-id').value;
  const rec = {
    id:               idVal ? Number(idVal) : null,
    ref_date:         document.getElementById('ef-ref-date').value         || '',
    name,
    ticker:           document.getElementById('ef-ticker').value.trim(),
    current_price:    parseFloat(document.getElementById('ef-price').value)           || 0,
    price_chg_pct:    parseFloat(document.getElementById('ef-chg').value)             || 0,
    div_cycle:        document.getElementById('ef-div-cycle').value,
    div_months:       document.getElementById('ef-div-months').value.trim(),
    recent_div:       parseFloat(document.getElementById('ef-recent-div').value)      || 0,
    recent_div_date:  document.getElementById('ef-recent-div-date').value.trim(),
    recent_div_rate:  parseFloat(document.getElementById('ef-recent-div-rate').value) || 0,
    recent_div_manual: (parseFloat(document.getElementById('ef-recent-div').value) || 0) > 0,
    annual_div_rate:  parseFloat(document.getElementById('ef-annual-div-rate').value) || 0,
    qty,
    avg_price: avg,
  };

  if (rec.id) {
    const idx = _etfRecords.findIndex(r => r.id == rec.id);
    if (idx !== -1) _etfRecords[idx] = rec;
    else _etfRecords.push(rec);
  } else {
    rec.id = Math.max(0, ..._etfRecords.map(r => Number(r.id) || 0)) + 1;
    _etfRecords.push(rec);
  }

  await saveEtfRecords();
  closeEtfModal();
  renderEtfCards();
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '저장'; }
  }
}

async function deleteEtf(id) {
  if (!confirm('이 ETF를 삭제하시겠습니까?')) return;
  _etfRecords = _etfRecords.filter(r => r.id != id);
  await saveEtfRecords();
  renderEtfCards();
}

// ══════════════════════════════════════════════════════════
// 거래 내역 (매수/매도)
// ══════════════════════════════════════════════════════════

function calcTxPreview() {
  const qty   = parseInt(document.getElementById('tx-qty').value) || 0;
  const price = parseFloat(document.getElementById('tx-price').value) || 0;
  const type  = document.getElementById('tx-type').value;
  const el    = document.getElementById('tx-preview');
  if (!el) return;
  if (!qty || !price) { el.innerHTML = ''; return; }
  const COMMISSION = 0.00015;
  const STX_TAX    = 0.0015;
  const tradeAmt   = qty * price;
  const commission = Math.round(tradeAmt * COMMISSION);
  const tax        = type === 'sell' ? Math.round(tradeAmt * STX_TAX) : 0;
  const net        = type === 'buy' ? -(tradeAmt + commission) : +(tradeAmt - commission - tax);
  const netSign    = net >= 0 ? '+' : '';
  const taxLine    = type === 'sell' ? `<span style="color:var(--muted)"> · 거래세 ${tax.toLocaleString()}원</span>` : '';
  el.innerHTML = `거래금 <b>${tradeAmt.toLocaleString()}원</b> · 수수료 ${commission.toLocaleString()}원${taxLine}<br>
    예수금 변동: <b style="color:${net>=0?'var(--green)':'var(--red)'}">${netSign}${net.toLocaleString()}원</b>`;
}

function openTransModal(etfId) {
  const r = _etfRecords.find(x => x.id == etfId);
  if (!r) return;
  document.getElementById('tx-etf-id').value     = r.id;
  document.getElementById('tx-etf-name').textContent = r.name + (r.ticker ? ` (${r.ticker})` : '');
  document.getElementById('tx-date').value        = new Date().toISOString().slice(0, 10);
  document.getElementById('tx-type').value        = 'buy';
  document.getElementById('tx-qty').value         = '';
  document.getElementById('tx-price').value       = '';
  document.getElementById('tx-note').value        = '';
  document.getElementById('tx-preview').innerHTML = '';
  document.getElementById('trans-modal').style.display = 'block';
}

function closeTransModal() {
  document.getElementById('trans-modal').style.display = 'none';
}

async function saveTransaction() {
  const etfId = document.getElementById('tx-etf-id').value;
  const date  = document.getElementById('tx-date').value;
  const type  = document.getElementById('tx-type').value;
  const qty   = parseInt(document.getElementById('tx-qty').value) || 0;
  const price = parseFloat(document.getElementById('tx-price').value) || 0;
  const note  = document.getElementById('tx-note').value.trim();

  if (!date) { alert('거래일을 입력하세요'); return; }
  if (!qty)  { alert('수량을 입력하세요'); return; }

  const etf = _etfRecords.find(x => x.id == etfId);
  const record = {
    id:         Date.now(),
    etf_id:     Number(etfId),
    ticker:     etf?.ticker || '',
    name:       etf?.name   || '',
    date, type,
    qty_change: qty,
    price:      price || null,
    note,
  };

  const btn = document.getElementById('tx-save-btn');
  btn.disabled = true; btn.textContent = '저장 중...';
  try {
    const resp = await fetch('/api/transactions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record }),
    });
    if (!resp.ok) throw new Error('저장 실패');
    _transactions.push(record);

    // ETF 레코드 qty · avg_price 업데이트 (합산에 반영)
    const delta = type === 'buy' ? qty : -qty;
    const etfIdx = _etfRecords.findIndex(r => r.id == Number(etfId));
    if (etfIdx !== -1) {
      const rec     = _etfRecords[etfIdx];
      const oldQty  = rec.qty || 0;
      const oldAvg  = rec.avg_price || 0;
      const newQty  = Math.max(0, oldQty + delta);
      let   newAvg  = oldAvg;
      // 매수이고 단가가 입력된 경우에만 가중평균 재계산
      if (type === 'buy' && price > 0 && newQty > 0) {
        newAvg = Math.round((oldQty * oldAvg + qty * price) / newQty);
      }
      _etfRecords[etfIdx] = { ...rec, qty: newQty, avg_price: newAvg };
      // 포트폴리오 탭 _portEtf 동기화 (Portfolio 탭 방문 전이면 undefined)
      if (typeof _portEtf !== 'undefined' && Array.isArray(_portEtf)) {
        const portIdx = _portEtf.findIndex(r => r.id == Number(etfId));
        if (portIdx !== -1) _portEtf[portIdx] = { ..._portEtf[portIdx], qty: newQty, avg_price: newAvg };
      }
      await saveEtfRecords();
    }

    // 예수금 자동 조정 (KB증권 수수료 0.015% + 매도 증권거래세 0.15%)
    if (price > 0) {
      const COMMISSION = 0.00015;
      const STX_TAX    = 0.0015;
      const tradeAmt   = qty * price;
      const commission = Math.round(tradeAmt * COMMISSION);
      const tax        = type === 'sell' ? Math.round(tradeAmt * STX_TAX) : 0;
      const cashDelta  = type === 'buy' ? -(tradeAmt + commission) : +(tradeAmt - commission - tax);
      await _adjustCash(cashDelta);
    }

    renderEtfCards();
    renderPortfolio();
    closeTransModal();
  } catch (e) {
    alert('저장 중 오류: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '저장';
  }
}

async function deleteTransaction(id) {
  if (!confirm('이 거래 내역을 삭제하시겠습니까?')) return;
  try {
    const tx = _transactions.find(t => t.id == id);
    await fetch(`/api/transactions?id=${id}`, { method: 'DELETE' });
    _transactions = _transactions.filter(t => t.id != id);

    // ETF 레코드 qty · avg_price 원상복구
    if (tx) {
      const delta   = tx.type === 'buy' ? -tx.qty_change : tx.qty_change;
      const etfIdx  = _etfRecords.findIndex(r => r.id == tx.etf_id);
      if (etfIdx !== -1) {
        const rec    = _etfRecords[etfIdx];
        const curQty = rec.qty || 0;
        const curAvg = rec.avg_price || 0;
        const newQty = Math.max(0, curQty + delta);
        let   newAvg = curAvg;
        // 매수 거래 삭제이고 단가가 있으면 가중평균 역산
        if (tx.type === 'buy' && tx.price && newQty > 0) {
          newAvg = Math.round((curQty * curAvg - tx.qty_change * tx.price) / newQty);
          if (newAvg < 0) newAvg = curAvg; // 음수 방어
        }
        _etfRecords[etfIdx] = { ...rec, qty: newQty, avg_price: newAvg };
        if (typeof _portEtf !== 'undefined' && Array.isArray(_portEtf)) {
          const portIdx = _portEtf.findIndex(r => r.id == tx.etf_id);
          if (portIdx !== -1) _portEtf[portIdx] = { ..._portEtf[portIdx], qty: newQty, avg_price: newAvg };
        }
        await saveEtfRecords();
      }
      // 예수금 자동 복구 (KB증권 수수료 0.015% + 매도 증권거래세 0.15% 역산)
      if (tx.price) {
        const COMMISSION = 0.00015;
        const STX_TAX    = 0.0015;
        const tradeAmt   = tx.qty_change * tx.price;
        const commission = Math.round(tradeAmt * COMMISSION);
        const tax        = tx.type === 'sell' ? Math.round(tradeAmt * STX_TAX) : 0;
        // 매수 삭제 → 당시 (매수금 + 수수료)를 되돌려줌
        // 매도 삭제 → 당시 (매도금 - 수수료 - 거래세)를 회수
        const cashDelta = tx.type === 'buy' ? +(tradeAmt + commission) : -(tradeAmt - commission - tax);
        await _adjustCash(cashDelta);
      }
    }

    renderEtfCards();
    renderPortfolio();
  } catch (e) {
    alert('삭제 중 오류: ' + e.message);
  }
}

// 예수금 조정 헬퍼 — in-memory 캐시 기준으로 delta 적용 후 저장 + UI 갱신
async function _adjustCash(delta) {
  if (!delta) return;
  try {
    // _portCash 또는 _sharedGistData 캐시에서 현재값 취득 (서버 GET 생략 → ~500ms 단축)
    const baseCash = _portCash || (_sharedGistData?.portfolio_meta?.cash ?? 0);
    const newCash  = Math.max(0, baseCash + delta);
    const saveRes  = await fetch('/api/data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cash: newCash }),
    });
    if (!saveRes.ok) throw new Error(`예수금 저장 실패 (${saveRes.status})`);
    _portCash = newCash;
    // 5분 캐시(_sharedGistData)도 동기화해 탭 재진입 시 구 값으로 덮어씌워지지 않도록
    if (_sharedGistData) {
      if (!_sharedGistData.portfolio_meta) _sharedGistData.portfolio_meta = {};
      _sharedGistData.portfolio_meta.cash = newCash;
    }
    const ci = document.getElementById('cash-input');
    if (ci) ci.value = newCash ? newCash.toLocaleString() : '0';
    if (typeof _renderPortAsset === 'function') _renderPortAsset();
    if (typeof _renderPortKpi   === 'function') _renderPortKpi();
  } catch(e) {
    alert(`⚠️ 예수금 자동 조정 실패: ${e.message}\n거래는 저장되었으니 예수금을 수동으로 업데이트해 주세요.`);
  }
}

// ══════════════════════════════════════════════════════════
// 배당 일정 자동조회 → 보유수량 계산 → 배당 적재
// ══════════════════════════════════════════════════════════

let _dsSelected = null; // 현재 선택된 배당 일정 항목

/**
 * 특정 날짜(targetDate) 기준 ETF 보유 수량 계산
 * 공식: 현재qty - sum(targetDate 이후 거래의 qty_delta)
 */
function calcQtyAtDate(etfId, targetDate) {
  const etf = _etfRecords.find(r => r.id == etfId);
  if (!etf) return 0;
  const laterDelta = _transactions
    .filter(t => t.etf_id == etfId && t.date > targetDate)
    .reduce((s, t) => s + (t.type === 'buy' ? +t.qty_change : -t.qty_change), 0);
  return Math.max(0, (etf.qty || 0) - laterDelta);
}

async function openDivScheduleModal(etfId) {
  const r = _etfRecords.find(x => x.id == etfId);
  if (!r) return;

  _dsSelected = null;
  document.getElementById('ds-etf-id').value      = r.id;
  document.getElementById('ds-etf-name').textContent = r.name + (r.ticker ? ` (${r.ticker})` : '');
  document.getElementById('ds-note').value        = '';
  document.getElementById('ds-loading').style.display  = 'block';
  document.getElementById('ds-error').style.display    = 'none';
  document.getElementById('ds-items').style.display    = 'none';
  document.getElementById('ds-result').style.display   = 'none';
  document.getElementById('ds-save-btn').disabled      = true;
  document.getElementById('ds-save-btn').style.opacity = '.5';
  document.getElementById('div-sched-modal').style.display = 'block';

  if (!r.ticker) {
    showDsError('티커 정보가 없습니다. ETF 정보에서 티커를 먼저 등록해주세요.');
    return;
  }

  try {
    const resp = await fetch(`/api/dividend-schedule?ticker=${r.ticker}`);
    const data = await resp.json();
    if (!resp.ok || !data.items?.length) throw new Error(data.error || '일정 없음');
    renderDsItems(etfId, data.items);
  } catch (e) {
    showDsError(e.message || '배당 일정을 가져오지 못했습니다.');
  }
}

function showDsError(msg) {
  document.getElementById('ds-loading').style.display  = 'none';
  document.getElementById('ds-error-msg').textContent  = msg;
  document.getElementById('ds-error').style.display    = 'block';
}

function renderDsItems(etfId, items) {
  document.getElementById('ds-loading').style.display = 'none';
  document.getElementById('ds-items').style.display   = 'block';

  const container = document.getElementById('ds-item-list');
  container.innerHTML = items.map((item, idx) => {
    const qty = calcQtyAtDate(etfId, item.ex_date);
    const gross = Math.round(item.per_share * qty);
    const net   = Math.round(gross * (1 - TAX_RATE));
    return `
    <label style="display:flex;align-items:flex-start;gap:10px;padding:10px 14px;border:1.5px solid var(--border);border-radius:8px;cursor:pointer;transition:border-color .15s" id="ds-label-${idx}">
      <input type="radio" name="ds-item" value="${idx}" onchange="selectDsItem(${idx})" style="margin-top:2px;accent-color:var(--primary)">
      <div style="flex:1">
        <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px">
          <span style="color:var(--muted)">기준일 <b style="color:var(--text)">${item.ex_date}</b></span>
          <span style="color:var(--muted)">지급일 <b style="color:var(--text)">${item.pay_date}</b></span>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:13px">
          <span>주당 <b style="color:var(--primary)">${item.per_share.toLocaleString()}원</b> × ${qty.toLocaleString()}주</span>
          <span style="color:#16a34a;font-weight:700">세후 ${net.toLocaleString()}원</span>
        </div>
      </div>
    </label>`;
  }).join('');

  // 데이터로 저장 (qty 포함)
  container._items = items.map(item => {
    const qty = calcQtyAtDate(etfId, item.ex_date);
    return { ...item, qty };
  });
}

function selectDsItem(idx) {
  const container = document.getElementById('ds-item-list');
  const items = container._items;
  if (!items?.[idx]) return;
  _dsSelected = items[idx];

  // 선택 테두리 강조
  container.querySelectorAll('label').forEach((el, i) => {
    el.style.borderColor = i === idx ? 'var(--primary)' : 'var(--border)';
    el.style.background  = i === idx ? 'var(--secondary)' : '';
  });

  // 계산 결과 표시
  const { ex_date, pay_date, per_share, qty } = _dsSelected;
  const gross = Math.round(per_share * qty);
  const net   = Math.round(gross * (1 - TAX_RATE));

  const etfId = document.getElementById('ds-etf-id').value;
  const etf   = _etfRecords.find(r => r.id == etfId);
  const laterTxs = _transactions.filter(t => t.etf_id == etfId && t.date > ex_date);
  let qtyNote = `현재 ${(etf?.qty||0).toLocaleString()}주`;
  if (laterTxs.length) {
    const delta = laterTxs.reduce((s, t) => s + (t.type === 'buy' ? +t.qty_change : -t.qty_change), 0);
    qtyNote += ` — 기준일(${ex_date}) 이후 거래 ${delta >= 0 ? '+' : ''}${delta}주 역산`;
  }

  document.getElementById('ds-r-qty').textContent       = qty.toLocaleString() + '주';
  document.getElementById('ds-r-qty-note').textContent  = qtyNote;
  document.getElementById('ds-r-per-share').textContent = per_share.toLocaleString() + '원';
  document.getElementById('ds-r-gross').textContent     = gross.toLocaleString() + '원';
  document.getElementById('ds-r-net').textContent       = net.toLocaleString() + '원';
  document.getElementById('ds-result').style.display    = 'block';

  const btn = document.getElementById('ds-save-btn');
  btn.disabled = false; btn.style.opacity = '1';
}

function closeDivScheduleModal() {
  document.getElementById('div-sched-modal').style.display = 'none';
  _dsSelected = null;
}

async function saveDivSchedule() {
  if (!_dsSelected) return;
  const etfId  = document.getElementById('ds-etf-id').value;
  const note   = document.getElementById('ds-note').value.trim();
  const { ex_date, pay_date, per_share, qty, ym } = _dsSelected;
  const etf    = _etfRecords.find(r => r.id == etfId);

  if (!qty) {
    alert('배당기준일 기준 보유 수량이 0입니다.\n거래 내역에서 매수 내역을 먼저 추가해주세요.');
    return;
  }

  const gross = Math.round(per_share * qty);
  const net   = Math.round(gross * (1 - TAX_RATE));
  const record = {
    id:        Date.now(),
    etf_id:    Number(etfId),
    ticker:    etf?.ticker || '',
    name:      etf?.name   || '',
    ym,
    per_share,
    qty,
    gross,
    net,
    note: note || `기준일 ${ex_date} / 지급일 ${pay_date}`,
  };

  const btn = document.getElementById('ds-save-btn');
  btn.disabled = true; btn.textContent = '저장 중...';
  try {
    const resp = await fetch('/api/dividend', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record }),
    });
    if (!resp.ok) throw new Error('저장 실패');
    _invalidateBinCache();
    _divRecords.push(record);
    renderDivHistory();
    closeDivScheduleModal();
    alert(`✅ 배당 적재 완료!\n${ym} / ${qty.toLocaleString()}주 × ${per_share}원\n세후 ${net.toLocaleString()}원`);
  } catch (e) {
    alert('저장 중 오류: ' + e.message);
    btn.disabled = false; btn.textContent = '배당 적재'; btn.style.opacity = '1';
  }
}

// ══════════════════════════════════════════════════════════
// 배당 수령 기록
// ══════════════════════════════════════════════════════════
let _divRecords = [];

async function loadDivRecords() {
  try {
    const d = await _fetchBinData();
    _divRecords = d.dividends ?? [];
  } catch {
    _divRecords = [];
  }
  renderDivHistory();
}

let _divNetManuallyEdited = false;  // 세후 입력란을 사용자가 직접 고쳤는지 추적 (아래 calcDivPreview 주석 참고)

function openDivModal(etfId) {
  _divNetManuallyEdited = false;
  // ETF 콤보박스 채우기
  const sel = document.getElementById('dv-etf-select');
  sel.innerHTML = '<option value="">ETF를 선택하세요</option>' +
    [..._etfRecords]
      .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
      .map(r => `<option value="${r.id}">${r.name}${r.ticker ? ' (' + r.ticker + ')' : ''}</option>`)
      .join('');

  // 기준월 = 현재월
  const today = new Date();
  document.getElementById('dv-ym').value =
    `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;

  document.getElementById('dv-note').value = '';
  document.getElementById('dv-gross-input').value = '';
  document.getElementById('dv-net-input').value = '';
  document.getElementById('dv-tax-rate-info').textContent = '';
  document.getElementById('dv-preview').style.display = 'none';

  // ETF 사전 선택 및 기본값 채우기
  if (etfId) sel.value = etfId;
  fillDivDefaults(sel.value);

  document.getElementById('div-modal').style.display = 'block';
}

function onDivEtfChange() {
  fillDivDefaults(document.getElementById('dv-etf-select').value);
}

function fillDivDefaults(etfId) {
  document.getElementById('dv-etf-id').value = etfId || '';

  if (!etfId) {
    document.getElementById('dv-qty').value = '';
    document.getElementById('dv-per-share').value = '';
    document.getElementById('dv-per-share-display').textContent = '-';
    document.getElementById('dv-gross-input').value = '';
    calcDivPreview();
    return;
  }

  const etfRec = _etfRecords.find(x => String(x.id) === String(etfId));
  const qty = etfRec?.qty || 0;

  // 현재 보유수량을 기본값으로 채우되, 사용자가 배당기준일 수량으로 직접 수정 가능
  document.getElementById('dv-qty').value = qty || '';

  calcDivPreview();
}

function closeDivModal() {
  document.getElementById('div-modal').style.display = 'none';
}

function calcDivPreview() {
  const gross = parseFloat(document.getElementById('dv-gross-input').value) || 0;
  const qty   = parseInt(document.getElementById('dv-qty').value) || 0;
  const preview = document.getElementById('dv-preview');

  // 주당 배당금 자동계산
  const perShare = (gross && qty) ? Math.round((gross / qty) * 100) / 100 : 0;
  document.getElementById('dv-per-share').value = perShare || '';
  document.getElementById('dv-per-share-display').textContent = perShare ? perShare.toLocaleString() + '원' : '-';
  document.getElementById('dv-per-share-display').style.color = perShare ? 'var(--text)' : 'var(--muted)';

  if (!gross) { preview.style.display = 'none'; return; }

  const estimated = Math.round(gross * (1 - TAX_RATE));
  document.getElementById('dv-gross-val').textContent       = gross.toLocaleString() + '원';
  document.getElementById('dv-net-estimated').textContent   = estimated.toLocaleString() + '원';

  // 실제 수령액 기본값 세팅 — 세전 금액을 입력하는 동안 매 키 입력마다 이 함수가
  // 다시 실행되는데, 예전엔 "!netInput.value"로 자동입력 여부를 판단해서 세전에
  // "3000"을 입력하면 첫 글자 "3" 입력 시점에 세후가 3으로 자동 채워지고, 그 뒤
  // "0"들을 계속 입력해도 netInput.value가 이미 "3"(비어있지 않음)이라 더 이상
  // 갱신이 안 되는 버그가 있었음(2026-08-20 실측). 사용자가 세후 칸을 "직접"
  // 고쳤는지 여부(_divNetManuallyEdited)로 판단하도록 수정 — 세전을 계속 입력하는
  // 동안은 세후가 계속 따라가고, 사용자가 세후 칸에 직접 타이핑한 뒤부터만 멈춘다.
  const netInput = document.getElementById('dv-net-input');
  if (!_divNetManuallyEdited) netInput.value = estimated;

  updateTaxRateInfo(gross);
  preview.style.display = 'block';
}

function onNetInputChange() {
  _divNetManuallyEdited = true;
  const gross = parseFloat(document.getElementById('dv-gross-input').value) || 0;
  updateTaxRateInfo(gross);
}

function updateTaxRateInfo(gross) {
  const netInput = document.getElementById('dv-net-input');
  const info     = document.getElementById('dv-tax-rate-info');
  const net      = parseFloat(netInput.value) || 0;
  if (!gross || !net) { info.textContent = ''; return; }
  const actualTaxRate = ((gross - net) / gross * 100).toFixed(2);
  const diff = (TAX_RATE * 100 - actualTaxRate).toFixed(2);
  if (Math.abs(diff) < 0.01) {
    info.textContent = '실효세율 15.4% (표준)';
    info.style.color = 'var(--muted)';
  } else {
    info.textContent = `실효세율 ${actualTaxRate}% (표준 대비 ${diff > 0 ? '-' : '+'}${Math.abs(diff)}%p)`;
    info.style.color = '#60a5fa';
  }
}

async function saveDivEntry() {
  const etfId   = document.getElementById('dv-etf-id').value;
  const ym      = document.getElementById('dv-ym').value;
  const gross   = parseFloat(document.getElementById('dv-gross-input').value) || 0;
  const qty     = parseInt(document.getElementById('dv-qty').value) || 0;
  const perShare = (gross && qty) ? Math.round((gross / qty) * 100) / 100 : 0;
  const note    = document.getElementById('dv-note').value.trim();

  const net = parseFloat(document.getElementById('dv-net-input').value) || 0;

  if (!etfId)  { alert('ETF를 선택하세요'); return; }
  if (!ym)     { alert('배당 기준월을 입력하세요'); return; }
  if (!gross)  { alert('수령 금액(세전)을 입력하세요'); return; }
  if (!net)    { alert('실제 수령액(세후)을 입력하세요'); return; }

  const etf = _etfRecords.find(x => x.id == etfId);
  const record = {
    id: Date.now(),
    etf_id: Number(etfId),
    ticker: etf?.ticker || '',
    name:   etf?.name   || '',
    ym,
    per_share: perShare,
    qty,
    gross,
    net,
    note,
  };

  const btn = document.getElementById('dv-save-btn');
  btn.disabled = true; btn.textContent = '저장 중...';
  try {
    const resp = await fetch('/api/dividend', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record }),
    });
    if (!resp.ok) throw new Error('저장 실패');
    _invalidateBinCache();
    _divRecords.push(record);
    // 포트폴리오 탭 _portDiv 동기화 (Portfolio 탭 방문 전이면 undefined일 수 있으므로 방어)
    if (Array.isArray(typeof _portDiv !== 'undefined' && _portDiv)) _portDiv.push(record);
    if (typeof _renderPortKpi   === 'function') _renderPortKpi();
    if (typeof _renderEtfDivChart === 'function') _renderEtfDivChart();
    // 세후 배당금을 예수금에 자동 합산
    await _adjustCash(net);
    const expandState = getDivExpandState();
    renderDivHistory();
    applyDivExpandState(expandState);
    expandDivYm(ym);   // 새로 저장한 연도·월 자동 펼침
    closeDivModal();
  } catch (e) {
    alert('저장 중 오류: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '저장';
  }
}

async function deleteDivRecord(id) {
  if (!confirm('이 배당 수령 기록을 삭제하시겠습니까?')) return;
  try {
    const rec = _divRecords.find(r => String(r.id) === String(id));
    await fetch(`/api/dividend?id=${id}`, { method: 'DELETE' });
    _invalidateBinCache();
    _divRecords = _divRecords.filter(r => r.id != id);
    if (typeof _portDiv !== 'undefined' && Array.isArray(_portDiv)) _portDiv = _portDiv.filter(r => r.id != id);
    if (typeof _renderPortKpi   === 'function') _renderPortKpi();
    if (typeof _renderEtfDivChart === 'function') _renderEtfDivChart();
    // 삭제 시 세후 배당금을 예수금에서 차감
    if (rec?.net) await _adjustCash(-rec.net);
    renderDivHistory();
  } catch (e) {
    alert('삭제 중 오류: ' + e.message);
  }
}

let _divFilterYear = 'all';

function renderDivHistory() {
  const el = id => document.getElementById(id);

  // ── 전체 누적 KPI (필터 무관) ──
  const grossTotal  = _divRecords.reduce((s, r) => s + (r.gross || 0), 0);
  const netTotal    = _divRecords.reduce((s, r) => s + (r.net   || 0), 0);
  const thisYear    = new Date().getFullYear();
  const netThisYear = _divRecords
    .filter(r => r.ym && r.ym.startsWith(String(thisYear)))
    .reduce((s, r) => s + (r.net || 0), 0);
  if (el('div-k-gross'))     el('div-k-gross').textContent     = grossTotal  ? grossTotal.toLocaleString()   + '원' : '-';
  if (el('div-k-net'))       el('div-k-net').textContent       = netTotal    ? netTotal.toLocaleString()     + '원' : '-';
  if (el('div-k-this-year')) el('div-k-this-year').textContent = netThisYear ? netThisYear.toLocaleString() + '원' : '-';

  const list = el('div-hist-list');
  if (!list) return;

  if (!_divRecords.length) {
    list.innerHTML = '<div style="text-align:center;color:var(--muted);padding:32px">수령 내역이 없습니다.<br><small>ETF 카드의 💰 버튼으로 입력하세요.</small></div>';
    renderDivYearTabs([]);
    return;
  }

  // ── 연도 목록 수집 ──
  const years = [...new Set(_divRecords.map(r => (r.ym || '').slice(0, 4)).filter(Boolean))].sort().reverse();
  renderDivYearTabs(years);

  // ── 필터 적용 ──
  const filtered = _divFilterYear === 'all'
    ? [..._divRecords]
    : _divRecords.filter(r => (r.ym || '').startsWith(_divFilterYear));

  filtered.sort((a, b) => (b.ym > a.ym ? 1 : -1));

  // ── 연도 → 월 2단계 그룹핑 ──
  const byYear = {};
  filtered.forEach(r => {
    const yr = (r.ym || 'unknown').slice(0, 4);
    const mo = r.ym || 'unknown';
    if (!byYear[yr]) byYear[yr] = {};
    if (!byYear[yr][mo]) byYear[yr][mo] = [];
    byYear[yr][mo].push(r);
  });

  const fmtW = v => v.toLocaleString() + '원';

  const sortedYears = Object.entries(byYear).sort(([a], [b]) => b.localeCompare(a));
  const latestYr = sortedYears[0]?.[0]; // 가장 최근 연도 → 기본 펼침

  list.innerHTML = sortedYears
    .map(([yr, months]) => {
      const yrGross  = Object.values(months).flat().reduce((s, r) => s + (r.gross || 0), 0);
      const yrNet    = Object.values(months).flat().reduce((s, r) => s + (r.net   || 0), 0);
      const yrOpen   = yr === latestYr; // 최신 연도만 기본 펼침

      const monthBlocks = Object.entries(months)
        .sort(([a], [b]) => b.localeCompare(a))
        .map(([ym, records], mi) => {
          const mGross = records.reduce((s, r) => s + (r.gross || 0), 0);
          const mNet   = records.reduce((s, r) => s + (r.net   || 0), 0);
          const [y, m] = ym.split('-');
          const label  = y && m ? `${parseInt(m)}월` : ym;
          const mOpen  = yrOpen && mi === 0; // 최신 연도의 첫 번째(최신) 월만 기본 펼침

          const rows = records.map(r => `
            <div class="div-record-row">
              <div class="div-record-info">
                <span class="div-record-name">${r.name || '-'}</span>
                <span class="div-record-detail">${r.ticker ? r.ticker + ' · ' : ''}${(r.per_share || 0).toLocaleString()}원/주 × ${(r.qty || 0).toLocaleString()}주${r.note ? ' · ' + r.note : ''}</span>
              </div>
              <div class="div-record-amounts">
                <span class="div-record-net">세후 ${fmtW(r.net || 0)}</span>
                <span class="div-record-gross">세전 ${fmtW(r.gross || 0)}</span>
              </div>
              <button class="div-del-btn" onclick="deleteDivRecord(${r.id})">삭제</button>
            </div>`).join('');

          return `<div class="div-month-group">
            <div class="div-month-header" data-ym="${ym}" onclick="toggleDivMonth(this)">
              <span style="display:flex;align-items:center;gap:6px">
                <span class="div-toggle-icon">${mOpen ? '▼' : '▶'}</span>
                <span>📅 ${label}</span>
                <span style="font-size:10px;color:var(--muted)">(${records.length}건)</span>
              </span>
              <span class="div-month-total">세후 ${fmtW(mNet)} &nbsp;·&nbsp; 세전 ${fmtW(mGross)}</span>
            </div>
            <div class="div-month-body" style="display:${mOpen ? '' : 'none'}">${rows}</div>
          </div>`;
        }).join('');

      return `<div class="div-year-group">
        <div class="div-year-header" data-yr="${yr}" onclick="toggleDivYear(this)">
          <span style="display:flex;align-items:center;gap:8px">
            <span class="div-toggle-icon" style="font-size:11px">${yrOpen ? '▼' : '▶'}</span>
            <span>📆 ${yr}년</span>
          </span>
          <span style="font-size:12px;color:#60a5fa;font-weight:700">
            세후 ${fmtW(yrNet)} <span style="color:var(--muted);font-weight:400;margin-left:4px">/ 세전 ${fmtW(yrGross)}</span>
          </span>
        </div>
        <div class="div-year-body" style="display:${yrOpen ? '' : 'none'}">${monthBlocks}</div>
      </div>`;
    }).join('');
}

function renderDivYearTabs(years) {
  const tabs = document.getElementById('div-year-tabs');
  if (!tabs) return;
  const btn = (val, label) => {
    const active = _divFilterYear === val;
    return `<button onclick="setDivYear('${val}')" style="padding:6px 14px;border-radius:20px;border:1.5px solid ${active ? '#3b82f6' : '#1e2d45'};background:${active ? '#1e3a5f' : 'transparent'};color:${active ? '#60a5fa' : 'var(--muted)'};font-size:12px;font-weight:${active ? '700' : '400'};cursor:pointer">${label}</button>`;
  };
  tabs.innerHTML = btn('all', '전체') + years.map(y => btn(y, y + '년')).join('');
}

function setDivYear(val) {
  _divFilterYear = val;
  renderDivHistory();
}

function toggleDivMonth(header) {
  const body = header.nextElementSibling;
  if (!body) return;
  const collapsed = body.style.display === 'none';
  body.style.display = collapsed ? '' : 'none';
  const icon = header.querySelector('.div-toggle-icon');
  if (icon) icon.textContent = collapsed ? '▼' : '▶';
}

function toggleDivYear(header) {
  const body = header.nextElementSibling;
  if (!body) return;
  const collapsed = body.style.display === 'none';
  body.style.display = collapsed ? '' : 'none';
  const icon = header.querySelector('.div-toggle-icon');
  if (icon) icon.textContent = collapsed ? '▼' : '▶';
}

// 현재 펼쳐진 연도·월 상태 수집
function getDivExpandState() {
  const state = new Set();
  document.querySelectorAll('.div-year-header').forEach(h => {
    if (h.querySelector('.div-toggle-icon')?.textContent === '▼')
      state.add('yr:' + h.dataset.yr);
  });
  document.querySelectorAll('.div-month-header').forEach(h => {
    if (h.querySelector('.div-toggle-icon')?.textContent === '▼')
      state.add('mo:' + h.dataset.ym);
  });
  return state;
}

// 저장된 상태 복원
function applyDivExpandState(state) {
  document.querySelectorAll('.div-year-header').forEach(h => {
    if (state.has('yr:' + h.dataset.yr)) _openDivSection(h);
  });
  document.querySelectorAll('.div-month-header').forEach(h => {
    if (state.has('mo:' + h.dataset.ym)) _openDivSection(h);
  });
}

// 특정 ym의 연도·월 강제 펼침
function expandDivYm(ym) {
  if (!ym) return;
  const yr = ym.slice(0, 4);
  document.querySelectorAll('.div-year-header').forEach(h => {
    if (h.dataset.yr === yr) _openDivSection(h);
  });
  document.querySelectorAll('.div-month-header').forEach(h => {
    if (h.dataset.ym === ym) {
      _openDivSection(h);
      h.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  });
}

function _openDivSection(header) {
  const body = header.nextElementSibling;
  if (body) body.style.display = '';
  const icon = header.querySelector('.div-toggle-icon');
  if (icon) icon.textContent = '▼';
}

function editEtf(id) { openEtfModal(id); }

// ── ETF명 자동완성 ────────────────────────────────────────
let _etfNameTimer = null;
function onEtfNameInput(val) {
  clearTimeout(_etfNameTimer);
  if (val.trim().length < 1) { hideEtfAc(); return; }
  _etfNameTimer = setTimeout(() => fetchEtfAc(val.trim()), 200);
}

async function fetchEtfAc(q) {
  try {
    const r = await fetch(`/api/etf?search=${encodeURIComponent(q)}`);
    const data = await r.json();
    showEtfAc(data.items || []);
  } catch { hideEtfAc(); }
}

function showEtfAc(items) {
  const list = document.getElementById('etf-ac-list');
  if (!list) return;
  if (!items.length) { list.style.display = 'none'; return; }
  list.innerHTML = items.map(item => {
    const safeN = item.name.replace(/'/g,"\\'");
    const safeC = (item.divCycle || '').replace(/'/g,"\\'");
    const badge = item.divCycle
      ? `<span class="etf-ac-ticker" style="background:#f0fdf4;color:#16a34a;margin-left:4px">${item.divCycle}</span>` : '';
    return `<div class="etf-ac-item"
      onmousedown="selectEtfAcItem('${safeN}','${item.ticker}','${safeC}')">
      <span>${item.name}${badge}</span>
      <span class="etf-ac-ticker">${item.ticker}</span>
    </div>`;
  }).join('');
  list.style.display = 'block';
}

function hideEtfAc() {
  setTimeout(() => {
    const list = document.getElementById('etf-ac-list');
    if (list) list.style.display = 'none';
  }, 150);
}

async function selectEtfAcItem(name, ticker, divCycle) {
  document.getElementById('ef-name').value   = name;
  document.getElementById('ef-ticker').value = ticker;
  // 검색 목록에서 알려진 배당주기 즉시 적용 (quote API 응답 전에도 표시)
  if (divCycle) {
    const el = document.getElementById('ef-div-cycle');
    if (el) el.value = divCycle;
  }
  hideEtfAc();
  await fetchAndFillQuote(ticker);
}

// ── 티커 입력 → 종목명 + 현재가 자동 조회 ───────────────
let _etfTickerTimer = null;
function onEtfTickerInput(val) {
  clearTimeout(_etfTickerTimer);
  const t = val.trim().toUpperCase();
  if (!/^[A-Z0-9]{6}$/.test(t)) return;
  _etfTickerTimer = setTimeout(() => fetchAndFillQuote(t), 400);
}

async function onEtfTickerBlur(ticker) {
  const t = ticker.trim().toUpperCase();
  if (/^[A-Z0-9]{6}$/.test(t)) await fetchAndFillQuote(t);
}

// ── 종목명 + 현재가 + 배당정보 자동 채움 ─────────────────
async function fetchAndFillQuote(ticker) {
  const tickerEl = document.getElementById('ef-ticker');
  const nameEl   = document.getElementById('ef-name');
  const priceEl  = document.getElementById('ef-price');
  const chgEl    = document.getElementById('ef-chg');
  if (!ticker || !priceEl) return;

  tickerEl.style.borderColor = 'var(--primary)';
  const prevPh = priceEl.placeholder;
  priceEl.placeholder = '조회 중...';

  try {
    const r = await fetch(`/api/quote?ticker=${ticker}`);
    const d = await r.json();
    if (d.price) {
      // 가격
      priceEl.value = d.price;
      chgEl.value   = d.chgPct ?? '';
      // 종목명 (비어있을 때만)
      if (d.name && !nameEl.value.trim()) nameEl.value = d.name;
      // 배당 정보 자동 채움
      _fillDivFields(d);
      tickerEl.style.borderColor = 'var(--green)';
      calcEtfPreview();
    } else {
      tickerEl.style.borderColor = 'var(--red)';
    }
  } catch {
    tickerEl.style.borderColor = 'var(--red)';
  }
  priceEl.placeholder = prevPh;
  setTimeout(() => { tickerEl.style.borderColor = ''; }, 2000);
}

function calcRecentDivRateFromInput() {
  const recentDiv  = parseFloat(document.getElementById('ef-recent-div').value)  || 0;
  const price      = parseFloat(document.getElementById('ef-price').value)        || 0;
  const rateEl     = document.getElementById('ef-recent-div-rate');
  if (rateEl) rateEl.value = (recentDiv && price) ? (recentDiv / price * 100).toFixed(3) : '';
}

function _fillDivFields(d) {
  const set = (id, val) => { const el = document.getElementById(id); if (el && val != null && val !== '') el.value = val; };
  if (d.divCycle)      set('ef-div-cycle', d.divCycle);
  if (d.divMonths)     set('ef-div-months', d.divMonths);
  // 수동 고정된 경우 recent_div / rate API 값으로 덮어쓰지 않음
  const idVal = document.getElementById('ef-id')?.value;
  const existRec = idVal ? _etfRecords.find(r => String(r.id) === String(idVal)) : null;
  if (!existRec?.recent_div_manual) {
    if (d.recentDiv) { set('ef-recent-div', d.recentDiv); calcRecentDivRateFromInput(); }
  }
  if (d.annualDivRate) set('ef-annual-div-rate', d.annualDivRate);
}

// ── 현재가 일괄 업데이트 ──────────────────────────────────
let _etfLastRefreshTime = null;

function _updateRefreshBtnLabel() {
  const btn = document.getElementById('etf-refresh-btn');
  if (!btn) return;
  if (!_etfLastRefreshTime) { btn.textContent = '⟳ 현재가 업데이트'; return; }
  const diffMin = Math.round((Date.now() - _etfLastRefreshTime) / 60000);
  const nextMin = 20 - diffMin;
  if (nextMin > 0)
    btn.textContent = `⟳ 현재가 업데이트 (${nextMin}분 후 자동갱신)`;
  else
    btn.textContent = '⟳ 현재가 업데이트';
}

let _etfRefreshing = false;
async function refreshAllEtfPrices(silent = false) {
  if (!_etfRecords.length) return;
  if (_etfRefreshing) return;   // 자동/수동 중복 실행 차단
  _etfRefreshing = true;
  const btn = document.getElementById('etf-refresh-btn');
  if (!silent && btn) { btn.textContent = '⟳ 업데이트 중...'; btn.disabled = true; }

  // 수동 업데이트(버튼 클릭): 캐시 무효화하여 강제 갱신
  if (!silent) Object.keys(_etfPriceCache).forEach(k => delete _etfPriceCache[k]);

  const now = Date.now();
  let updated = 0;
  await Promise.all(_etfRecords.map(async r => {
    if (!r.ticker) return;
    // 캐시 유효한 티커는 건너뜀 (자동 갱신 시에만)
    const c = _etfPriceCache[r.ticker];
    if (c && now - c.at < ETF_PRICE_CACHE_TTL) return;
    const release = await _priceSem();
    try {
      const res = await fetch(`/api/quote?ticker=${r.ticker}`);
      const d   = await res.json();
      if (d.price) {
        r.current_price  = d.price;
        r.price_chg_pct  = d.chgPct ?? r.price_chg_pct;
        if (d.divCycle)      r.div_cycle        = d.divCycle;
        if (d.divMonths)     r.div_months       = d.divMonths;
        // 수동 고정된 종목은 recent_div / rate API 값으로 덮어쓰지 않음
        if (!r.recent_div_manual) {
          if (d.recentDiv)     r.recent_div      = d.recentDiv;
          if (d.recentDiv && r.current_price)
            r.recent_div_rate = parseFloat((d.recentDiv / r.current_price * 100).toFixed(3));
        }
        if (d.annualDivRate) r.annual_div_rate  = d.annualDivRate;
        _etfPriceCache[r.ticker] = { price: d.price, chgPct: d.chgPct, at: Date.now() };
        updated++;
      }
    } catch {} finally { release(); }
  }));

  if (updated) {
    _etfLastRefreshTime = Date.now();
    await saveEtfRecords();
    renderEtfCards();
  }
  // updated === 0이면 loadEtfRecords의 _applyEtfPriceCache + renderEtfCards가 이미 처리

  if (!silent && btn) {
    btn.textContent = updated ? `✔ ${updated}개 업데이트됨` : '⟳ 현재가 업데이트';
    btn.disabled = false;
    if (updated) setTimeout(_updateRefreshBtnLabel, 2500);
    else setTimeout(_updateRefreshBtnLabel, 0);
  } else if (updated) {
    setTimeout(_updateRefreshBtnLabel, 0);
  }
  _etfRefreshing = false;
}

// 버튼 라벨 1분마다 갱신 (남은 시간 표시)
setInterval(_updateRefreshBtnLabel, 60000);

// ══════════════════════════════════════════════════════════════════
// 시뮬레이터 공통 — 2025-03 이후 실투자 기록 기반 기본값 계산
// ══════════════════════════════════════════════════════════════════
function _calcSimDefaults() {
  if (!_etfRecords || !_etfRecords.length) return null;
  const totalBuy = _etfRecords.reduce((s, r) => s + (r.qty||0) * (r.avg_price||0), 0);
  if (totalBuy <= 0) return null;

  const START_YM = '2025-04';
  const now = new Date();
  const elapsedMonths = Math.max(1, (now.getFullYear() - 2025) * 12 + (now.getMonth() + 1 - 4));

  // 연배당률: 2025-04 이후 월별 세후배당률 평균 × 12
  // 월별로 합산 → 각 월의 (net / totalBuy) → 평균 → ×12
  const recentDivs = (_divRecords || []).filter(r => (r.ym || '') >= START_YM);
  const monthMap = {};
  recentDivs.forEach(r => { monthMap[r.ym] = (monthMap[r.ym] || 0) + (r.net || 0); });
  const monthYields = Object.values(monthMap).map(net => net / totalBuy);
  let yieldPct = 0;
  if (monthYields.length > 0) {
    const avgMonthly = monthYields.reduce((s, v) => s + v, 0) / monthYields.length;
    yieldPct = avgMonthly * 12 * 100;
  } else {
    // fallback: ETF annual_div_rate 가중평균
    const wr = _etfRecords.reduce((s, r) => s + (r.annual_div_rate||0) * (r.qty||0) * (r.avg_price||0), 0);
    yieldPct = wr / totalBuy;
  }

  // 연 주가 상승률: current_price vs avg_price 연환산 (가중평균)
  const etfWithP = _etfRecords.filter(r => r.current_price && r.avg_price && r.qty);
  let priceGrowPct = 0;
  if (etfWithP.length > 0) {
    const wT = etfWithP.reduce((s, r) => s + (r.qty||0) * (r.avg_price||0), 0);
    const wR = etfWithP.reduce((s, r) => {
      return s + (r.current_price / r.avg_price - 1) * (r.qty||0) * (r.avg_price||0);
    }, 0);
    const totalRet = wT > 0 ? wR / wT : 0;
    priceGrowPct = (Math.pow(1 + totalRet, 12 / elapsedMonths) - 1) * 100;
  }

  // 연 배당상승률: ETF별 per_share 첫→끝 추세 가중평균
  const divByEtf = {};
  (_divRecords || [])
    .filter(r => (r.ym || '') >= START_YM && r.per_share && r.etf_id)
    .forEach(r => { (divByEtf[r.etf_id] = divByEtf[r.etf_id] || []).push({ ym: r.ym, ps: r.per_share }); });
  let dgW = 0, dgSum = 0;
  Object.keys(divByEtf).forEach(id => {
    const recs = divByEtf[id].sort((a, b) => a.ym.localeCompare(b.ym));
    if (recs.length < 2) return;
    const first = recs[0], last = recs[recs.length - 1];
    const [fy, fm] = first.ym.split('-').map(Number);
    const [ly, lm] = last.ym.split('-').map(Number);
    const gap = (ly - fy) * 12 + (lm - fm);
    if (gap < 3 || first.ps <= 0) return;
    const g = (Math.pow(last.ps / first.ps, 12 / gap) - 1) * 100;
    const etf = _etfRecords.find(r => r.id == id);
    const w = etf ? (etf.qty||0) * (etf.avg_price||0) : 1;
    dgW += w; dgSum += g * w;
  });
  const divGrowPct = dgW > 0 ? Math.max(-5, Math.min(20, dgSum / dgW)) : 0;

  return {
    invest:       Math.round(totalBuy / 10000),
    yieldPct:     Math.max(0, yieldPct).toFixed(1),
    priceGrowPct: priceGrowPct.toFixed(1),
    divGrowPct:   divGrowPct.toFixed(1),
  };
}

// ══════════════════════════════════════════════════════════════════
// 월배당 목표 달성 시뮬레이터
// ══════════════════════════════════════════════════════════════════
function openMonthlySim() {
  const d = _calcSimDefaults();
  if (d) {
    document.getElementById('ms-invest').value     = d.invest;
    document.getElementById('ms-yield').value      = d.yieldPct;
    document.getElementById('ms-price-grow').value = d.priceGrowPct;
  }
  document.getElementById('msim-modal').style.display = 'block';
  calcMonthlySim();
}

function closeMonthlySim() {
  document.getElementById('msim-modal').style.display = 'none';
}

function calcMonthlySim() {
  const initInvest  = (parseFloat(document.getElementById('ms-invest').value)      || 0) * 10000;
  const annYield    = (parseFloat(document.getElementById('ms-yield').value)        || 0) / 100;
  const monthlyAdd  = (parseFloat(document.getElementById('ms-monthly-add').value) || 0) * 10000;
  const targetDiv   = (parseFloat(document.getElementById('ms-target').value)      || 200) * 10000;
  const priceGrow   = (parseFloat(document.getElementById('ms-price-grow').value)  || 0)  / 100;
  const doReinvest  = document.getElementById('ms-reinvest').checked;
  const doTax       = document.getElementById('ms-tax').checked;

  if (initInvest <= 0 || annYield <= 0) return;

  const mPriceGrow  = Math.pow(1 + priceGrow, 1/12) - 1;
  const mYieldBase  = annYield / 12;
  const taxRate     = doTax ? (1 - TAX_RATE) : 1;  // 전역 TAX_RATE = 0.154 사용

  let portfolio     = initInvest;   // 포트폴리오 평가액 (주가 상승 반영)
  let costBasis     = initInvest;   // 누적 투자 원금
  let reachMonth    = null;
  let reachPortfolio= 0;
  let reachCostBasis= 0;

  const MAX_MONTHS  = 30 * 12;      // 최대 30년
  const chartDiv    = [];           // 월별 세후 배당
  const yearRows    = [];

  // 현재 월 배당
  const curMonthDiv = initInvest * mYieldBase * taxRate;
  document.getElementById('ms-cur-div').textContent = fmtW(curMonthDiv) + '/월';

  for (let m = 1; m <= MAX_MONTHS; m++) {
    // 1. 월 추가 투자
    portfolio  += monthlyAdd;
    costBasis  += monthlyAdd;

    // 2. 배당 계산 (추가투자 반영 후, 주가상승 전)
    const mDiv = portfolio * mYieldBase * taxRate;

    // 3. 배당 재투자
    if (doReinvest) portfolio += mDiv;

    // 4. 주가 상승
    portfolio  *= (1 + mPriceGrow);

    chartDiv.push(mDiv);

    // 목표 최초 달성
    if (!reachMonth && mDiv >= targetDiv) {
      reachMonth     = m;
      reachPortfolio = portfolio;
      reachCostBasis = costBasis;
    }

    // 연도별 행 (12개월마다)
    if (m % 12 === 0) {
      yearRows.push({ yr: m/12, portfolio, costBasis, mDiv,
        pct: mDiv / targetDiv * 100, reached: mDiv >= targetDiv });
      if (reachMonth && m > reachMonth + 36) break; // 달성 후 3년까지만
    }
  }

  // 요약 배지
  if (reachMonth) {
    const yrs = Math.floor(reachMonth / 12);
    const mos = reachMonth % 12;
    document.getElementById('ms-reach-when').textContent =
      yrs > 0 ? `${yrs}년 ${mos > 0 ? mos+'개월' : ''}` : `${mos}개월`;
    document.getElementById('ms-reach-sub').textContent = `약 ${reachMonth}개월 후`;
    document.getElementById('ms-reach-total-inv').textContent = fmtW(reachCostBasis);
    document.getElementById('ms-reach-portfolio').textContent = fmtW(reachPortfolio);
  } else {
    document.getElementById('ms-reach-when').textContent = '30년 내 미달성';
    document.getElementById('ms-reach-sub').textContent = '투자금 또는 배당률 조정 필요';
    document.getElementById('ms-reach-total-inv').textContent = '-';
    document.getElementById('ms-reach-portfolio').textContent = '-';
  }

  // 테이블
  const tbody = document.getElementById('ms-table-body');
  tbody.innerHTML = yearRows.map(r => `
    <tr style="border-top:1px solid #1e2d45;color:#f1f5f9;${r.reached?'background:#0d1f15':''}">
      <td style="padding:7px 10px;text-align:center;color:${r.reached?'#34d399':'#94a3b8'}">${r.yr}년 후${r.reached?' 🎯':''}</td>
      <td style="padding:7px 10px;text-align:right">${fmtW(r.portfolio)}</td>
      <td style="padding:7px 10px;text-align:right;color:#64748b">${fmtW(r.costBasis)}</td>
      <td style="padding:7px 10px;text-align:right;font-weight:600;color:${r.mDiv>=targetDiv?'#34d399':'#f1f5f9'}">${fmtW(r.mDiv)}</td>
      <td style="padding:7px 10px;text-align:right;color:${r.pct>=100?'#34d399':'#94a3b8'}">${r.pct.toFixed(0)}%</td>
    </tr>`).join('');

  // SVG 차트 (월 단위)
  const svg = document.getElementById('ms-chart');
  const W   = svg.parentElement.offsetWidth || 640;
  const H   = 200;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

  const displayMonths = Math.min(chartDiv.length, (reachMonth ? reachMonth + 36 : MAX_MONTHS));
  const displayData   = chartDiv.slice(0, displayMonths);
  const maxDiv        = Math.max(...displayData, targetDiv) * 1.1;
  const PAD = { left: 50, right: 12, top: 12, bottom: 28 };
  const cw  = W - PAD.left - PAD.right;
  const ch  = H - PAD.top  - PAD.bottom;

  const xPos = (m) => PAD.left + (m / displayMonths) * cw;
  const yPos = (v)  => PAD.top  + ch - (v / maxDiv) * ch;

  // Y 그리드
  let grid = '';
  for (let i = 0; i <= 4; i++) {
    const y = PAD.top + (ch/4)*i;
    const v = maxDiv * (1 - i/4);
    grid += `<line x1="${PAD.left}" y1="${y.toFixed(1)}" x2="${W-PAD.right}" y2="${y.toFixed(1)}" stroke="#1e2d45" stroke-width="1"/>`;
    grid += `<text x="${(PAD.left-4).toFixed(1)}" y="${(y+3).toFixed(1)}" fill="#475569" font-size="9" text-anchor="end">${fmtW(v)}</text>`;
  }

  // X 연도 라벨
  let xLabels = '';
  const step = Math.max(1, Math.ceil(displayMonths / 12 / 6)) * 12;
  for (let m = 0; m <= displayMonths; m += step) {
    xLabels += `<text x="${xPos(m).toFixed(1)}" y="${H-4}" fill="#475569" font-size="9" text-anchor="middle">${m===0?'시작':Math.round(m/12)+'년'}</text>`;
  }

  // 목표선
  const ty = yPos(targetDiv).toFixed(1);
  const targetLine = `
    <line x1="${PAD.left}" y1="${ty}" x2="${W-PAD.right}" y2="${ty}" stroke="#f59e0b" stroke-width="1.5" stroke-dasharray="5 3" opacity="0.8"/>
    <text x="${(W-PAD.right+2).toFixed(1)}" y="${(parseFloat(ty)+3).toFixed(1)}" fill="#f59e0b" font-size="9">목표</text>`;

  // 달성 수직선
  let reachLine = '';
  if (reachMonth && reachMonth <= displayMonths) {
    const rx = xPos(reachMonth).toFixed(1);
    reachLine = `<line x1="${rx}" y1="${PAD.top}" x2="${rx}" y2="${(PAD.top+ch).toFixed(1)}" stroke="#34d399" stroke-width="1.5" stroke-dasharray="3 3" opacity="0.7"/>
    <text x="${rx}" y="${(PAD.top-2).toFixed(1)}" fill="#34d399" font-size="9" text-anchor="middle">달성!</text>`;
  }

  // 월별 배당선
  const path = displayData.map((v,i) => `${i===0?'M':'L'}${xPos(i+1).toFixed(1)},${yPos(v).toFixed(1)}`).join(' ');

  svg.innerHTML = `${grid}${xLabels}${targetLine}${reachLine}
    <path d="M${xPos(0).toFixed(1)},${yPos(initInvest*mYieldBase*taxRate).toFixed(1)} ${path}" fill="none" stroke="#34d399" stroke-width="2"/>`;
}

// ══════════════════════════════════════════════════════════════════
// 배당 재투자 계산기
// ══════════════════════════════════════════════════════════════════

function openDripModal() {
  const d = _calcSimDefaults();
  if (d) {
    document.getElementById('drip-invest').value       = d.invest;
    document.getElementById('drip-yield').value        = d.yieldPct;
    document.getElementById('drip-price-growth').value = d.priceGrowPct;
    document.getElementById('drip-div-growth').value   = d.divGrowPct;
  }
  document.getElementById('drip-modal').style.display = 'block';
  calcDrip();
}

function closeDripModal() {
  document.getElementById('drip-modal').style.display = 'none';
}

function fmtW(val) {
  const abs = Math.abs(val);
  if (abs >= 1e8)  return (val/1e8).toFixed(1) + '억원';
  if (abs >= 1e4)  return Math.round(val/1e4) + '만원';
  return Math.round(val).toLocaleString() + '원';
}

function calcDrip() {
  const invest    = (parseFloat(document.getElementById('drip-invest').value) || 0) * 10000;
  const divYield  = (parseFloat(document.getElementById('drip-yield').value) || 0) / 100;
  const priceGrow = (parseFloat(document.getElementById('drip-price-growth').value) || 0) / 100;
  const divGrow   = (parseFloat(document.getElementById('drip-div-growth').value) || 0) / 100;
  const years     = parseInt(document.getElementById('drip-years').value) || 5;

  if (invest <= 0) return;

  const totalMonths = years * 12;
  const basePrice   = 10000;
  // 월 단위 환산
  const mPriceGrow  = Math.pow(1 + priceGrow, 1/12) - 1;
  const mDivGrow    = Math.pow(1 + divGrow,   1/12) - 1;
  const mDivYield   = divYield / 12;

  let shares   = invest / basePrice;
  let cumulDiv = 0;

  // 월별 데이터 (차트용)
  const chartDrip   = [invest];
  const chartNoRein = [invest];

  // 연도별 집계 (테이블용)
  const yearRows = [];

  let prevShares = shares;
  let yrDiv = 0;

  for (let m = 1; m <= totalMonths; m++) {
    const price     = basePrice * Math.pow(1 + mPriceGrow, m);
    const mYield    = mDivYield * Math.pow(1 + mDivGrow, m - 1);
    const dividend  = shares * price * mYield;
    cumulDiv += dividend;
    yrDiv    += dividend;
    shares   += dividend / price;          // 재투자
    const evalAmt = shares * price;
    chartDrip.push(evalAmt);
    chartNoRein.push((invest / basePrice) * price);

    // 매 12개월마다 연도 행 추가
    if (m % 12 === 0) {
      const yr  = m / 12;
      const ret = (evalAmt / invest - 1) * 100;
      yearRows.push({ yr, shares, yrDiv, cumulDiv, evalAmt, ret });
      yrDiv = 0;
    }
  }

  const finalEval    = chartDrip[totalMonths];
  const noReinvFinal = chartNoRein[totalMonths];
  const totalRet     = (finalEval / invest - 1) * 100;
  const bonus        = finalEval - noReinvFinal;

  // 요약 지표
  document.getElementById('drip-s-invest').textContent = fmtW(invest);
  document.getElementById('drip-s-final').textContent  = fmtW(finalEval);
  document.getElementById('drip-s-return').textContent = (totalRet >= 0 ? '+' : '') + totalRet.toFixed(1) + '%';
  document.getElementById('drip-s-div').textContent    = fmtW(cumulDiv);
  document.getElementById('drip-s-bonus').textContent  = fmtW(bonus);

  // 연도별 테이블
  const tbody = document.getElementById('drip-table-body');
  tbody.innerHTML = yearRows.map(r => `
    <tr style="border-top:1px solid #1e2d45;color:#f1f5f9">
      <td style="padding:8px 10px;text-align:center;color:#94a3b8">${r.yr}년 후</td>
      <td style="padding:8px 10px;text-align:right">${r.shares.toFixed(2)}주</td>
      <td style="padding:8px 10px;text-align:right">${fmtW(r.yrDiv)}</td>
      <td style="padding:8px 10px;text-align:right">${fmtW(r.cumulDiv)}</td>
      <td style="padding:8px 10px;text-align:right;font-weight:600">${fmtW(r.evalAmt)}</td>
      <td style="padding:8px 10px;text-align:right;color:${r.ret>=0?'#34d399':'#f87171'};font-weight:600">${r.ret>=0?'+':''}${r.ret.toFixed(1)}%</td>
    </tr>`).join('');

  // SVG 차트 (월 단위)
  const svg = document.getElementById('drip-chart');
  const W = svg.parentElement.offsetWidth || 600;
  const H = 180;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

  const maxVal = Math.max(...chartDrip, ...chartNoRein);
  const PAD = { left: 46, right: 10, top: 12, bottom: 28 };
  const cw = W - PAD.left - PAD.right;
  const ch = H - PAD.top - PAD.bottom;

  const xPos = (m) => PAD.left + (m / totalMonths) * cw;
  const yPos = (v)  => PAD.top + ch - (v / maxVal) * ch;

  const toPath = (vals) =>
    vals.map((v, i) => `${i===0?'M':'L'}${xPos(i).toFixed(1)},${yPos(v).toFixed(1)}`).join(' ');

  // 그리드 라인 (Y축)
  let gridLines = '';
  for (let i = 0; i <= 4; i++) {
    const y   = PAD.top + (ch / 4) * i;
    const val = maxVal * (1 - i / 4);
    gridLines += `<line x1="${PAD.left}" y1="${y.toFixed(1)}" x2="${W-PAD.right}" y2="${y.toFixed(1)}" stroke="#1e2d45" stroke-width="1"/>`;
    gridLines += `<text x="${(PAD.left-4).toFixed(1)}" y="${(y+3).toFixed(1)}" fill="#475569" font-size="9" text-anchor="end">${fmtW(val)}</text>`;
  }

  // X축 라벨 — 연 단위만 표시
  let xLabels = '';
  for (let yr = 0; yr <= years; yr++) {
    const mx = yr * 12;
    xLabels += `<line x1="${xPos(mx).toFixed(1)}" y1="${(PAD.top+ch).toFixed(1)}" x2="${xPos(mx).toFixed(1)}" y2="${(PAD.top+ch+4).toFixed(1)}" stroke="#1e2d45" stroke-width="1"/>`;
    xLabels += `<text x="${xPos(mx).toFixed(1)}" y="${H-4}" fill="#475569" font-size="9" text-anchor="middle">${yr === 0 ? '시작' : yr + '년'}</text>`;
  }

  // 연도 경계 수직 점선
  let vLines = '';
  for (let yr = 1; yr < years; yr++) {
    const mx = yr * 12;
    vLines += `<line x1="${xPos(mx).toFixed(1)}" y1="${PAD.top}" x2="${xPos(mx).toFixed(1)}" y2="${(PAD.top+ch).toFixed(1)}" stroke="#1e2d45" stroke-width="0.8" stroke-dasharray="3 3"/>`;
  }

  svg.innerHTML = `
    ${gridLines}
    ${vLines}
    ${xLabels}
    <path d="${toPath(chartNoRein)}" fill="none" stroke="#3b82f6" stroke-width="1.5" stroke-dasharray="4 3" opacity="0.6"/>
    <path d="${toPath(chartDrip)}"   fill="none" stroke="#34d399" stroke-width="2"/>
    ${yearRows.map(r => {
      const mx = r.yr * 12;
      return `<circle cx="${xPos(mx).toFixed(1)}" cy="${yPos(r.evalAmt).toFixed(1)}" r="3.5" fill="#34d399"/>`;
    }).join('')}
  `;
}

// ══════════════════════════════════════════════════════════════════
// 시장현황 탭
// ══════════════════════════════════════════════════════════════════
function _mktCard(id, name, priceStr, chg, chgPct, extraHtml = '') {
  const up  = chg >= 0;
  const sign = up ? '+' : '';
  const cls  = up ? 'mkt-chg-up' : 'mkt-chg-dn';
  return `
    <div class="mkt-name">${name}</div>
    <div class="mkt-price">${priceStr}</div>
    <div class="${cls}">${sign}${chg.toFixed(chg > 100 ? 0 : 2)} (${sign}${chgPct.toFixed(2)}%)</div>
    ${extraHtml}`;
}

function _mktSparkline(data) {
  const W = 240, H = 48, PAD = 2;
  const vals = data.filter(v => !isNaN(v) && v > 0);
  if (vals.length < 2) return '';
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = max - min || 1;
  const sx = (i) => (i / (vals.length - 1)) * (W - PAD * 2) + PAD;
  const sy = (v) => H - PAD - ((v - min) / range) * (H - PAD * 2);
  const pts = vals.map((v, i) => `${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ');
  const lx = sx(vals.length - 1), ly = sy(vals[vals.length - 1]);
  const trend = vals[vals.length - 1] >= vals[0];
  const col = trend ? '#f87171' : '#34d399'; // 환율 상승=위험(빨강), 하락=긍정(초록)
  const startDate = '', endDate = '';
  return `<div style="margin-top:10px;border-top:1px solid #1e293b;padding-top:8px">
    <div style="font-size:10px;color:#475569;margin-bottom:4px">최근 60일 추이</div>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;overflow:visible">
      <polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.5" stroke-linejoin="round" opacity="0.9"/>
      <circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="3" fill="${col}"/>
      <text x="${PAD}" y="${H - PAD - 1}" fill="#475569" font-size="9">${min.toFixed(1)}</text>
      <text x="${PAD}" y="${PAD + 8}" fill="#475569" font-size="9">${max.toFixed(1)}</text>
    </svg>
  </div>`;
}

function _fgLabel(v) {
  if (v <= 25) return { label: '극단적 공포', color: '#f87171' };
  if (v <= 45) return { label: '공포', color: '#fb923c' };
  if (v <= 55) return { label: '중립', color: '#facc15' };
  if (v <= 75) return { label: '탐욕', color: '#4ade80' };
  return { label: '극단적 탐욕', color: '#34d399' };
}

