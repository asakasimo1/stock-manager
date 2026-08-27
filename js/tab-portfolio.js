async function initPortfolio() {
  _initPortCollapse();
  // 번들 캐시가 없을 때만 로딩 스피너 표시 (캐시 워밍 시 즉시 렌더 가능 → 깜빡임 방지)
  if (!_binData) _showPortLoading();
  const gen = ++_portGeneration; // 이 호출의 세대 번호 — 이후 구 refresh가 덮어쓰지 못하도록
  _portRefreshing = false; // 이전 refresh 플래그 초기화 — 새 방문에서 반드시 실행되도록
  // 항상 최신 데이터로 갱신 (TDZ 에러 방지 + 타 탭 변경사항 반영)
  try {
    // /api/ipo · /api/data는 전역 캐시(_ipoRecords, _sharedGistData) 재사용해 중복 호출 방지
    const ipoPromise = _ipoRecords.length > 0
      ? Promise.resolve({ records: _ipoRecords })
      : fetch('/api/ipo').then(r => r.json());
    const [bundleData, ipoRes, metaRes] = await Promise.all([
      _fetchBinData(),
      ipoPromise,
      _fetchGistData(),
    ]);
    if (gen !== _portGeneration) return; // 더 새로운 initPortfolio()가 이미 실행됨
    _portEtf      = bundleData.etf       ?? [];
    _portIpo      = ipoRes.records       || [];
    _portDiv      = bundleData.dividends ?? [];
    _stockRecords = (bundleData.stocks   ?? []).map(r => ({ ...r, current_price: null, chg: null, chgPct: null }));
    _portCash     = (metaRes.portfolio_meta || {}).cash || 0;
    const ci = document.getElementById('cash-input');
    if (ci) ci.value = _portCash ? _portCash.toLocaleString() : '';
    // 캐시된 현재가 즉시 적용 → Phase 1 렌더에서 avg_price 대신 사용
    _applyPriceCache();
  } catch(e) {
    console.error('포트폴리오 데이터 로드 실패:', e);
  }
  // Phase 1: 캐시 가격(또는 avg_price)으로 즉시 렌더 — 사용자 대기 없음
  if (gen === _portGeneration) renderPortfolio();
  // Phase 2: 실시간 가격 조회 후 재렌더
  await _refreshPortfolioRealtime(gen);
  if (gen === _portGeneration) renderPortfolio();
  _startPortAutoRefresh();
}

// 포트폴리오 탭 — 현재가 TTL 캐시 (3분)
const _portPriceCache = {};
const _PORT_PRICE_TTL = 3 * 60 * 1000;

function _applyPriceCache() {
  const now = Date.now();
  _portEtf.forEach(r => {
    const c = r.ticker && _portPriceCache[r.ticker];
    if (c && now - c.at < _PORT_PRICE_TTL) { r.current_price = c.price; r.chg = c.chg; r.chgPct = c.chgPct; }
  });
  _stockRecords.forEach(r => {
    const c = r.ticker && _portPriceCache[r.ticker];
    if (c && now - c.at < _PORT_PRICE_TTL) { r.current_price = c.price; r.chg = c.chg; r.chgPct = c.chgPct; }
  });
}

// 포트폴리오 탭 — 실시간 현재가 일괄 조회 (Gist 저장 없이 화면만 갱신)
let _portRefreshing = false;
async function _refreshPortfolioRealtime(gen) {
  if (_portRefreshing) return;   // 중복 실행 차단
  _portRefreshing = true;
  try {
    const now = Date.now();
    const etfSnap = [..._portEtf];      // 이 시점의 배열 스냅샷 — 교체돼도 구 fetch 결과 무시
    const stkSnap = [..._stockRecords];
    const etfFetches = etfSnap
      .filter(r => r.ticker)
      .map(async r => {
        const c = _portPriceCache[r.ticker];
        if (c && now - c.at < _PORT_PRICE_TTL) return; // 캐시 유효 → 건너뜀
        const release = await _priceSem();
        try {
          const d = await fetch(`/api/quote?ticker=${r.ticker}`).then(x => x.json());
          if (d.price) {
            r.current_price = d.price; r.chg = d.chg ?? null; r.chgPct = d.chgPct ?? null;
            _portPriceCache[r.ticker] = { price: d.price, chg: d.chg ?? null, chgPct: d.chgPct ?? null, at: Date.now() };
          }
        } catch {} finally { release(); }
      });

    const stkFetches = stkSnap
      .filter(r => r.ticker)
      .map(async r => {
        const c = _portPriceCache[r.ticker];
        if (c && now - c.at < _PORT_PRICE_TTL) return; // 캐시 유효 → 건너뜀
        const release = await _priceSem();
        try {
          const d = await fetch(`/api/stock?ticker=${r.ticker}`).then(x => x.json());
          if (d.price) {
            r.current_price = d.price; r.chg = d.chg ?? null; r.chgPct = d.chgPct ?? null;
            _portPriceCache[r.ticker] = { price: d.price, chg: d.chg ?? null, chgPct: d.chgPct ?? null, at: Date.now() };
          }
        } catch {} finally { release(); }
      });

    await Promise.all([...etfFetches, ...stkFetches]);
  } finally {
    _portRefreshing = false;
  }
}

function _startPortAutoRefresh() {
  clearInterval(_portAutoTimer);
  _portAutoTimer = setInterval(() => {
    const tab = document.getElementById('tab-portfolio');
    if (tab && tab.classList.contains('active')) initPortfolio(true);
  }, PORT_REFRESH_MS);
}

function renderPortfolio() {
  _renderPortKpi();
  _renderEtfDivChart();
  _renderPortEtf();
  _renderPortIpo();
  _renderPortStock();
}

// ── 금액 포맷 (자산현황·수익배분 도넛 차트용: 만원 단위) ─────
const fmtK = v => {
  const abs = Math.abs(v), sign = v < 0 ? '-' : '';
  if (abs >= 1e8) {
    const uk = Math.floor(abs / 1e8);
    const man = Math.round((abs % 1e8) / 1e4);
    return sign + uk + '억' + (man > 0 ? ' ' + man.toLocaleString() + '만원' : '원');
  }
  if (abs >= 1e4) return sign + Math.round(abs / 1e4).toLocaleString() + '만원';
  return sign + Math.round(abs).toLocaleString() + '원';
};
const signFmt = v => (v > 0 ? '+' : '') + fmtK(v);

// ── KPI ────────────────────────────────────────────────────
function _renderPortKpi() {
  const colorVal = (el, val) => { el.style.color = val > 0 ? 'var(--green)' : val < 0 ? 'var(--red)' : 'var(--primary)'; };

  // 개별주 평가손익 (현재가 있는 것만, 테이블 합계와 동일 기준)
  const stkProfit = _stockRecords.reduce((s, r) => {
    if (!r.current_price) return s;
    return s + (r.current_price - (r.avg_price||0)) * (r.qty||0);
  }, 0);
  const stkEl = document.getElementById('pk-stk-eval');
  const wSign = v => (v > 0 ? '+' : '') + Math.round(v).toLocaleString() + '원';
  stkEl.textContent = _stockRecords.some(r => r.current_price) ? wSign(stkProfit) : '-';
  colorVal(stkEl, stkProfit);

  // ETF 평가손익 (현재가 있는 것만)
  const etfProfit = _portEtf.reduce((s, r) => {
    if (!r.current_price) return s;
    return s + (r.current_price - (r.avg_price||0)) * (r.qty||0);
  }, 0);
  const etfEl = document.getElementById('pk-etf-eval');
  etfEl.textContent = _portEtf.some(r => r.current_price) ? wSign(etfProfit) : '-';
  colorVal(etfEl, etfProfit);

  // 공모주 누적 수익 (매도 완료된 것만 — direct_profit 우선)
  const ipoProfit = _portIpo
    .filter(r => r.direct_profit != null || (r.price_open > 0 && r.price_ipo > 0))
    .reduce((s, r) => s + (r.direct_profit != null
      ? r.direct_profit
      : (r.price_open - r.price_ipo) * (r.sell_qty || r.shares_alloc || 0) - 2000), 0);
  const ipoEl = document.getElementById('pk-ipo-profit');
  const hasIpoData = _portIpo.some(r => r.direct_profit != null || r.price_open > 0);
  ipoEl.textContent = hasIpoData ? wSign(ipoProfit) : '-';
  colorVal(ipoEl, ipoProfit);

  // 세후 배당 누적
  const divTotal = _portDiv.reduce((s, r) => s + (r.net || 0), 0);
  const divEl = document.getElementById('pk-div-total');
  divEl.textContent = divTotal ? Math.round(divTotal).toLocaleString() + '원' : '-';
  colorVal(divEl, divTotal);

  // 차트
  _renderPortAsset(etfProfit, stkProfit);
  _renderIpoDivByStock();
}

// ── ETF 보유현황 ───────────────────────────────────────────
function _renderPortEtf() {
  const tbody = document.getElementById('port-etf-tbody');
  if (!_portEtf.length) { tbody.innerHTML = '<tr><td colspan="4" class="port-loading">ETF 데이터 없음 (ETF 탭에서 먼저 등록하세요)</td></tr>'; return; }

  let totalBuy = 0, totalEval = 0, totalProfit = 0, totalDayPnl = 0, hasDayPnl = false;
  const rows = _portEtf.map(r => {
    const avg     = r.avg_price || 0;
    const cur     = r.current_price || 0;
    const qty     = r.qty || 0;
    const buy     = avg * qty;
    const eval_   = cur * qty;
    const profit  = eval_ - buy;
    const rate    = buy > 0 ? profit / buy * 100 : null;
    const rc      = rate === null ? '' : rate >= 0 ? 'up' : 'dn';
    const dayPnl  = r.chg != null ? r.chg * qty : null;
    const prev    = cur - (r.chg || 0);
    const dayRate = (r.chg != null && prev > 0) ? r.chg / prev * 100 : null;
    const drc     = dayPnl === null ? '' : dayPnl >= 0 ? 'up' : 'dn';
    totalBuy += buy; totalEval += eval_; totalProfit += profit;
    if (dayPnl !== null) { totalDayPnl += dayPnl; hasDayPnl = true; }
    const rateStr   = rate    !== null ? `${profit>=0?'+':''}${Math.round(profit).toLocaleString()}원<br>(${rate>=0?'+':''}${rate.toFixed(2)}%)` : '-';
    const dayStr    = dayPnl  !== null
      ? (dayRate !== null
          ? `${dayPnl>=0?'+':''}${Math.round(dayPnl).toLocaleString()}원<br>(${dayRate>=0?'+':''}${dayRate.toFixed(2)}%)`
          : `${dayPnl>=0?'+':''}${Math.round(dayPnl).toLocaleString()}원`)
      : '-';
    return `<tr>
      <td>${r.name||r.ticker||'-'}</td>
      <td class="${rc}">${rateStr}</td>
      <td class="${drc}">${dayStr}</td>
      <td>${eval_ ? eval_.toLocaleString()+'원' : '-'}</td>
    </tr>`;
  }).join('');
  const totalRate    = totalBuy > 0 ? (totalProfit / totalBuy * 100) : null;
  const prevEval     = totalEval - totalDayPnl;
  const totalDayRate = hasDayPnl && prevEval > 0 ? totalDayPnl / prevEval * 100 : null;
  const trc  = totalProfit >= 0 ? 'up' : 'dn';
  const dtrc = totalDayPnl >= 0 ? 'up' : 'dn';
  const totalRateStr = totalRate !== null
    ? `${totalProfit>=0?'+':''}${Math.round(totalProfit).toLocaleString()}원<br>(${totalRate>=0?'+':''}${totalRate.toFixed(2)}%)`
    : '-';
  const totalDayStr  = hasDayPnl
    ? (totalDayRate !== null
        ? `${totalDayPnl>=0?'+':''}${Math.round(totalDayPnl).toLocaleString()}원<br>(${totalDayRate>=0?'+':''}${totalDayRate.toFixed(2)}%)`
        : `${totalDayPnl>=0?'+':''}${Math.round(totalDayPnl).toLocaleString()}원`)
    : '-';
  tbody.innerHTML = rows + `<tr style="border-top:2px solid var(--border);font-weight:700;background:var(--bg)">
    <td>합계</td>
    <td class="${trc}">${totalRateStr}</td>
    <td class="${dtrc}">${totalDayStr}</td>
    <td>${totalEval ? totalEval.toLocaleString()+'원' : '-'}</td>
  </tr>`;
}

// ── 공모주 수익현황 (데이터 있을 때만 카드 표시) ────────────
function _renderPortIpo() {
  const card  = document.getElementById('port-ipo-card');
  const tbody = document.getElementById('port-ipo-tbody');
  // direct_profit(직접 입력) 또는 price_open+price_ipo 계산 둘 다 표시, 매도일 최신순 정렬
  const sold  = _portIpo
    .filter(r => r.direct_profit != null || (r.price_open > 0 && r.price_ipo > 0))
    .sort((a, b) => (b.sell_date || '').localeCompare(a.sell_date || ''));
  if (!sold.length) { if (card) card.style.display = 'none'; return; }
  if (card) card.style.display = '';
  let totalProfit = 0;
  const rows = sold.map(r => {
    const profit = r.direct_profit != null
      ? r.direct_profit
      : (r.price_open - r.price_ipo) * (r.sell_qty || r.shares_alloc || 0) - 2000;
    const rate = r.direct_rate != null
      ? Number(r.direct_rate).toFixed(2)
      : ((r.price_open - r.price_ipo) / r.price_ipo * 100).toFixed(2);
    totalProfit += profit;
    const pc      = profit >= 0 ? 'up' : 'dn';
    const rateStr = `${profit>=0?'+':''}${profit.toLocaleString()}원<br>(${Number(rate)>=0?'+':''}${rate}%)`;
    const dateStr = r.sell_date ? r.sell_date.slice(2).replace(/-/g, '.') : '-';
    return `<tr>
      <td>${r.name||'-'}</td>
      <td class="${pc}">${rateStr}</td>
      <td>${profit>=0?'+':''}${profit.toLocaleString()}원</td>
      <td style="color:var(--muted);font-size:11px">${dateStr}</td>
    </tr>`;
  }).join('');
  const tc = totalProfit >= 0 ? 'up' : 'dn';
  tbody.innerHTML = rows;

  // 합계 — 접힌 상태에서도 항상 보이도록 tbody 밖 별도 요소에 렌더링
  const summary = document.getElementById('port-ipo-summary');
  if (summary) {
    summary.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-top:1px solid var(--border);font-weight:700;font-size:13px">
      <span>합계 <span style="font-weight:400;color:var(--muted);font-size:11px">(${sold.length}건)</span></span>
      <span class="${tc}">${totalProfit>=0?'+':''}${totalProfit.toLocaleString()}원</span>
    </div>`;
  }
}

async function clearIpoSale(id) {
  if (!confirm('이 공모주의 매도 기록을 초기화하시겠습니까?\n(종목은 유지되고 매도가·매도수량만 삭제됩니다)')) return;
  const rec = _portIpo.find(r => r.id == id);
  if (!rec) return;
  rec.price_open = 0;
  rec.sell_qty   = null;
  rec.sell_date  = null;
  try {
    const res = await fetch('/api/ipo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ records: _portIpo }),
    });
    if (!(await res.json()).ok) throw new Error('저장 실패');
    await initPortfolio();
  } catch(e) { alert('오류: ' + e.message); }
}

// ── 개별주 보유현황 ────────────────────────────────────────
function _renderPortStock() {
  const tbody = document.getElementById('port-stock-tbody');
  if (!_stockRecords.length) { tbody.innerHTML = '<tr><td colspan="4" class="port-loading">개별주 없음 · 개별주 탭에서 추가하세요</td></tr>'; return; }
  let sTotalBuy = 0, sTotalEval = 0, sTotalProfit = 0, sHasCur = false, sTotalDayPnl = 0, sHasDayPnl = false;
  const sRows = _stockRecords.map(r => {
    const buy     = (r.qty||0) * (r.avg_price||0);
    const eval_   = (r.qty||0) * (r.current_price||0);
    const profit  = eval_ - buy;
    const rate    = buy > 0 && r.current_price ? profit / buy * 100 : null;
    const rc      = rate === null ? '' : rate >= 0 ? 'up' : 'dn';
    const dayPnl  = r.chg != null ? r.chg * (r.qty||0) : null;
    const prev    = (r.current_price||0) - (r.chg||0);
    const dayRate = (r.chg != null && prev > 0) ? r.chg / prev * 100 : null;
    const drc     = dayPnl === null ? '' : dayPnl >= 0 ? 'up' : 'dn';
    sTotalBuy += buy;
    if (r.current_price) { sTotalEval += eval_; sTotalProfit += profit; sHasCur = true; }
    if (dayPnl !== null) { sTotalDayPnl += dayPnl; sHasDayPnl = true; }
    const rateStr  = rate   !== null ? `${profit>=0?'+':''}${Math.round(profit).toLocaleString()}원<br>(${rate>=0?'+':''}${rate.toFixed(2)}%)` : '-';
    const dayStr   = dayPnl !== null
      ? (dayRate !== null
          ? `${dayPnl>=0?'+':''}${Math.round(dayPnl).toLocaleString()}원<br>(${dayRate>=0?'+':''}${dayRate.toFixed(2)}%)`
          : `${dayPnl>=0?'+':''}${Math.round(dayPnl).toLocaleString()}원`)
      : '-';
    return `<tr>
      <td>${r.name||'-'}${r.ticker ? `<div style="font-size:10px;color:var(--muted)">${r.ticker}</div>` : ''}</td>
      <td class="${rc}">${rateStr}</td>
      <td class="${drc}">${dayStr}</td>
      <td>${eval_ ? eval_.toLocaleString()+'원' : '-'}</td>
    </tr>`;
  }).join('');
  const sTotalRate    = sTotalBuy > 0 && sHasCur ? (sTotalProfit / sTotalBuy * 100) : null;
  const sPrevEval     = sTotalEval - sTotalDayPnl;
  const sTotalDayRate = sHasDayPnl && sPrevEval > 0 ? sTotalDayPnl / sPrevEval * 100 : null;
  const strc  = sTotalProfit >= 0 ? 'up' : 'dn';
  const sdtrc = sTotalDayPnl >= 0 ? 'up' : 'dn';
  const sTotalRateStr = sTotalRate !== null
    ? `${sTotalProfit>=0?'+':''}${Math.round(sTotalProfit).toLocaleString()}원<br>(${sTotalRate>=0?'+':''}${sTotalRate.toFixed(2)}%)`
    : '-';
  const sTotalDayStr  = sHasDayPnl
    ? (sTotalDayRate !== null
        ? `${sTotalDayPnl>=0?'+':''}${Math.round(sTotalDayPnl).toLocaleString()}원<br>(${sTotalDayRate>=0?'+':''}${sTotalDayRate.toFixed(2)}%)`
        : `${sTotalDayPnl>=0?'+':''}${Math.round(sTotalDayPnl).toLocaleString()}원`)
    : '-';
  tbody.innerHTML = sRows + `<tr style="border-top:2px solid var(--border);font-weight:700;background:var(--bg)">
    <td>합계</td>
    <td class="${strc}">${sTotalRateStr}</td>
    <td class="${sdtrc}">${sTotalDayStr}</td>
    <td>${sTotalEval ? sTotalEval.toLocaleString()+'원' : '-'}</td>
  </tr>`;
}

// ── ETF별 배당 현황 차트 ──────────────────────────────────
function _renderEtfDivChart() {
  const el = document.getElementById('port-etf-div-chart');
  if (!el) return;

  // ETF별 실수령 배당 합계
  const divByEtf = {};
  _portDiv.forEach(r => { divByEtf[r.etf_id] = (divByEtf[r.etf_id]||0) + (r.net||0); });

  const data = _portEtf.map(r => {
    const evalAmt  = r.qty * (r.current_price || r.avg_price || 0);
    const annRate  = r.annual_div_rate || 0;           // 연배당수익률 %
    const annDivNet= evalAmt * annRate / 100 * (1 - TAX_RATE);  // 연간 추정 세후
    const actual   = divByEtf[r.id] || 0;
    return { name: r.name || r.ticker || '-', annRate, annDivNet, actual };
  }).filter(e => e.annRate > 0 || e.actual > 0)
    .sort((a, b) => b.annRate - a.annRate);

  if (!data.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:12px;text-align:center;padding:20px 0">배당 정보가 있는 ETF가 없습니다<br><span style="font-size:11px">ETF 탭에서 연배당수익률을 입력하세요</span></div>';
    return;
  }

  const maxRate = Math.max(...data.map(e => e.annRate));
  const fmtN = v => { const uk = Math.floor(v/1e8); const man = Math.round((v%1e8)/1e4); return v >= 1e8 ? uk+'억'+(man>0?' '+man.toLocaleString()+'만':'') : v >= 1e4 ? Math.round(v/1e4).toLocaleString()+'만' : Math.round(v).toLocaleString(); };
  const rateColor = r => r >= 9 ? '#00C853' : r >= 6 ? '#3D5AFE' : r >= 3 ? '#FF9100' : '#9EA3B0';

  el.innerHTML = data.map(e => {
    const barW  = maxRate > 0 ? (e.annRate / maxRate * 100).toFixed(1) : 0;
    const color = rateColor(e.annRate);
    return `
      <div style="margin-bottom:16px">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:5px">
          <span style="font-size:12px;font-weight:600;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:65%">${e.name}</span>
          <span style="font-size:13px;font-weight:700;color:${color};flex-shrink:0;margin-left:6px">연 ${e.annRate.toFixed(1)}%</span>
        </div>
        <div style="background:var(--bg);border-radius:4px;height:8px;overflow:hidden">
          <div style="height:100%;width:${barW}%;background:linear-gradient(90deg,${color}88,${color});border-radius:4px;transition:width .4s"></div>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:4px">
          <span style="font-size:10px;color:var(--muted)">추정 연 세후 <strong style="color:var(--text)">${fmtN(e.annDivNet)}원</strong></span>
          ${e.actual ? `<span style="font-size:10px;color:var(--muted)">누적 실수령 ${fmtN(e.actual)}원</span>` : ''}
        </div>
      </div>`;
  }).join('');
}

// ── 자산 현황 도넛 차트 ────────────────────────────────────
// 2026-08-28 — 우측 "수익 배분" 도넛을 없애면서 그 값을 여기로 옮김:
// ETF/개별주 행 아래에 각각의 평가손익을 보조 라인으로 붙임(예수금은
// 손익 개념이 없어 그대로 둠). 카테고리 점 색은 초록/빨강(=상승/하락
// 신호색)과 겹치면 "개별주 -587만원"처럼 라벨색(초록)과 값 부호색(빨강)이
// 어긋나 보이는 문제가 있어서, 손익 부호에만 초록/빨강을 쓰고 카테고리
// 점은 그와 안 겹치는 파랑/보라/주황 계열로 통일함.
function _renderPortAsset(etfProfit = 0, stkProfit = 0) {
  const el = document.getElementById('port-asset-wrap');
  if (!el) return;

  const etfEval = _portEtf.reduce((s, r) => s + (r.qty||0) * (r.current_price || r.avg_price || 0), 0);
  const stkEval = _stockRecords.reduce((s, r) => s + (r.qty||0) * (r.current_price || r.avg_price || 0), 0);
  const cash    = _portCash || 0;
  const total   = etfEval + stkEval + cash;

  if (total <= 0) {
    el.innerHTML = '<div style="color:var(--muted);font-size:12px;text-align:center;padding:20px 0">자산 데이터 없음</div>';
    return;
  }

  const profitLine = (label, val) => {
    if (!val) return '';
    const color = val > 0 ? 'var(--green)' : val < 0 ? 'var(--red)' : 'var(--muted)';
    return `<div style="display:flex;align-items:center;gap:6px;font-size:11px;margin:-3px 0 7px 15px">
      <span style="color:var(--muted);flex:1">${label} 수익</span>
      <span style="font-weight:600;color:${color}">${signFmt(val)}</span>
    </div>`;
  };

  const slices = [
    { label: 'ETF',   val: etfEval, color: '#4da3ff', sub: profitLine('ETF', etfProfit) },
    { label: '개별주', val: stkEval, color: '#a855f7', sub: profitLine('개별주', stkProfit) },
    { label: '예수금', val: cash,    color: '#FF9100' },
  ].filter(s => s.val > 0);

  _drawDonut(el, slices, total, '총 자산', 'var(--primary)');
}

// ── 도넛 공통 렌더러 ──────────────────────────────────────
function _drawDonut(el, slices, total, centerLabel, centerColor, signPrefix = false) {
  const W = 160, CX = 80, CY = 80, R = 64, r = 38;
  const fmtN = v => { const uk = Math.floor(v/1e8); const man = Math.round((v%1e8)/1e4); return v >= 1e8 ? uk+'억'+(man>0?' '+man.toLocaleString()+'만':'') : v >= 1e4 ? Math.round(v/1e4).toLocaleString()+'만' : v.toLocaleString(); };

  let paths = '';
  if (slices.length === 1) {
    // 단일 슬라이스: 원 두 개로 도넛 표현
    paths = `<circle cx="${CX}" cy="${CY}" r="${R}" fill="${slices[0].color}" opacity="0.9"/>
             <circle cx="${CX}" cy="${CY}" r="${r}" fill="var(--surface)"/>`;
  } else {
    let startAngle = -Math.PI / 2;
    slices.forEach(s => {
      const angle = (s.val / total) * 2 * Math.PI;
      const endAngle = startAngle + angle;
      const x1  = CX + R * Math.cos(startAngle), y1  = CY + R * Math.sin(startAngle);
      const x2  = CX + R * Math.cos(endAngle),   y2  = CY + R * Math.sin(endAngle);
      const ix1 = CX + r * Math.cos(startAngle), iy1 = CY + r * Math.sin(startAngle);
      const ix2 = CX + r * Math.cos(endAngle),   iy2 = CY + r * Math.sin(endAngle);
      const large = angle > Math.PI ? 1 : 0;
      paths += `<path d="M${ix1},${iy1} L${x1},${y1} A${R},${R} 0 ${large} 1 ${x2},${y2} L${ix2},${iy2} A${r},${r} 0 ${large} 0 ${ix1},${iy1} Z" fill="${s.color}" opacity="0.9"/>`;
      startAngle = endAngle;
    });
  }

  const totalFmt = (signPrefix ? '+' : '') + fmtN(total) + '원';
  // s.sub: 슬라이스 아래 덧붙일 보조 라인(HTML) — 예: 자산현황의 ETF/개별주
  // 행 밑에 "ETF 수익 -781만원"처럼 손익을 같이 보여줄 때 사용(선택 항목)
  const legend = slices.map(s => {
    const pct = (s.val / total * 100).toFixed(1);
    return `<div style="display:flex;align-items:center;gap:6px;font-size:12px;margin-bottom:7px">
      <span style="width:9px;height:9px;border-radius:50%;background:${s.color};flex-shrink:0"></span>
      <span style="color:var(--muted);flex:1">${s.label}</span>
      <span style="font-weight:600">${signPrefix?'+':''}${fmtN(s.val)}원</span>
      <span style="color:var(--muted);min-width:34px;text-align:right">${pct}%</span>
    </div>${s.sub || ''}`;
  }).join('');

  // 세로 레이아웃: SVG 위 / 범례 아래
  el.innerHTML = `
    <div style="display:flex;flex-direction:column;align-items:center;gap:12px">
      <svg width="${W}" height="${W}" viewBox="0 0 ${W} ${W}">${paths}
        <text x="${CX}" y="${CY-6}" text-anchor="middle" font-size="10" fill="var(--muted)">${centerLabel}</text>
        <text x="${CX}" y="${CY+10}" text-anchor="middle" font-size="13" font-weight="700" fill="${centerColor}">${totalFmt}</text>
      </svg>
      <div style="width:100%">${legend}</div>
    </div>`;
}

// ── 공모주·배당금 누적 (종목별, 기간 필터) ──────────────────
// 2026-08-28 — 기존 "수익 배분" 4항목 도넛(ETF/개별주/공모주/배당)을
// 없애고, ETF·개별주 손익은 왼쪽 자산현황 카드로 옮김. 여기는 "실현·
// 수령 완료된" 공모주 수익 + 배당금만 종목별로 나열 + 기간 필터
// (1개월/1년/전체기간) — 보유 포지션 평가손익(왼쪽)과 실제 들어온
// 현금성 수익(오른쪽)을 성격이 달라 카드를 분리했다.
let _ipoDivPeriod = 'all';

function _withinPeriod(dateStr, period) {
  if (period === 'all') return true;
  if (!dateStr) return false;
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return false;
  const cutoff = new Date();
  if (period === '1m') cutoff.setMonth(cutoff.getMonth() - 1);
  else if (period === '1y') cutoff.setFullYear(cutoff.getFullYear() - 1);
  return d >= cutoff;
}

function _computeIpoDivByStock(period) {
  const rows = [];

  _portIpo.forEach(r => {
    if (r.direct_profit == null && !(r.price_open > 0 && r.price_ipo > 0)) return;
    if (!_withinPeriod(r.date_list, period)) return;
    const profit = r.direct_profit != null
      ? r.direct_profit
      : (r.price_open - r.price_ipo) * (r.sell_qty || r.shares_alloc || 0) - 2000;
    rows.push({ name: r.name || r.ticker || '-', amount: profit, type: 'ipo' });
  });

  const divByEtf = {};
  _portDiv.forEach(r => {
    if (!_withinPeriod(r.pay_date, period)) return;
    divByEtf[r.etf_id] = (divByEtf[r.etf_id] || 0) + (r.net || 0);
  });
  Object.entries(divByEtf).forEach(([etfId, amt]) => {
    const etf = _portEtf.find(e => String(e.id) === String(etfId));
    rows.push({ name: (etf && (etf.name || etf.ticker)) || 'ETF', amount: amt, type: 'div' });
  });

  return rows.sort((a, b) => b.amount - a.amount);
}

function setIpoDivPeriod(period) {
  _ipoDivPeriod = period;
  _renderIpoDivByStock();
}

function _renderIpoDivByStock() {
  const el = document.getElementById('port-donut-wrap');
  if (!el) return;

  const period = _ipoDivPeriod;
  const rows = _computeIpoDivByStock(period);
  const total = rows.reduce((s, r) => s + r.amount, 0);
  const valColor = v => v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--muted)';

  const PERIODS = [['1m', '1개월'], ['1y', '1년'], ['all', '전체기간']];
  const tabs = PERIODS.map(([key, label]) => {
    const active = key === period;
    return `<button onclick="setIpoDivPeriod('${key}')" style="flex:1;padding:6px 0;border-radius:8px;font-size:11px;font-weight:600;
      border:1px solid ${active ? 'var(--primary)' : 'var(--border)'};
      background:${active ? 'var(--primary)' : 'none'};
      color:${active ? '#fff' : 'var(--muted)'};cursor:pointer;transition:.15s">${label}</button>`;
  }).join('');

  const list = rows.length ? rows.map(r => `
    <div style="display:flex;align-items:center;gap:8px;font-size:12px;padding:7px 0;border-bottom:1px solid var(--border)">
      <span style="font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;flex-shrink:0;
        background:${r.type === 'ipo' ? 'rgba(45,212,191,.15)' : 'rgba(192,132,252,.15)'};
        color:${r.type === 'ipo' ? '#2dd4bf' : '#c084fc'}">${r.type === 'ipo' ? '공모주' : '배당'}</span>
      <span style="flex:1;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.name}</span>
      <span style="font-weight:600;color:${valColor(r.amount)}">${signFmt(r.amount)}</span>
    </div>`).join('')
    : `<div style="color:var(--muted);font-size:12px;text-align:center;padding:24px 0">해당 기간 데이터 없음</div>`;

  el.innerHTML = `
    <div style="display:flex;gap:6px;margin-bottom:14px">${tabs}</div>
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;padding-bottom:10px;border-bottom:1px solid var(--border)">
      <span style="font-size:12px;color:var(--muted);font-weight:700">합계</span>
      <span style="font-size:16px;font-weight:700;color:${valColor(total)}">${signFmt(total)}</span>
    </div>
    <div style="max-height:280px;overflow-y:auto">${list}</div>`;
}

// ── 개별주 종목 자동완성 ────────────────────────────────────
let _stkAcTimer = null;

function onStkNameInput(val) {
  clearTimeout(_stkAcTimer);
  if (!val.trim()) { hideStkAc(); return; }
  _stkAcTimer = setTimeout(() => fetchStkAc(val.trim()), 220);
}

async function fetchStkAc(q) {
  try {
    const r = await fetch(`/api/stock?q=${encodeURIComponent(q)}`);
    const d = await r.json();
    showStkAc(d.items || []);
  } catch { hideStkAc(); }
}

function showStkAc(items) {
  const list = document.getElementById('stk-ac-list');
  if (!list || !items.length) { hideStkAc(); return; }
  list.innerHTML = items.map(it => {
    const safeN = it.name.replace(/'/g, "\\'");
    const mkt = it.market ? `<span style="font-size:10px;color:var(--muted);margin-left:4px">${it.market}</span>` : '';
    return `<div onmousedown="selectStkAcItem('${safeN}','${it.ticker}')"
      style="padding:9px 12px;font-size:13px;cursor:pointer;display:flex;justify-content:space-between;
             align-items:center;border-bottom:1px solid var(--border)"
      onmouseover="this.style.background='var(--secondary)'" onmouseout="this.style.background=''">
      <span>${it.name}${mkt}</span>
      <span style="font-size:11px;color:var(--primary);font-weight:600">${it.ticker}</span>
    </div>`;
  }).join('');
  list.style.display = 'block';
}

function hideStkAc() {
  setTimeout(() => {
    const list = document.getElementById('stk-ac-list');
    if (list) list.style.display = 'none';
  }, 150);
}

async function selectStkAcItem(name, ticker) {
  document.getElementById('sm-name').value   = name;
  document.getElementById('sm-ticker').value = ticker;
  hideStkAc();
  await fetchStkQuote(ticker);
}

function onStkTickerInput(val) {
  clearTimeout(_stkAcTimer);
  const clean = val.trim();
  if (clean.length === 6 && /^\d{6}$/.test(clean)) {
    _stkAcTimer = setTimeout(() => fetchStkQuote(clean), 400);
  }
}

async function fetchStkQuote(ticker) {
  const curEl    = document.getElementById('sm-cur');
  const statusEl = document.getElementById('sm-cur-status');
  const tickerEl = document.getElementById('sm-ticker');
  if (!curEl || !ticker) return;

  statusEl.textContent = '조회 중...';
  statusEl.style.color = 'var(--muted)';
  tickerEl.style.borderColor = 'var(--primary)';

  try {
    const r = await fetch(`/api/stock?ticker=${ticker}`);
    const d = await r.json();
    if (d.price) {
      curEl.value = d.price;
      // 이름이 비어있으면 자동 채움
      const nameEl = document.getElementById('sm-name');
      if (d.name && !nameEl.value.trim()) nameEl.value = d.name;
      statusEl.textContent = `✓ ${d.chgPct >= 0 ? '+' : ''}${d.chgPct}%`;
      statusEl.style.color = d.chgPct >= 0 ? 'var(--green)' : 'var(--red)';
      tickerEl.style.borderColor = 'var(--green)';
    } else {
      statusEl.textContent = '조회 실패';
      statusEl.style.color = 'var(--red)';
      tickerEl.style.borderColor = 'var(--red)';
    }
  } catch {
    statusEl.textContent = '오류';
    statusEl.style.color = 'var(--red)';
  }
  setTimeout(() => { tickerEl.style.borderColor = ''; }, 2000);
}

// ── 개별주 모달 ────────────────────────────────────────────
function openStockModal(id) {
  const r = id ? _stockRecords.find(s => s.id == id) : null;
  document.getElementById('stock-modal-title').textContent = r ? '개별주 편집' : '개별주 추가';
  document.getElementById('sm-id').value      = r?.id || '';
  document.getElementById('sm-name').value    = r?.name || '';
  document.getElementById('sm-ticker').value  = r?.ticker || '';
  document.getElementById('sm-qty').value     = r?.qty || '';
  document.getElementById('sm-avg').value     = r?.avg_price || '';
  document.getElementById('sm-cur').value     = r?.current_price || '';
  document.getElementById('sm-note').value    = r?.note || '';
  const st = document.getElementById('sm-cur-status');
  if (st) st.textContent = '';
  hideStkAc();
  document.getElementById('stock-modal-overlay').style.display = 'flex';
  // 편집 시 티커가 있으면 현재가 자동 갱신
  if (r?.ticker) fetchStkQuote(r.ticker);
}
function closeStockModal() { document.getElementById('stock-modal-overlay').style.display = 'none'; }

async function saveStockRecord() {
  const name = document.getElementById('sm-name').value.trim();
  const qty  = parseFloat(document.getElementById('sm-qty').value) || 0;
  const avg  = parseFloat(document.getElementById('sm-avg').value) || 0;
  if (!name) { alert('종목명을 입력하세요'); return; }
  const btn = document.querySelector('[onclick="saveStockRecord()"]');
  if (btn?.disabled) return;
  if (btn) { btn.disabled = true; btn.textContent = '저장 중...'; }
  try {
    const record = {
      id:            parseInt(document.getElementById('sm-id').value) || undefined,
      name,
      ticker:        document.getElementById('sm-ticker').value.trim(),
      qty,
      avg_price:     avg,
      current_price: parseFloat(document.getElementById('sm-cur').value) || 0,
      note:          document.getElementById('sm-note').value.trim(),
    };
    const res = await fetch('/api/stocks', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({record}) });
    const d   = await res.json();
    if (!d.ok) { alert('저장 실패: ' + (d.error||'')); return; }
    _invalidateBinCache();
    const idx = _stockRecords.findIndex(s => s.id == d.record.id);
    if (idx !== -1) _stockRecords[idx] = d.record; else _stockRecords.push(d.record);
    closeStockModal();
    renderPortfolio();
    renderStockCards();
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '💾 저장'; }
  }
}

async function deleteStockRecord(id) {
  if (!confirm('삭제하시겠습니까?')) return;
  await fetch('/api/stocks?id=' + id, { method:'DELETE' });
  _invalidateBinCache();
  _stockRecords = _stockRecords.filter(s => s.id != id);
  renderPortfolio();
  renderStockCards();
}

// ══════════════════════════════════════════════════════════════════════════════
// 개별주 탭
// ══════════════════════════════════════════════════════════════════════════════

let _stkTransactions = [];   // 개별주 거래 내역 (stock_id 기반)
let _stkRefreshing   = false;

