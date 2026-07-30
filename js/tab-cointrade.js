async function initCoinTrade() {
  _restoreTradeSection('cc', true);
  ctLoadConfig();
  await ctLoadAll();
  clearInterval(_ctRefreshTimer);
  _ctRefreshTimer = setInterval(() => {
    if (document.querySelector('.tab-btn.active')?.getAttribute('onclick')?.includes('cointrade')) {
      ctLoadAll();
    } else {
      clearInterval(_ctRefreshTimer);
      clearInterval(_ctPriceTimer);
    }
  }, 30000);
  clearInterval(_ctPriceTimer);
  ctRefreshPrices();
  _ctPriceTimer = setInterval(() => {
    if (document.querySelector('.tab-btn.active')?.getAttribute('onclick')?.includes('cointrade')) {
      ctRefreshPrices();
    }
  }, 30000);

  // 잔고 자동 갱신 타이머
  clearInterval(_ctBalanceTimer);
  if (_ctConfig.autoRefreshSec > 0) {
    _ctBalanceTimer = setInterval(() => {
      if (document.querySelector('.tab-btn.active')?.getAttribute('onclick')?.includes('cointrade')) {
        ctRefreshBalance();
      } else {
        clearInterval(_ctBalanceTimer);
      }
    }, _ctConfig.autoRefreshSec * 1000);
  }

  ctCheckDaemonStatus();
  ctLoadToday();
}

// ── 일별 수익 현황 ────────────────────────────────────────────
async function ctFetchDay(date) {
  const url = date === null ? '/api/coin-today' : `/api/coin-date?date=${date}`;
  try {
    const r = await fetch(url);
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

function ctRenderDailyCard(data, idx) {
  const wrap = document.getElementById('ct-daily-wrap');
  if (!wrap) return;
  const card = document.getElementById(`ct-day-card-${idx}`);
  if (!card) return;

  if (!data || data.error) {
    card.innerHTML = `<div style="color:var(--muted);font-size:12px;padding:4px 0">데이터 없음</div>`;
    return;
  }
  const net    = data.netProfit || 0;
  const netCls = net > 0 ? '#22c55e' : net < 0 ? '#ef4444' : 'var(--muted)';
  const netStr = (net >= 0 ? '+' : '') + net.toLocaleString() + '원';
  const buyCnt  = data.buys?.length  || 0;
  const sellCnt = data.sells?.length || 0;
  const fee     = data.totalFee || 0;
  const pending = data.pendingBuyCnt || data.unmatchedBuyCnt || 0;

  let txStr = `매도 ${sellCnt}건`;
  if (buyCnt > 0) txStr += ` · 매수 ${buyCnt}건`;
  if (pending > 0) txStr += ` <span style="font-size:10px;font-weight:600;color:#f59e0b">(미매도 ${pending})</span>`;

  card.innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
      <div style="text-align:center">
        <div style="font-size:9px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">실현손익</div>
        <div style="font-size:17px;font-weight:800;color:${netCls}">${netStr}</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:9px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">체결</div>
        <div style="font-size:13px;font-weight:700;color:var(--text);line-height:1.4">${txStr}</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:9px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">수수료</div>
        <div style="font-size:17px;font-weight:800;color:var(--muted)">${fee.toLocaleString()}원</div>
      </div>
    </div>
    ${(buyCnt + sellCnt) > 0 ? `
    <div id="ct-day-detail-${idx}" style="display:none;margin-top:10px">
      <div style="overflow-x:auto;border-radius:8px;border:1px solid var(--border)">
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead><tr style="background:var(--surface)">
            <th style="padding:5px 8px;text-align:left;color:var(--muted);font-size:10px;font-weight:700">구분</th>
            <th style="padding:5px 8px;text-align:left;color:var(--muted);font-size:10px;font-weight:700">시각</th>
            <th style="padding:5px 8px;text-align:left;color:var(--muted);font-size:10px;font-weight:700">종목</th>
            <th style="padding:5px 8px;text-align:right;color:var(--muted);font-size:10px;font-weight:700">수량</th>
            <th style="padding:5px 8px;text-align:right;color:var(--muted);font-size:10px;font-weight:700">단가</th>
            <th style="padding:5px 8px;text-align:right;color:var(--muted);font-size:10px;font-weight:700">손익</th>
          </tr></thead>
          <tbody>
            ${[...(data.sells||[]).map(o => {
              const p = o.profit;
              const hasProfit = p !== null && p !== undefined;
              const pRounded  = hasProfit ? Math.round(p) : null;
              const pCls      = !hasProfit ? 'var(--muted)' : pRounded > 0 ? '#22c55e' : pRounded < 0 ? '#ef4444' : 'var(--muted)';
              const pStr      = hasProfit ? `${pRounded >= 0 ? '+' : ''}${pRounded.toLocaleString()}원` : '<span style="font-size:10px">매수가 미기록</span>';
              const isGrid    = o.source === 'grid';
              const isLoss    = hasProfit && pRounded < 0;
              const sellLabel = isLoss ? '손절' : '매도';
              const sellColor = isLoss ? '#ef4444' : (isGrid ? '#3b82f6' : '#22c55e');
              const buyPriceStr = o.buyPrice ? `${Math.round(o.buyPrice).toLocaleString()}원` : '—';
              const qty = typeof o.qty === 'number'
                ? (Number.isInteger(o.qty) ? o.qty.toLocaleString() : o.qty.toLocaleString(undefined, {maximumFractionDigits:6}))
                : (o.qty || '—');
              return `<tr style="border-top:1px solid var(--border)">
                  <td style="padding:5px 8px;color:#f59e0b;font-weight:700">매수</td>
                  <td style="padding:5px 8px;color:var(--muted);font-variant-numeric:tabular-nums">${o.buyTime || '—'}</td>
                  <td style="padding:5px 8px;color:var(--text)">${o.name}</td>
                  <td style="padding:5px 8px;text-align:right;color:var(--text);font-variant-numeric:tabular-nums">${qty}</td>
                  <td style="padding:5px 8px;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums">${buyPriceStr}</td>
                  <td style="padding:5px 8px;text-align:right;color:var(--muted)">—</td>
                </tr>
                <tr>
                  <td style="padding:5px 8px;color:${sellColor};font-weight:700">${sellLabel}</td>
                  <td style="padding:5px 8px;color:var(--muted);font-variant-numeric:tabular-nums">${o.time}</td>
                  <td style="padding:5px 8px;color:var(--text)">${o.name}</td>
                  <td style="padding:5px 8px;text-align:right;color:var(--text);font-variant-numeric:tabular-nums">${qty}</td>
                  <td style="padding:5px 8px;text-align:right;color:var(--text);font-variant-numeric:tabular-nums">${Math.round(o.sellPrice||0).toLocaleString()}원</td>
                  <td style="padding:5px 8px;text-align:right;font-weight:700;color:${pCls}">${pStr}</td>
                </tr>`;
            }),
            ...(data.buys||[]).map(o=>{
              const qty = typeof o.qty === 'number'
                ? (Number.isInteger(o.qty) ? o.qty.toLocaleString() : o.qty.toLocaleString(undefined, {maximumFractionDigits:6}))
                : (o.qty || '—');
              return `<tr style="border-top:1px solid var(--border)">
                <td style="padding:5px 8px;color:#f59e0b;font-weight:700">매수</td>
                <td style="padding:5px 8px;color:var(--muted);font-variant-numeric:tabular-nums">${o.time}</td>
                <td style="padding:5px 8px;color:var(--text)">${o.name}</td>
                <td style="padding:5px 8px;text-align:right;color:var(--text);font-variant-numeric:tabular-nums">${qty}</td>
                <td style="padding:5px 8px;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums">${Math.round(o.buyPrice||0).toLocaleString()}원</td>
                <td style="padding:5px 8px;text-align:right;color:#f59e0b;font-size:10px">보유중</td>
              </tr>`;
            })].join('')}
          </tbody>
        </table>
      </div>
    </div>
    <button onclick="ctToggleDayDetail(${idx})" id="ct-day-toggle-${idx}"
      style="margin-top:8px;background:none;border:none;color:var(--muted);font-size:11px;cursor:pointer;padding:0">▼ 상세 보기</button>
    ` : ''}
  `;
}

function ctToggleDayDetail(idx) {
  const el  = document.getElementById(`ct-day-detail-${idx}`);
  const btn = document.getElementById(`ct-day-toggle-${idx}`);
  if (!el) return;
  const open = el.style.display === 'none';
  el.style.display = open ? 'block' : 'none';
  if (btn) btn.textContent = open ? '▲ 접기' : '▼ 상세 보기';
}

async function ctLoadToday() {
  const kstNow = new Date(Date.now() + 9 * 3600 * 1000);
  const dates  = [null];
  for (let i = 1; i <= 2; i++) {
    const d = new Date(kstNow.getTime() - i * 86400000);
    dates.push(d.toISOString().slice(0, 10));
  }
  const mm = String(kstNow.getMonth() + 1).padStart(2, '0');
  const dd = String(kstNow.getDate()).padStart(2, '0');
  const labels = [`오늘 (${mm}/${dd})`];
  for (let i = 1; i <= 2; i++) {
    const d = new Date(kstNow.getTime() - i * 86400000);
    const m = String(d.getMonth()+1).padStart(2,'0');
    const day = String(d.getDate()).padStart(2,'0');
    labels.push(`${i===1?'어제':'그제'} (${m}/${day})`);
  }
  const wrap = document.getElementById('ct-daily-wrap');
  if (!wrap) return;
  wrap.innerHTML = dates.map((_, i) => `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px">
      <div style="font-size:10px;font-weight:700;color:var(--muted);margin-bottom:10px;display:flex;justify-content:space-between;align-items:center">
        <span style="text-transform:uppercase;letter-spacing:.06em">${labels[i]}</span>
        <span id="ct-day-loading-${i}" style="color:var(--muted);font-size:10px">로딩중…</span>
      </div>
      <div id="ct-day-card-${i}"></div>
    </div>
  `).join('');
  await Promise.all(dates.map(async (date, i) => {
    const data = await ctFetchDay(date);
    const loadEl = document.getElementById(`ct-day-loading-${i}`);
    if (loadEl) loadEl.remove();
    ctRenderDailyCard(data, i);
  }));
}

async function ctLoadAll() {
  try {
    const [rBuy, rSell, rCycle, rAccount, rSignal, rGrid] = await Promise.all([
      fetch('/api/coin-buy'),
      fetch('/api/coin-sell'),
      fetch('/api/coin-cycle'),
      fetch('/api/coin-account'),
      fetch('/api/coin-signal'),
      fetch('/api/coin-grid'),
    ]);
    if (rBuy.ok)    _ctBuyJobs    = await rBuy.json();
    if (rSell.ok)   _ctSellJobs   = await rSell.json();
    if (rCycle.ok)  _ctCycleJobs  = await rCycle.json();
    if (rAccount.ok) _ctAccount   = await rAccount.json();
    if (rSignal.ok) _ctSignalJobs = await rSignal.json();
    if (rGrid.ok)   _ctGridJobs   = await rGrid.json();
  } catch (e) {
    console.warn('코인 데이터 로드 실패:', e);
  }

  // stopped 그리드 잡 Gist에서 자동 삭제
  const stoppedGrids = (_ctGridJobs || []).filter(j => j.status === 'stopped');
  for (const j of stoppedGrids) {
    fetch(`/api/coin-grid?id=${encodeURIComponent(j.id)}`, { method: 'DELETE' }).catch(() => {});
  }
  if (stoppedGrids.length) {
    _ctGridJobs = (_ctGridJobs || []).filter(j => j.status !== 'stopped');
  }
  ctRenderAccount();
  ctRenderBuyJobs();
  ctRenderSellJobs();
  ctRenderCycleJobs();
  ctRenderGridJobs();
  ctRenderSignalJobs();
  ctRenderHistory();
  ctRenderHoldingChips();
  ctRefreshPrices();
}

// ── 잔고 새로고침 ────────────────────────────────────────
async function ctRefreshBalance() {
  const btn = document.getElementById('ct-balance-refresh-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⟳ 조회중...'; }
  try {
    const r = await fetch('/api/data?mode=trigger_coin_balance');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    let sec = 30;
    const tick = setInterval(() => {
      if (btn) btn.textContent = `⟳ 조회중... (${--sec}초)`;
      if (sec <= 0) {
        clearInterval(tick);
        fetch('/api/coin-account').then(res => res.json()).then(d => {
          _ctAccount = d;
          ctRenderAccount();
          if (btn) { btn.disabled = false; btn.textContent = '⟳ 잔고 새로고침'; }
        }).catch(() => {
          if (btn) { btn.disabled = false; btn.textContent = '⟳ 잔고 새로고침'; }
        });
      }
    }, 1000);
  } catch {
    if (btn) { btn.disabled = false; btn.textContent = '⟳ 잔고 새로고침'; }
  }
}

// ── 계좌 렌더링 ──────────────────────────────────────────
function ctRenderAccount() {
  const el = document.getElementById('ct-account');
  if (!el) return;
  if (!_ctAccount) {
    el.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:8px 0">잔고 정보 없음 — 잔고 새로고침을 눌러주세요</div>';
    return;
  }
  const a = _ctAccount;
  const krw = Number(a.krw || 0);
  const holdings = a.holdings || [];

  const totalEval  = Math.round(krw + holdings.reduce((s, h) => s + (h.eval_amount || 0), 0));
  const totalPnl   = Math.round(holdings.reduce((s, h) => s + (h.pnl || 0), 0));
  const pnlColor   = totalPnl >= 0 ? 'var(--green)' : 'var(--red)';

  const rows = holdings.map(h => {
    const hPnlColor = h.pnl_pct >= 0 ? 'var(--green)' : 'var(--red)';
    const qty = Number(h.qty);
    const qtyStr = qty >= 100 ? qty.toLocaleString('ko-KR', {maximumFractionDigits:2})
                 : qty >= 1   ? qty.toFixed(4)
                 :              qty.toFixed(8).replace(/0+$/, '');
    const safeN = (h.name || '').replace(/'/g, "\\'");
    return `
    <tr style="border-bottom:1px solid var(--border)">
      <td style="padding:5px 6px;white-space:nowrap">
        <div style="cursor:pointer;color:var(--primary);font-size:11px" onclick="csSelectCoin('${h.ticker}','${safeN}','${h.symbol}',${h.qty},${h.avg_price})">${h.ticker}</div>
        <div style="color:var(--text);font-size:11px">${h.name}</div>
      </td>
      <td style="padding:5px 6px;text-align:right;white-space:nowrap;color:var(--text);font-size:11px">${qtyStr}</td>
      <td style="padding:5px 6px;text-align:right;white-space:nowrap;font-size:11px">
        <div style="color:var(--muted)">${h.avg_price.toLocaleString()}</div>
        <div style="color:var(--text)">${h.cur_price.toLocaleString()}</div>
      </td>
      <td style="padding:5px 6px;text-align:right;white-space:nowrap">
        <div style="color:${hPnlColor};font-weight:600;font-size:12px">${h.pnl>=0?'+':''}${Math.round(h.pnl).toLocaleString()}원</div>
        <div style="color:${hPnlColor};font-size:10px">${h.pnl_pct>=0?'+':''}${h.pnl_pct.toFixed(2)}%</div>
      </td>
    </tr>`;
  }).join('');

  el.innerHTML = `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px 20px">
      <div style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:10px">🪙 업비트 계좌</div>
      <div style="display:flex;gap:0;flex-wrap:nowrap;margin-bottom:12px;border:1px solid var(--border);border-radius:10px;overflow:hidden">
        <div style="flex:1;padding:6px 4px;text-align:center;border-right:1px solid var(--border);min-width:0">
          <div style="font-size:9px;color:var(--muted);margin-bottom:2px;white-space:nowrap">총평가금액</div>
          <div style="font-size:11px;font-weight:700;color:var(--text);white-space:nowrap">${totalEval.toLocaleString()}원</div>
        </div>
        <div style="flex:1;padding:6px 4px;text-align:center;border-right:1px solid var(--border);min-width:0">
          <div style="font-size:9px;color:var(--muted);margin-bottom:2px;white-space:nowrap">보유 원화</div>
          <div style="font-size:11px;font-weight:700;color:var(--text);white-space:nowrap">${krw.toLocaleString('ko-KR',{maximumFractionDigits:0})}원</div>
        </div>
        <div style="flex:1;padding:6px 4px;text-align:center;border-right:1px solid var(--border);min-width:0">
          <div style="font-size:9px;color:var(--muted);margin-bottom:2px;white-space:nowrap">총평가손익</div>
          <div style="font-size:11px;font-weight:700;color:${pnlColor};white-space:nowrap">${totalPnl>=0?'+':''}${totalPnl.toLocaleString()}원</div>
        </div>
        <div style="flex:1;padding:6px 4px;text-align:center;min-width:0">
          <div style="font-size:9px;color:var(--muted);margin-bottom:2px;white-space:nowrap">보유 코인</div>
          <div style="font-size:11px;font-weight:700;color:var(--text);white-space:nowrap">${holdings.length}종</div>
        </div>
      </div>
      ${rows ? `<table style="width:100%;border-collapse:collapse">
        <thead><tr style="color:var(--muted);border-bottom:1px solid var(--border);font-size:11px">
          <th style="padding:4px 6px;text-align:left;font-weight:500">티커 / 코인명</th>
          <th style="padding:4px 6px;text-align:right;font-weight:500">수량</th>
          <th style="padding:4px 6px;text-align:right;font-weight:500">평균→현재(원)</th>
          <th style="padding:4px 6px;text-align:right;font-weight:500">평가손익</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div style="font-size:10px;color:var(--muted);margin-top:6px;display:flex;justify-content:space-between">
        <span>티커 클릭 시 매도 폼 자동입력</span>
        ${a.updated_at ? `<span>갱신: ${a.updated_at}</span>` : ''}
      </div>` : '<div style="color:var(--muted);font-size:12px">보유 코인 없음</div>'}
    </div>`;

  ctRenderHoldingChips();
}

// ── 현재가 갱신 ──────────────────────────────────────────
async function ctRefreshPrices() {
  const activeSell  = _ctSellJobs.filter(j => j.status === 'active');
  const activeCycle = _ctCycleJobs.filter(j => !['done','cancelled','stopped'].includes(j.status));
  const all = [...activeSell, ...activeCycle];
  // 앱 탭이 활성화된 동안 coin-runner를 서버사이드에서 트리거 (매매 자동 실행)
  if (all.length > 0) {
    fetch('/api/data?mode=trigger_coin_runner&runner_mode=sell').catch(() => {});
  }
  if (!all.length) return;

  const tickers = [...new Set(all.map(j => j.ticker))];
  try {
    const r = await fetch(`/api/coin-price?markets=${tickers.join(',')}`);
    const data = await r.json();
    if (!Array.isArray(data)) return;
    const priceMap = {};
    for (const d of data) priceMap[d.market] = d;

    for (const job of all) {
      const d = priceMap[job.ticker];
      if (!d) continue;
      const cur    = d.trade_price;
      const chgPct = (d.signed_change_rate * 100).toFixed(2);
      const uid    = job.ticker + (job.created_at || '').replace(/\s/g,'');
      const priceText = `${cur.toLocaleString()}원 (${chgPct >= 0 ? '+' : ''}${chgPct}%)`;
      let pnlHtml = null;
      if (job.buy_price) {
        const buyTotal = job.buy_price * (1 + COIN_FEE);
        const sellNet  = cur * (1 - COIN_FEE);
        const pnlPct   = (sellNet - buyTotal) / buyTotal * 100;
        const color    = pnlPct >= 0 ? 'var(--green)' : 'var(--red)';
        pnlHtml = `<span style="color:${color};font-weight:700">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</span>`;
      }
      _ctPriceCache[job.ticker] = { priceText, pnlHtml };

      const priceEl = document.getElementById(`ct-price-${uid}`);
      const pnlEl   = document.getElementById(`ct-pnl-${uid}`);
      if (priceEl) priceEl.textContent = priceText;
      if (pnlEl && pnlHtml) pnlEl.innerHTML = pnlHtml;
    }
  } catch (_) {}
}

// ── 보유 코인 칩 ─────────────────────────────────────────
function ctRenderHoldingChips() {
  const wrap  = document.getElementById('ct-holding-chips');
  const inner = document.getElementById('ct-holding-chips-inner');
  if (!wrap || !inner) return;
  const holdings = _ctAccount?.holdings || [];
  if (!holdings.length) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';
  inner.innerHTML = holdings.map(h => `
    <button onclick="csSelectCoin('${h.ticker}','${h.name}','${h.symbol}',${h.qty},${h.avg_price})"
      style="padding:5px 10px;border:1px solid var(--border);border-radius:20px;background:var(--bg);color:var(--text);font-size:12px;cursor:pointer">
      ${h.symbol} · ${h.qty.toFixed(8)}개
    </button>`).join('');
}

// ── 코인 자동완성 (공통) ─────────────────────────────────
const _acRegistry = {};

function _acPick(listId, idx) {
  const entry = _acRegistry[listId];
  if (!entry) return;
  entry.onSelect(entry.coins[idx]);
}

function _filterCoins(q) {
  if (!q) return COIN_LIST.slice(0, 8);
  const lq = q.toLowerCase();
  return COIN_LIST.filter(c =>
    c.name.includes(q) || c.symbol.toLowerCase().includes(lq) || c.ticker.toLowerCase().includes(lq)
  ).slice(0, 8);
}

function _renderAcList(listId, coins, onSelect) {
  const el = document.getElementById(listId);
  if (!el) return;
  if (!coins.length) { el.style.display = 'none'; return; }
  _acRegistry[listId] = { coins, onSelect };
  el.style.display = 'block';
  el.innerHTML = coins.map((c, i) =>
    `<div onmousedown="event.preventDefault()" onclick="_acPick('${listId}',${i})"
      style="padding:8px 12px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--border)">
      <b>${c.name}</b> <span style="color:var(--muted);font-size:11px">${c.symbol} · ${c.ticker}</span>
    </div>`
  ).join('');
}

// ── 매수 폼 ──────────────────────────────────────────────
function onCbNameInput(v) {
  _renderAcList('cb-ac-list', _filterCoins(v), (c) => {
    document.getElementById('cb-name').value = c.name;
    document.getElementById('cb-ticker').value = c.ticker;
    document.getElementById('cb-ticker-display').textContent = c.ticker;
    document.getElementById('cb-ac-list').style.display = 'none';
    cbUpdateHint();
    _showCoinCurPrice(c.ticker, 'cb-cur-price-wrap', 'cb-cur-price', 'cb-cur-pct');
  });
}
function hideCbAc() { setTimeout(() => { const el = document.getElementById('cb-ac-list'); if (el) el.style.display = 'none'; }, 150); }

function cbCondChange() {
  const v = document.querySelector('input[name="cb-cond"]:checked')?.value;
  const row = document.getElementById('cb-limit-row');
  if (row) row.style.display = v === 'limit' ? 'block' : 'none';
}

function cbAmountTypeChange() {
  const v = document.querySelector('input[name="cb-amount-type"]:checked')?.value;
  const krwRow = document.getElementById('cb-krw-row');
  const qtyRow = document.getElementById('cb-qty-row');
  if (krwRow) krwRow.style.display = v === 'qty' ? 'none' : 'block';
  if (qtyRow) qtyRow.style.display = v === 'qty' ? 'block' : 'none';
  cbUpdateHint();
}

function cbUpdateHint() {
  const amtType = document.querySelector('input[name="cb-amount-type"]:checked')?.value || 'krw';
  const hint = document.getElementById('cb-hint');
  if (!hint) return;
  if (amtType === 'qty') {
    const qty = Number(document.getElementById('cb-coin-qty')?.value || 0);
    hint.textContent = qty > 0 ? `${qty}개 매수` : '매수할 코인 수량';
  } else {
    const amt = Number(document.getElementById('cb-krw-amount')?.value || 0);
    hint.textContent = amt > 0 ? `${amt.toLocaleString()}원 매수 · 수수료 ${(amt * COIN_FEE).toFixed(0)}원` : '매수에 사용할 KRW 금액';
  }
}

async function cbRegister() {
  const ticker    = document.getElementById('cb-ticker')?.value;
  const name      = document.getElementById('cb-name')?.value;
  const cond      = document.querySelector('input[name="cb-cond"]:checked')?.value || 'market_krw';
  const amtType   = document.querySelector('input[name="cb-amount-type"]:checked')?.value || 'krw';
  const amt       = Number(document.getElementById('cb-krw-amount')?.value || 0);
  const coinQty   = Number(document.getElementById('cb-coin-qty')?.value || 0);
  const tp        = Number(document.getElementById('cb-target-price')?.value || 0);
  const msg       = document.getElementById('cb-msg');

  if (!ticker) { if (msg) msg.innerHTML = '<span style="color:var(--red)">코인을 선택해주세요</span>'; return; }
  if (amtType === 'krw' && amt < 5000) { if (msg) msg.innerHTML = '<span style="color:var(--red)">최소 매수금액은 5,000원입니다</span>'; return; }
  if (amtType === 'qty' && coinQty <= 0) { if (msg) msg.innerHTML = '<span style="color:var(--red)">코인 수량을 입력해주세요</span>'; return; }
  if (cond === 'limit' && tp <= 0) { if (msg) msg.innerHTML = '<span style="color:var(--red)">목표 매수가를 입력해주세요</span>'; return; }

  const job = {
    ticker,
    name,
    condition_type: cond,
    ...(amtType === 'qty' ? {coin_qty: coinQty} : {krw_amount: amt}),
    ...(cond === 'limit' ? {target_price: tp} : {}),
  };

  if (msg) msg.textContent = '등록 중...';
  try {
    const r = await fetch('/api/coin-buy', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(job) });
    const d = await r.json();
    if (d.ok) {
      if (msg) msg.innerHTML = '<span style="color:var(--green)">✅ 매수 잡 등록 완료</span>';
      document.getElementById('cb-name').value = '';
      document.getElementById('cb-ticker').value = '';
      document.getElementById('cb-ticker-display').textContent = '—';
      document.getElementById('cb-krw-amount').value = '';
      document.getElementById('cb-coin-qty').value = '';
      const wrap = document.getElementById('cb-cur-price-wrap');
      if (wrap) wrap.style.display = 'none';
      await ctLoadAll();
    } else {
      if (msg) msg.innerHTML = `<span style="color:var(--red)">❌ 등록 실패: ${d.error || ''}</span>`;
    }
  } catch (e) {
    if (msg) msg.innerHTML = `<span style="color:var(--red)">❌ 오류: ${e.message}</span>`;
  }
}

// ── 매수 잡 렌더링 ───────────────────────────────────────
function ctRenderBuyJobs() {
  const el = document.getElementById('cb-active-list');
  if (!el) return;
  const active = _ctBuyJobs.filter(j => j.status === 'active');
  if (!active.length) { el.innerHTML = `<div style="color:var(--muted);font-size:13px;padding:8px 0">없음</div>`; return; }
  el.innerHTML = active.map(j => `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <span style="font-weight:700">${j.name}</span>
          <span style="color:var(--muted);font-size:11px;margin-left:6px">${j.ticker}</span>
          <span style="margin-left:8px;font-size:11px;background:#f59e0b22;color:#f59e0b;padding:1px 6px;border-radius:4px">
            ${j.condition_type === 'market_krw' ? '시장가' : '지정가'}
          </span>
        </div>
        <button onclick="ctCancelJob('buy','${j.ticker}')"
          style="padding:3px 10px;border:1px solid var(--red);color:var(--red);border-radius:6px;background:none;font-size:12px;cursor:pointer">취소</button>
      </div>
      <div style="font-size:12px;color:var(--muted);margin-top:6px">
        매수금액: <b style="color:var(--text)">${Number(j.krw_amount || 0).toLocaleString()}원</b>
        ${j.target_price ? ` | 목표가: <b>${Number(j.target_price).toLocaleString()}원</b>` : ''}
        | 등록: ${j.created_at || '—'}
      </div>
    </div>`).join('');
}

// ── 매도 폼 ──────────────────────────────────────────────
function onCsNameFocus() { onCsNameInput(document.getElementById('cs-name')?.value || ''); }
function onCsNameInput(v) {
  const coins = v ? _filterCoins(v) : (_ctAccount?.holdings || []).map(h =>
    COIN_LIST.find(c => c.ticker === h.ticker) || {ticker: h.ticker, name: h.name || h.ticker, symbol: h.symbol || ''}
  ).slice(0, 8);
  _renderAcList('cs-ac-list', coins, (c) => {
    document.getElementById('cs-name').value = c.name;
    document.getElementById('cs-ticker').value = c.ticker;
    document.getElementById('cs-ticker-display').textContent = c.ticker;
    const holding = _ctAccount?.holdings?.find(h => h.ticker === c.ticker);
    if (holding) {
      document.getElementById('cs-qty').value = holding.qty;
      document.getElementById('cs-buyprice').value = holding.avg_price;
    }
    document.getElementById('cs-ac-list').style.display = 'none';
    csUpdateHint();
    csShowCurPrice(c.ticker);
  });
}
function hideCsAc() { setTimeout(() => { const el = document.getElementById('cs-ac-list'); if (el) el.style.display = 'none'; }, 150); }

function csSelectCoin(ticker, name, _symbol, qty, avgPrice) {
  document.getElementById('cs-name').value = name;
  document.getElementById('cs-ticker').value = ticker;
  document.getElementById('cs-ticker-display').textContent = ticker;
  document.getElementById('cs-qty').value = qty;
  document.getElementById('cs-buyprice').value = avgPrice;
  csShowCurPrice(ticker);
}

async function _showCoinCurPrice(ticker, wrapId, priceId, pctId) {
  const wrap = document.getElementById(wrapId);
  const el   = document.getElementById(priceId);
  const pct  = document.getElementById(pctId);
  if (!wrap || !el) return;
  try {
    const r = await fetch(`https://api.upbit.com/v1/ticker?markets=${ticker}`);
    const d = await r.json();
    if (!d?.[0]) return;
    const cur = d[0].trade_price;
    const chg = (d[0].signed_change_rate * 100).toFixed(2);
    el.textContent = `${cur.toLocaleString()}원`;
    if (pct) { pct.textContent = `${chg >= 0 ? '+' : ''}${chg}%`; pct.style.color = chg >= 0 ? 'var(--green)' : 'var(--red)'; }
    wrap.style.display = 'inline';
  } catch (_) {}
}

function csShowCurPrice(ticker) {
  _showCoinCurPrice(ticker, 'cs-cur-price-wrap', 'cs-cur-price', 'cs-cur-pct');
}

function csAmountTypeChange() {
  const v = document.querySelector('input[name="cs-amount-type"]:checked')?.value;
  const qtyEl    = document.getElementById('cs-qty');
  const qtyLabel = document.getElementById('cs-qty-label');
  const krwRow   = document.getElementById('cs-krw-row');
  if (qtyEl)    qtyEl.style.display    = v === 'krw' ? 'none' : '';
  if (qtyLabel) qtyLabel.style.display = v === 'krw' ? 'none' : '';
  if (krwRow)   krwRow.style.display   = v === 'krw' ? 'block' : 'none';
}

function csTypeChange() {
  const v    = document.querySelector('input[name="cs-type"]:checked')?.value;
  const hint = document.getElementById('cs-target-hint');
  if (hint) hint.textContent = v === 'price' ? '이 가격 이상일 때 시장가 매도' : '수수료 차감 후 순수익 달성 시 매도';
}

function csUpdateHint() {}

async function csRegister() {
  const ticker    = document.getElementById('cs-ticker')?.value;
  const name      = document.getElementById('cs-name')?.value;
  const amtType   = document.querySelector('input[name="cs-amount-type"]:checked')?.value || 'qty';
  const qty       = Number(document.getElementById('cs-qty')?.value || 0);
  const krwAmt    = Number(document.getElementById('cs-krw-amount')?.value || 0);
  const buyPrice  = Number(document.getElementById('cs-buyprice')?.value || 0);
  const type      = document.querySelector('input[name="cs-type"]:checked')?.value || 'pct';
  const target    = Number(document.getElementById('cs-target')?.value || 0);
  const msg       = document.getElementById('cs-msg');

  if (!ticker) { if (msg) msg.innerHTML = '<span style="color:var(--red)">코인을 선택해주세요</span>'; return; }
  if (amtType === 'qty' && qty <= 0) { if (msg) msg.innerHTML = '<span style="color:var(--red)">수량을 입력해주세요</span>'; return; }
  if (amtType === 'krw' && krwAmt < 5000) { if (msg) msg.innerHTML = '<span style="color:var(--red)">최소 5,000원 이상 입력해주세요</span>'; return; }
  if (target <= 0) { if (msg) msg.innerHTML = '<span style="color:var(--red)">목표값을 입력해주세요</span>'; return; }

  let finalQty = qty;
  if (amtType === 'krw') {
    const curPriceText = document.getElementById('cs-cur-price')?.textContent?.replace(/[^0-9.]/g, '');
    const curPrice = Number(curPriceText) || 0;
    if (curPrice <= 0) { if (msg) msg.innerHTML = '<span style="color:var(--red)">현재가를 확인할 수 없습니다. 코인을 다시 선택해주세요</span>'; return; }
    finalQty = Math.floor(krwAmt / curPrice * 1e8) / 1e8;
    if (finalQty <= 0) { if (msg) msg.innerHTML = '<span style="color:var(--red)">수량 계산 실패 — 금액을 확인해주세요</span>'; return; }
  }

  const job = { ticker, name, qty: finalQty, buy_price: buyPrice, target_type: type, target_value: target };
  if (msg) msg.textContent = '등록 중...';
  try {
    const r = await fetch('/api/coin-sell', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(job) });
    const d = await r.json();
    if (d.ok) {
      if (msg) msg.innerHTML = '<span style="color:var(--green)">✅ 매도 잡 등록 완료</span>';
      document.getElementById('cs-name').value = '';
      document.getElementById('cs-ticker').value = '';
      document.getElementById('cs-ticker-display').textContent = '—';
      document.getElementById('cs-qty').value = '';
      document.getElementById('cs-krw-amount').value = '';
      document.getElementById('cs-buyprice').value = '';
      document.getElementById('cs-target').value = '';
      document.getElementById('cs-cur-price-wrap').style.display = 'none';
      await ctLoadAll();
    } else {
      if (msg) msg.innerHTML = `<span style="color:var(--red)">❌ 등록 실패: ${d.error || ''}</span>`;
    }
  } catch (e) {
    if (msg) msg.innerHTML = `<span style="color:var(--red)">❌ 오류: ${e.message}</span>`;
  }
}

// ── 매도 잡 렌더링 ───────────────────────────────────────
function ctRenderSellJobs() {
  const el = document.getElementById('cs-active-list');
  if (!el) return;
  const active = _ctSellJobs.filter(j => j.status === 'active');
  if (!active.length) { el.innerHTML = `<div style="color:var(--muted);font-size:13px;padding:12px 0">없음</div>`; return; }
  el.innerHTML = active.map(j => {
    const uid = j.ticker + (j.created_at || '').replace(/\s/g,'');
    const typeLabel = {pct:'수익률', price:'지정가', amount:'수익금액'}[j.target_type] || j.target_type;
    return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <span style="font-weight:700">${j.name}</span>
          <span style="color:var(--muted);font-size:11px;margin-left:6px">${j.ticker}</span>
        </div>
        <button onclick="ctCancelJob('sell','${j.ticker}')"
          style="padding:3px 10px;border:1px solid var(--red);color:var(--red);border-radius:6px;background:none;font-size:12px;cursor:pointer">취소</button>
      </div>
      <div style="font-size:12px;color:var(--muted);margin-top:6px">
        수량: <b style="color:var(--text)">${Number(j.qty || 0).toFixed(8)}</b>
        | 평단: ${j.buy_price ? Number(j.buy_price).toLocaleString() + '원' : '—'}
        | 목표: <b>${typeLabel} ${j.target_value}${j.target_type === 'pct' ? '%' : '원'}</b>
      </div>
      <div id="ct-price-${uid}" style="font-size:12px;color:var(--muted);margin-top:4px">${_ctPriceCache[j.ticker]?.priceText || '현재가 로딩중...'}</div>
      <div id="ct-pnl-${uid}" style="font-size:12px;margin-top:2px">${_ctPriceCache[j.ticker]?.pnlHtml || '—'}</div>
    </div>`;
  }).join('');
}

// ── 사이클 폼 ────────────────────────────────────────────
function toggleSection(id) {
  document.getElementById(id)?.classList.toggle('open');
}

let _ccCurPrice = 0;

function onCcNameFocus() { onCcNameInput(document.getElementById('cc-name')?.value || ''); }
function onCcNameInput(v) {
  const list = v ? _filterCoins(v) : COIN_LIST.slice(0, 8);
  _renderAcList('cc-ac-list', list, (c) => {
    document.getElementById('cc-name').value = c.name;
    document.getElementById('cc-ticker').value = c.ticker;
    document.getElementById('cc-ticker-display').textContent = c.ticker;
    document.getElementById('cc-ac-list').style.display = 'none';
    _showCoinCurPrice(c.ticker, 'cc-cur-price-wrap', 'cc-cur-price', 'cc-cur-pct');
    // 보유코인 매도먼저 모드일 때 avg_price, hold_qty 자동입력
    const startType = document.querySelector('input[name="cc-start-type"]:checked')?.value;
    if (startType === 'sell') {
      const h = _ctAccount?.holdings?.find(hh => hh.ticker === c.ticker);
      if (h) {
        const avgEl  = document.getElementById('cc-avg-price');
        const qtyEl  = document.getElementById('cc-hold-qty');
        if (avgEl) avgEl.value = h.avg_price;
        if (qtyEl) qtyEl.value = h.qty;
      }
    }
  });
}
function hideCcAc() { setTimeout(() => { const el = document.getElementById('cc-ac-list'); if (el) el.style.display = 'none'; }, 150); }

async function ccShowCurPrice(ticker) {
  const wrap = document.getElementById('cc-cur-price-wrap');
  const el   = document.getElementById('cc-cur-price');
  const pct  = document.getElementById('cc-cur-pct');
  _ccCurPrice = 0;
  if (wrap) wrap.style.display = 'none';
  try {
    const r = await fetch(`https://api.upbit.com/v1/ticker?markets=${ticker}`);
    const d = await r.json();
    if (!d?.[0]) return;
    _ccCurPrice = d[0].trade_price;
    const chg = (d[0].signed_change_rate * 100).toFixed(2);
    if (el) el.textContent = `${_ccCurPrice.toLocaleString()}원`;
    if (pct) { pct.textContent = `${chg >= 0 ? '+' : ''}${chg}%`; pct.style.color = chg >= 0 ? 'var(--green)' : 'var(--red)'; }
    if (wrap) wrap.style.display = 'inline-flex';
    ccUpdateQtyHint();
  } catch (_) {}
}

function ccAmtModeChange() {
  const mode = document.querySelector('input[name="cc-amt-mode"]:checked')?.value;
  const krwRow = document.getElementById('cc-krw-row');
  const qtyRow = document.getElementById('cc-qty-row');
  if (krwRow) krwRow.style.display = mode === 'qty' ? 'none' : 'block';
  if (qtyRow) qtyRow.style.display = mode === 'qty' ? 'block' : 'none';
  ccUpdateQtyHint();
}

function ccUpdateQtyHint() {
  const hint = document.getElementById('cc-qty-hint');
  if (!hint) return;
  const qty = parseFloat(document.getElementById('cc-coin-qty')?.value || '0');
  if (_ccCurPrice > 0 && qty > 0) {
    const est = Math.round(qty * _ccCurPrice).toLocaleString();
    hint.textContent = `현재가 기준 약 ${est}원`;
    hint.style.color = 'var(--text)';
  } else {
    hint.textContent = '사이클마다 이 수량으로 매수';
    hint.style.color = 'var(--muted)';
  }
}

function ccCondChange() {
  const v = document.querySelector('input[name="cc-cond"]:checked')?.value;
  const row = document.getElementById('cc-limit-row');
  if (row) row.style.display = v === 'limit' ? 'block' : 'none';
}

function ccAmountTypeChange() {
  const v = document.querySelector('input[name="cc-amount-type"]:checked')?.value;
  const krwRow = document.getElementById('cc-krw-row');
  const qtyRow = document.getElementById('cc-qty-row');
  if (krwRow) krwRow.style.display = v === 'qty' ? 'none' : 'block';
  if (qtyRow) qtyRow.style.display = v === 'qty' ? 'block' : 'none';
}

function ccStartTypeChange() {
  const v = document.querySelector('input[name="cc-start-type"]:checked')?.value;
  const buySection  = document.getElementById('cc-buy-first-section');
  const sellSection = document.getElementById('cc-sell-first-section');
  if (buySection)  buySection.style.display  = v === 'sell' ? 'none' : 'block';
  if (sellSection) sellSection.style.display = v === 'sell' ? 'block' : 'none';
}

function ccRebuyTypeChange() {
  const v = document.querySelector('input[name="cc-rebuy-type"]:checked')?.value;
  const krwRow = document.getElementById('cc-rebuy-krw-row');
  const qtyRow = document.getElementById('cc-rebuy-qty-row');
  if (krwRow) krwRow.style.display = v === 'qty' ? 'none' : 'block';
  if (qtyRow) qtyRow.style.display = v === 'qty' ? 'block' : 'none';
}

async function ccRegister() {
  const ticker     = document.getElementById('cc-ticker')?.value;
  const name       = document.getElementById('cc-name')?.value;
  const startType  = document.querySelector('input[name="cc-start-type"]:checked')?.value || 'buy';
  const takePct    = Number(document.getElementById('cc-take-pct')?.value || 3);
  const rebuyDrop  = Number(document.getElementById('cc-rebuy-drop')?.value || 2);
  const repeatTake = Number(document.getElementById('cc-repeat-take')?.value || 2);
  const maxCycles  = Number(document.getElementById('cc-max-cycles')?.value || 0);
  const msg        = document.getElementById('cc-msg');

  if (!ticker) { if (msg) msg.innerHTML = '<span style="color:var(--red)">코인을 선택해주세요</span>'; return; }

  let job;

  if (startType === 'sell') {
    // 보유 코인 매도 먼저
    const avgPrice  = Number(document.getElementById('cc-avg-price')?.value || 0);
    const holdQty   = Number(document.getElementById('cc-hold-qty')?.value || 0);
    const rebuyType = document.querySelector('input[name="cc-rebuy-type"]:checked')?.value || 'krw';
    const rebuyKrw  = Number(document.getElementById('cc-rebuy-krw-amount')?.value || 0);
    const rebuyQty  = Number(document.getElementById('cc-rebuy-coin-qty')?.value || 0);

    if (avgPrice <= 0) { if (msg) msg.innerHTML = '<span style="color:var(--red)">평균 매수가를 입력해주세요</span>'; return; }
    if (holdQty <= 0)  { if (msg) msg.innerHTML = '<span style="color:var(--red)">매도 수량을 입력해주세요</span>'; return; }
    if (rebuyType === 'krw' && rebuyKrw < 5000) { if (msg) msg.innerHTML = '<span style="color:var(--red)">재매수 금액은 5,000원 이상</span>'; return; }
    if (rebuyType === 'qty' && rebuyQty <= 0)   { if (msg) msg.innerHTML = '<span style="color:var(--red)">재매수 수량을 입력해주세요</span>'; return; }

    const sellPrice = Math.ceil(avgPrice * (1 + COIN_FEE) * (1 + takePct / 100) / (1 - COIN_FEE));
    job = {
      ticker, name,
      condition_type: 'holding_sell',
      take_pct: takePct, rebuy_drop: rebuyDrop, repeat_take: repeatTake, max_cycles: maxCycles,
      phase: 'holding', buy_price: avgPrice, hold_qty: holdQty, sell_price: sellPrice,
      ...(rebuyType === 'qty' ? {coin_qty: rebuyQty} : {krw_amount: rebuyKrw}),
      cycle_count: 0,
    };
  } else {
    // 신규 매수
    const cond      = document.querySelector('input[name="cc-cond"]:checked')?.value || 'market_krw';
    const amtType   = document.querySelector('input[name="cc-amount-type"]:checked')?.value || 'krw';
    const buyTarget = Number(document.getElementById('cc-buy-price')?.value || 0);
    const krwAmt    = Number(document.getElementById('cc-krw-amount')?.value || 0);
    const coinQty   = Number(document.getElementById('cc-coin-qty')?.value || 0);

    if (amtType === 'krw' && krwAmt < 5000) { if (msg) msg.innerHTML = '<span style="color:var(--red)">최소 매수금액은 5,000원입니다</span>'; return; }
    if (amtType === 'qty' && coinQty <= 0)  { if (msg) msg.innerHTML = '<span style="color:var(--red)">코인 수량을 입력해주세요</span>'; return; }

    job = {
      ticker, name,
      condition_type: cond,
      ...(cond === 'limit' ? {buy_target_price: buyTarget} : {}),
      ...(amtType === 'qty' ? {coin_qty: coinQty} : {krw_amount: krwAmt}),
      take_pct: takePct, rebuy_drop: rebuyDrop, repeat_take: repeatTake, max_cycles: maxCycles,
      phase: 'waiting_buy', cycle_count: 0,
    };
  }

  if (msg) msg.textContent = '등록 중...';
  try {
    const r = await fetch('/api/coin-cycle', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(job) });
    const d = await r.json();
    if (d.ok) {
      if (msg) msg.innerHTML = '<span style="color:var(--green)">✅ 사이클 잡 등록 완료</span>';
      ['cc-name','cc-krw-amount','cc-coin-qty','cc-avg-price','cc-hold-qty',
       'cc-rebuy-krw-amount','cc-rebuy-coin-qty'].forEach(id => {
        const el = document.getElementById(id); if (el) el.value = '';
      });
      document.getElementById('cc-ticker').value = '';
      document.getElementById('cc-ticker-display').textContent = '—';
      const wrap = document.getElementById('cc-cur-price-wrap');
      if (wrap) wrap.style.display = 'none';
      await ctLoadAll();
    } else {
      if (msg) msg.innerHTML = `<span style="color:var(--red)">❌ 등록 실패: ${d.error || ''}</span>`;
    }
  } catch (e) {
    if (msg) msg.innerHTML = `<span style="color:var(--red)">❌ 오류: ${e.message}</span>`;
  }
}

// ── 사이클 잡 렌더링 ─────────────────────────────────────
function ctRenderCycleJobs() {
  const el = document.getElementById('cc-active-list');
  if (!el) return;
  const active = _ctCycleJobs.filter(j => !['done','cancelled','stopped'].includes(j.status));
  if (!active.length) { el.innerHTML = `<div style="color:var(--muted);font-size:13px;padding:8px 0">없음</div>`; return; }
  const phaseLabel = {waiting_buy:'매수 대기', holding:'보유 중', waiting_rebuy:'재매수 대기'};
  const iStyle = 'width:100%;box-sizing:border-box;padding:5px 8px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:12px';
  el.innerHTML = active.map(j => {
    const uid    = j.ticker + (j.created_at || '').replace(/\s/g,'');
    const pLabel = phaseLabel[j.phase] || j.phase;
    const pColor = {holding:'var(--green)', waiting_rebuy:'#f59e0b'}[j.phase] || 'var(--muted)';
    const safeCA = (j.created_at || '').replace(/'/g, "\\'");
    return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <span style="font-weight:700">${j.name}</span>
          <span style="color:var(--muted);font-size:11px;margin-left:6px">${j.ticker}</span>
          <span style="margin-left:8px;font-size:11px;color:${pColor};font-weight:600">${pLabel}</span>
        </div>
        <div style="display:flex;gap:6px">
          <button onclick="ctToggleEdit('${uid}')"
            style="padding:3px 10px;border:1px solid var(--primary);color:var(--primary);border-radius:6px;background:none;font-size:12px;cursor:pointer">수정</button>
          <button onclick="ctCancelJob('cycle','${j.ticker}','${safeCA}')"
            style="padding:3px 10px;border:1px solid var(--red);color:var(--red);border-radius:6px;background:none;font-size:12px;cursor:pointer">중단</button>
        </div>
      </div>
      <div style="font-size:12px;color:var(--muted);margin-top:6px">
        ${j.krw_amount ? `매수금액: ${Number(j.krw_amount).toLocaleString()}원` : j.coin_qty ? `매수수량: ${j.coin_qty}개` : ''}
        | 수익: ${j.take_pct}% / 재매수: -${j.rebuy_drop}% / 반복: ${j.repeat_take}%
        | 사이클: ${j.cycle_count || 0}${j.max_cycles > 0 ? '/'+j.max_cycles : ''}회
      </div>
      <div id="ct-price-${uid}" style="font-size:12px;color:var(--muted);margin-top:4px">${_ctPriceCache[j.ticker]?.priceText || '현재가 로딩중...'}</div>
      ${j.buy_price ? `<div id="ct-pnl-${uid}" style="font-size:12px;margin-top:2px">${_ctPriceCache[j.ticker]?.pnlHtml || '—'}</div>` : ''}
      ${j.sell_price ? `<div style="font-size:11px;color:var(--muted);margin-top:3px">매도 목표: ${Math.ceil(Number(j.sell_price)).toLocaleString()}원/개</div>` : ''}
      ${j.rebuy_price ? `<div style="font-size:11px;color:var(--muted);margin-top:3px">재매수 목표: ${Math.floor(Number(j.rebuy_price)).toLocaleString()}원/개</div>` : ''}
      ${j.buy_target_price ? `<div style="font-size:11px;color:var(--muted);margin-top:3px">매수 목표: ${Math.floor(Number(j.buy_target_price)).toLocaleString()}원/개</div>` : ''}
      <!-- 수정 폼 -->
      <div id="ct-edit-${uid}" style="display:none;margin-top:10px;padding:10px;background:var(--bg);border-radius:8px;border:1px solid var(--border)">
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;margin-bottom:8px">
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:3px">수익률%</div>
            <input id="ct-edit-take-${uid}" type="number" value="${j.take_pct}" min="0.1" step="0.1" style="${iStyle}" />
          </div>
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:3px">재매수하락%</div>
            <input id="ct-edit-rebuy-${uid}" type="number" value="${j.rebuy_drop}" min="0.1" step="0.1" style="${iStyle}" />
          </div>
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:3px">반복수익%</div>
            <input id="ct-edit-repeat-${uid}" type="number" value="${j.repeat_take}" min="0.1" step="0.1" style="${iStyle}" />
          </div>
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:3px">최대횟수</div>
            <input id="ct-edit-max-${uid}" type="number" value="${j.max_cycles}" min="0" step="1" style="${iStyle}" />
          </div>
        </div>
        <div style="display:flex;gap:6px">
          <button onclick="ctSaveJobEdit('${j.ticker}','${safeCA}','${uid}')"
            style="flex:1;padding:5px;background:var(--primary);color:#fff;border:none;border-radius:6px;font-size:12px;cursor:pointer">저장</button>
          <button onclick="ctToggleEdit('${uid}')"
            style="flex:1;padding:5px;background:none;border:1px solid var(--border);color:var(--muted);border-radius:6px;font-size:12px;cursor:pointer">취소</button>
        </div>
        <div id="ct-edit-msg-${uid}" style="font-size:11px;margin-top:5px;text-align:center"></div>
      </div>
    </div>`;
  }).join('');
}

function ctToggleEdit(uid) {
  const el = document.getElementById(`ct-edit-${uid}`);
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

async function ctSaveJobEdit(ticker, createdAt, uid) {
  const takePct    = Number(document.getElementById(`ct-edit-take-${uid}`)?.value || 0);
  const rebuyDrop  = Number(document.getElementById(`ct-edit-rebuy-${uid}`)?.value || 0);
  const repeatTake = Number(document.getElementById(`ct-edit-repeat-${uid}`)?.value || 0);
  const maxCycles  = Number(document.getElementById(`ct-edit-max-${uid}`)?.value || 0);
  const msgEl      = document.getElementById(`ct-edit-msg-${uid}`);

  const job = _ctCycleJobs.find(j => j.ticker === ticker && j.created_at === createdAt);
  const updates = { take_pct: takePct, rebuy_drop: rebuyDrop, repeat_take: repeatTake, max_cycles: maxCycles };
  if (job?.phase === 'holding' && job?.buy_price) {
    updates.sell_price = Math.ceil(job.buy_price * (1 + COIN_FEE) * (1 + takePct / 100) / (1 - COIN_FEE));
  }

  if (msgEl) msgEl.textContent = '저장 중...';
  try {
    const r = await fetch('/api/coin-cycle', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ ticker, created_at: createdAt, ...updates }),
    });
    const d = await r.json();
    if (d.ok) {
      await ctLoadAll();
    } else {
      if (msgEl) msgEl.innerHTML = `<span style="color:var(--red)">실패: ${d.error || ''}</span>`;
    }
  } catch (e) {
    if (msgEl) msgEl.innerHTML = `<span style="color:var(--red)">오류: ${e.message}</span>`;
  }
}

// ── 히스토리 렌더링 ──────────────────────────────────────
function ctRenderHistory() {
  const el = document.getElementById('ct-history-list');
  if (!el) return;
  const done = [
    ..._ctBuyJobs.filter(j => ['done','cancelled'].includes(j.status)),
    ..._ctSellJobs.filter(j => ['done','cancelled'].includes(j.status)),
    ..._ctCycleJobs.filter(j => ['done','cancelled','stopped'].includes(j.status)),
  ].sort((a, b) => (b.executed_at || b.cancelled_at || b.created_at || '').localeCompare(
                   a.executed_at || a.cancelled_at || a.created_at || ''));

  if (!done.length) { el.innerHTML = `<div style="color:var(--muted);font-size:13px;padding:12px 0">없음</div>`; return; }
  el.innerHTML = done.slice(0, 5).map(j => {
    const isCancelled = j.status === 'cancelled' || j.status === 'stopped';
    const statusColor = isCancelled ? 'var(--muted)' : 'var(--green)';
    const statusLabel = isCancelled ? '취소' : '완료';
    return `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);font-size:12px">
      <div>
        <span style="font-weight:600">${j.name}</span>
        <span style="color:var(--muted);margin-left:6px">${j.ticker}</span>
        ${j.exec_price ? `<span style="margin-left:6px;color:var(--text)">@ ${Number(j.exec_price).toLocaleString()}원</span>` : ''}
        ${j.pnl_pct != null ? `<span style="margin-left:6px;color:${j.pnl_pct >= 0 ? 'var(--green)' : 'var(--red)'}">${j.pnl_pct >= 0 ? '+' : ''}${j.pnl_pct}%</span>` : ''}
      </div>
      <div style="text-align:right;color:var(--muted)">
        <span style="color:${statusColor};font-weight:600">${statusLabel}</span>
        <span style="margin-left:8px">${j.executed_at || j.cancelled_at || j.created_at || '—'}</span>
      </div>
    </div>`;
  }).join('');
}

// ── 잡 취소 ──────────────────────────────────────────────
async function ctCancelJob(type, ticker, createdAt) {
  if (!confirm(`${ticker} 잡을 취소하시겠습니까?`)) return;
  const endpoint = type === 'buy' ? 'coin-buy' : type === 'sell' ? 'coin-sell' : 'coin-cycle';
  const qs = `ticker=${encodeURIComponent(ticker)}${createdAt ? `&created_at=${encodeURIComponent(createdAt)}` : ''}`;
  try {
    const r = await fetch(`/api/${endpoint}?${qs}`, { method:'DELETE' });
    const d = await r.json();
    if (d.ok) await ctLoadAll();
    else alert('취소 실패: ' + (d.error || ''));
  } catch (e) {
    alert('오류: ' + e.message);
  }
}

// ══════════════════════════════════════════════════════════
// 그리드 트레이딩
// ══════════════════════════════════════════════════════════
let _cgCurrentPrice = 0;

function onCgNameFocus() { onCgNameInput(document.getElementById('cg-name')?.value || ''); }
function onCgNameInput(v) {
  _renderAcList('cg-ac-list', _filterCoins(v), (c) => {
    document.getElementById('cg-name').value = c.name;
    document.getElementById('cg-ticker').value = c.ticker;
    document.getElementById('cg-ticker-display').textContent = c.ticker;
    document.getElementById('cg-ac-list').style.display = 'none';
    _cgFetchAndAutoFill(c.ticker);
  });
}
function hideCgAc() { setTimeout(() => { const el = document.getElementById('cg-ac-list'); if (el) el.style.display = 'none'; }, 150); }

async function _cgFetchAndAutoFill(ticker) {
  const hint = document.getElementById('cg-price-hint');
  if (hint) hint.textContent = '현재가 조회 중...';
  _showCoinCurPrice(ticker, 'cg-cur-price-wrap', 'cg-cur-price', 'cg-cur-pct');
  try {
    const r = await fetch(`/api/coin-price?markets=${ticker}`);
    const d = await r.json();
    const price = d?.[0]?.trade_price;
    if (!price) { if (hint) hint.textContent = ''; return; }
    _cgCurrentPrice = price;
    if (hint) hint.textContent = `현재가 ${price.toLocaleString()}원 — 격자 수와 간격을 설정하면 범위가 자동 계산됩니다`;
    ctGridPreview();
  } catch (e) {
    if (hint) hint.textContent = '';
  }
}

function ctToggleGridForm() {
  const form = document.getElementById('ct-grid-form');
  if (!form) return;
  const opening = form.style.display === 'none';
  form.style.display = opening ? 'block' : 'none';
  if (opening) {
    ['cg-name','cg-ticker','cg-krw','cg-reinit'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    document.getElementById('cg-count').value = '20';
    document.getElementById('cg-pct').value = '1.0';
    _cgCurrentPrice = 0;
    const tickerDisp = document.getElementById('cg-ticker-display');
    if (tickerDisp) tickerDisp.textContent = '—';
    const priceWrap = document.getElementById('cg-cur-price-wrap');
    if (priceWrap) priceWrap.style.display = 'none';
    const hint = document.getElementById('cg-price-hint');
    if (hint) hint.textContent = '';
    document.getElementById('cg-preview').textContent = '';
    document.getElementById('cg-msg').textContent = '';
  }
}

function _cgCalcRange(count, pct, curPrice) {
  const nBelow = Math.ceil(count / 2);
  const nAbove = count - nBelow;
  const lower  = Math.round(curPrice / Math.pow(1 + pct / 100, nBelow));
  const upper  = Math.round(curPrice * Math.pow(1 + pct / 100, nAbove + 1));
  return { lower, upper };
}

function ctGridPreview() {
  const count = +document.getElementById('cg-count')?.value || 0;
  const pct   = +document.getElementById('cg-pct')?.value   || 1.0;
  const krw   = +document.getElementById('cg-krw')?.value   || 0;
  const el    = document.getElementById('cg-preview');
  if (!el) return;
  if (!count || !_cgCurrentPrice) { el.textContent = _cgCurrentPrice ? '' : '코인을 먼저 선택하세요'; return; }
  if (pct <= 0.1) { el.textContent = '간격은 0.1% 초과여야 합니다'; return; }

  const { lower, upper } = _cgCalcRange(count, pct, _cgCurrentPrice);
  const totalKrw   = count * krw;
  const netPerGrid = (pct - 0.1).toFixed(2);
  el.innerHTML = `범위: <b>${lower.toLocaleString()}~${upper.toLocaleString()}원</b> &nbsp;|&nbsp; 총 투자금: <b>${totalKrw.toLocaleString()}원</b> &nbsp;|&nbsp; 격자당 순이익: <b>≈${netPerGrid}%</b>`;
}

async function ctAddGridJob() {
  const msg    = document.getElementById('cg-msg');
  const ticker = (document.getElementById('cg-ticker')?.value || '').trim().toUpperCase();
  const name   = (document.getElementById('cg-name')?.value   || '').trim();
  const count  = +document.getElementById('cg-count')?.value  || 0;
  const pct    = +document.getElementById('cg-pct')?.value    || 1.0;
  const krw    = +document.getElementById('cg-krw')?.value    || 0;

  if (!ticker) { if (msg) msg.innerHTML = '<span style="color:var(--red)">코인명을 검색해서 선택하세요</span>'; return; }
  if (!_cgCurrentPrice) { if (msg) msg.innerHTML = '<span style="color:var(--red)">코인명을 다시 선택해 현재가를 조회하세요</span>'; return; }
  if (count < 4) { if (msg) msg.innerHTML = '<span style="color:var(--red)">격자 수는 4개 이상이어야 합니다</span>'; return; }
  if (krw < 5000) { if (msg) msg.innerHTML = '<span style="color:var(--red)">격자당 금액 5,000원 이상</span>'; return; }
  if (pct <= 0.1) { if (msg) msg.innerHTML = '<span style="color:var(--red)">격자 간격은 수수료(0.1%) 초과여야 합니다</span>'; return; }

  const { lower, upper } = _cgCalcRange(count, pct, _cgCurrentPrice);

  const reinitMin = +document.getElementById('cg-reinit')?.value || 0;

  const finalTicker = ticker.startsWith('KRW-') ? ticker : `KRW-${ticker}`;
  if (msg) msg.innerHTML = '<span style="color:var(--muted)">등록 중...</span>';

  const payload = {
    name:         name || `${finalTicker} 그리드`,
    ticker:       finalTicker,
    grid_pct:     pct,
    lower_price:  lower,
    upper_price:  upper,
    krw_per_grid: krw,
  };
  if (reinitMin >= 10) payload.auto_reinit_minutes = reinitMin;

  try {
    const r = await fetch('/api/coin-grid', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (r.ok && !d.error) {
      if (msg) msg.innerHTML = '<span style="color:var(--green)">등록 완료 — 30초 내 초기화 시작</span>';
      await ctLoadAll();
      document.getElementById('ct-grid-form').style.display = 'none';
    } else {
      if (msg) msg.innerHTML = `<span style="color:var(--red)">${d.error || '저장 실패'}</span>`;
    }
  } catch (e) {
    if (msg) msg.innerHTML = `<span style="color:var(--red)">오류: ${e.message}</span>`;
  }
}

async function ctStopGridJob(id, name) {
  if (!confirm(`"${name}" 그리드를 중단하시겠습니까?\n미체결 주문이 모두 취소됩니다.`)) return;
  try {
    const r = await fetch(`/api/coin-grid?id=${encodeURIComponent(id)}`, { method: 'DELETE' });
    const d = await r.json();
    if (d.ok) { await ctLoadAll(); }
    else alert('중단 실패: ' + (d.error || ''));
  } catch (e) { alert('오류: ' + e.message); }
}

let _cgEditingId = null;

function ctToggleGridEdit(id) {
  _cgEditingId = (_cgEditingId === id) ? null : id;
  ctRenderGridJobs();
}

async function ctSaveGridEdit(id) {
  const job = (_ctGridJobs || []).find(j => j.id === id);
  if (!job) return;

  const lower   = +document.getElementById(`cg-edit-lower-${id}`)?.value || 0;
  const upper   = +document.getElementById(`cg-edit-upper-${id}`)?.value || 0;
  const reinitV = document.getElementById(`cg-edit-reinit-${id}`)?.value;
  const reinit  = reinitV !== '' ? +reinitV : null;

  if (lower && upper && lower >= upper) {
    alert('하한가는 상한가보다 작아야 합니다');
    return;
  }

  const patch = {};
  const rangeChanged = lower && upper && (lower !== +job.lower_price || upper !== +job.upper_price);

  if (lower && upper) {
    patch.lower_price = lower;
    patch.upper_price = upper;
    if (rangeChanged && ['active', 'init'].includes(job.status)) {
      patch.status = 'reinit';
    }
  }

  if (reinitV !== '') {
    patch.auto_reinit_minutes = (reinit >= 10) ? reinit : null;
  }

  try {
    const r = await fetch(`/api/coin-grid?id=${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    const d = await r.json();
    if (r.ok && !d.error) {
      _cgEditingId = null;
      await ctLoadAll();
    } else {
      alert('저장 실패: ' + (d.error || ''));
    }
  } catch (e) {
    alert('오류: ' + e.message);
  }
}

function ctRenderGridJobs() {
  const el = document.getElementById('ct-grid-list');
  if (!el) return;

  const jobs = (Array.isArray(_ctGridJobs) ? _ctGridJobs : []).filter(j => j.status !== 'stopped');
  if (!jobs.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">없음</div>';
    return;
  }

  el.innerHTML = jobs.map(j => {
    const grids     = j.grids || [];
    const buyWait   = grids.filter(g => g.state === 'buy_waiting').length;
    const sellWait  = grids.filter(g => g.state === 'sell_waiting').length;
    const idle      = grids.filter(g => g.state === 'idle').length;
    const total     = grids.length;

    const statusMap = {
      init:    ['초기화중',   'var(--muted)'],
      reinit:  ['재초기화중', 'var(--muted)'],
      active:  ['활성',       'var(--green)'],
      stopping:['중단중',     'var(--red)'],
      stopped: ['중단됨',     'var(--muted)'],
    };
    const [statusLabel, statusColor] = statusMap[j.status] || ['알 수 없음', 'var(--muted)'];

    const pnl    = j.total_profit_krw || 0;
    const pnlClr = pnl >= 0 ? 'var(--green)' : 'var(--red)';
    const canStop = ['active', 'init', 'reinit'].includes(j.status);

    // 그리드 미니 시각화 (최대 20칸)
    const visGrids = grids.slice(0, 20);
    const barHtml  = visGrids.map(g => {
      const c = g.state === 'sell_waiting' ? '#f59e0b'
              : g.state === 'buy_waiting'  ? 'var(--primary)'
              : 'var(--border)';
      return `<div title="${Number(g.level).toLocaleString()}원 (${g.state})"
        style="flex:1;height:14px;background:${c};border-radius:2px;min-width:4px"></div>`;
    }).join('');

    return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <div>
          <span style="font-weight:700">${j.name}</span>
          <span style="color:var(--muted);font-size:11px;margin-left:6px">${j.ticker}</span>
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <span style="color:${statusColor};font-size:12px;font-weight:600">${statusLabel}</span>
          ${canStop ? `<button onclick="ctToggleGridEdit('${j.id}')"
            style="padding:2px 9px;border:1px solid var(--border);border-radius:5px;background:none;font-size:11px;color:var(--muted);cursor:pointer">${_cgEditingId === j.id ? '닫기' : '편집'}</button>` : ''}
          ${canStop ? `<button onclick="ctStopGridJob('${j.id}','${j.name}')"
            style="padding:2px 9px;border:1px solid var(--red);border-radius:5px;background:none;font-size:11px;color:var(--red);cursor:pointer">중단</button>` : ''}
        </div>
      </div>
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">
        범위: ${Number(j.lower_price).toLocaleString()} ~ ${Number(j.upper_price).toLocaleString()}원
        &nbsp;|&nbsp; 간격: ${j.grid_pct}%
        &nbsp;|&nbsp; 격자당: ${Number(j.krw_per_grid).toLocaleString()}원
        ${j.auto_reinit_minutes ? `&nbsp;|&nbsp; <span style="color:var(--primary)">이탈재설정 ${j.auto_reinit_minutes}분</span>` : ''}
      </div>
      <div style="display:flex;gap:3px;margin-bottom:6px" title="파랑=매수대기 / 노랑=매도대기 / 회색=미활성">${barHtml}</div>
      <div style="display:flex;justify-content:space-between;font-size:12px">
        <div style="color:var(--muted)">
          매수대기 <b style="color:var(--primary)">${buyWait}</b>
          &nbsp; 매도대기 <b style="color:#f59e0b">${sellWait}</b>
          &nbsp; 총 ${total}격자
        </div>
        <div>
          <span style="color:var(--muted)">누적수익 </span>
          <span style="color:${pnlClr};font-weight:700">${pnl >= 0 ? '+' : ''}${Math.round(pnl).toLocaleString()}원</span>
          <span style="color:var(--muted);margin-left:8px">${j.trade_count||0}회</span>
        </div>
      </div>
      ${_cgEditingId === j.id ? `
      <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:8px">
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:3px">하한가 (원)</div>
            <input id="cg-edit-lower-${j.id}" type="number" value="${j.lower_price}"
              style="width:100%;padding:5px 7px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:12px;box-sizing:border-box">
          </div>
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:3px">상한가 (원)</div>
            <input id="cg-edit-upper-${j.id}" type="number" value="${j.upper_price}"
              style="width:100%;padding:5px 7px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:12px;box-sizing:border-box">
          </div>
          <div>
            <div style="font-size:10px;color:var(--muted);margin-bottom:3px">이탈재설정 (분)</div>
            <input id="cg-edit-reinit-${j.id}" type="number" min="10" placeholder="미설정"
              value="${j.auto_reinit_minutes || ''}"
              style="width:100%;padding:5px 7px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--text);font-size:12px;box-sizing:border-box">
          </div>
        </div>
        <div style="font-size:10px;color:var(--muted);margin-bottom:8px">* 하한/상한 변경 시 기존 주문 전부 취소 후 재초기화됩니다</div>
        <div style="display:flex;gap:8px">
          <button onclick="ctSaveGridEdit('${j.id}')"
            style="flex:1;padding:6px;background:var(--primary);color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">저장</button>
          <button onclick="ctToggleGridEdit('${j.id}')"
            style="padding:6px 14px;background:none;border:1px solid var(--border);border-radius:6px;font-size:12px;color:var(--muted);cursor:pointer">취소</button>
        </div>
      </div>` : ''}
    </div>`;
  }).join('');
}

// ── 시그널 타입별 파라미터 UI ─────────────────────────────
const SIGNAL_PARAM_UI = {
  btc_pump_lag: `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
      <div><div style="font-size:10px;color:var(--muted);margin-bottom:2px">BTC 최소 상승률 (%)</div>
        <input id="cs-p-btc_rise_pct" type="number" value="2" step="0.1" style="width:100%;padding:5px 7px;border:1px solid var(--border);border-radius:5px;background:var(--bg);color:var(--text);font-size:12px;box-sizing:border-box"></div>
      <div><div style="font-size:10px;color:var(--muted);margin-bottom:2px">대상 코인 최대 상승률 (%)</div>
        <input id="cs-p-coin_max_pct" type="number" value="1" step="0.1" style="width:100%;padding:5px 7px;border:1px solid var(--border);border-radius:5px;background:var(--bg);color:var(--text);font-size:12px;box-sizing:border-box"></div>
    </div>
    <div style="margin-top:6px;font-size:11px;color:var(--muted)">BTC 24h 상승률 ≥ X% 이면서 대상 코인 24h 상승률 &lt; Y% 일 때 매수</div>`,
  dip_buy: `
    <div><div style="font-size:10px;color:var(--muted);margin-bottom:2px">24h 고점 대비 최소 하락률 (%)</div>
      <input id="cs-p-dip_pct" type="number" value="5" step="0.5" style="width:100%;padding:5px 7px;border:1px solid var(--border);border-radius:5px;background:var(--bg);color:var(--text);font-size:12px;box-sizing:border-box"></div>
    <div style="margin-top:6px;font-size:11px;color:var(--muted)">24h 고점 대비 X% 이상 하락 시 반등 매수</div>`,
  price_breakout: `
    <div><div style="font-size:10px;color:var(--muted);margin-bottom:2px">돌파 기준가 (원)</div>
      <input id="cs-p-breakout_price" type="number" placeholder="예: 2500" style="width:100%;padding:5px 7px;border:1px solid var(--border);border-radius:5px;background:var(--bg);color:var(--text);font-size:12px;box-sizing:border-box"></div>
    <div style="margin-top:6px;font-size:11px;color:var(--muted)">현재가 ≥ 기준가 돌파 시 즉시 매수</div>`,
  btc_dump_sell: `
    <div><div style="font-size:10px;color:var(--muted);margin-bottom:2px">BTC 최소 하락률 (%)</div>
      <input id="cs-p-btc_drop_pct" type="number" value="3" step="0.5" style="width:100%;padding:5px 7px;border:1px solid var(--border);border-radius:5px;background:var(--bg);color:var(--text);font-size:12px;box-sizing:border-box"></div>
    <div style="margin-top:6px;font-size:11px;color:var(--muted)">BTC 24h 하락률 ≥ X% 시 보유 코인 강제 매도</div>`,
};

const SIGNAL_TYPE_LABELS = {
  btc_pump_lag:   'BTC 급등 추종',
  dip_buy:        '급락 반등',
  price_breakout: '가격 돌파',
  btc_dump_sell:  'BTC 급락 매도',
};

function ctSignalTypeChange() {
  const type = document.getElementById('cs-sig-type')?.value;
  const params = document.getElementById('cs-sig-params');
  const buyFields = document.getElementById('cs-sig-buy-fields');
  if (params) params.innerHTML = SIGNAL_PARAM_UI[type] || '';
  if (buyFields) buyFields.style.display = type === 'btc_dump_sell' ? 'none' : '';
}

function ctToggleSignalForm() {
  const form = document.getElementById('ct-signal-form');
  if (!form) return;
  const isHidden = form.style.display === 'none';
  form.style.display = isHidden ? 'block' : 'none';
  if (isHidden) ctSignalTypeChange();
}

function _ctSignalGetParams() {
  const type = document.getElementById('cs-sig-type')?.value;
  const params = {};
  if (type === 'btc_pump_lag') {
    params.btc_rise_pct = +document.getElementById('cs-p-btc_rise_pct')?.value || 2;
    params.coin_max_pct = +document.getElementById('cs-p-coin_max_pct')?.value || 1;
  } else if (type === 'dip_buy') {
    params.dip_pct = +document.getElementById('cs-p-dip_pct')?.value || 5;
  } else if (type === 'price_breakout') {
    params.breakout_price = +document.getElementById('cs-p-breakout_price')?.value || 0;
  } else if (type === 'btc_dump_sell') {
    params.btc_drop_pct = +document.getElementById('cs-p-btc_drop_pct')?.value || 3;
  }
  return params;
}

async function ctAddSignalJob() {
  const msg = document.getElementById('cs-sig-msg');
  const ticker = (document.getElementById('cs-sig-ticker')?.value || '').trim().toUpperCase();
  const name   = (document.getElementById('cs-sig-name')?.value || '').trim() || ticker;
  const type   = document.getElementById('cs-sig-type')?.value;
  const krw    = +document.getElementById('cs-sig-krw')?.value || 0;

  if (!ticker) { if (msg) msg.innerHTML = '<span style="color:var(--red)">코인 티커를 입력하세요</span>'; return; }
  const finalTicker = ticker.startsWith('KRW-') ? ticker : `KRW-${ticker}`;

  if (type !== 'btc_dump_sell' && krw < 5000) {
    if (msg) msg.innerHTML = '<span style="color:var(--red)">매수금액을 5,000원 이상 입력하세요</span>';
    return;
  }

  if (msg) msg.innerHTML = '<span style="color:var(--muted)">저장 중...</span>';

  const body = {
    name, ticker: finalTicker, signal_type: type,
    params:      _ctSignalGetParams(),
    krw_amount:  krw,
    take_pct:    +document.getElementById('cs-sig-take')?.value   || 2,
    rebuy_drop:  +document.getElementById('cs-sig-rebuy')?.value  || 2,
    repeat_take: +document.getElementById('cs-sig-take')?.value   || 2,
    cooldown_min:    +document.getElementById('cs-sig-cooldown')?.value      || 60,
    max_triggers:    +document.getElementById('cs-sig-max-triggers')?.value  || 0,
    max_cycles:      +document.getElementById('cs-sig-max-cycles')?.value    || 0,
  };

  try {
    const r = await fetch('/api/coin-signal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (r.ok && !d.error) {
      if (msg) msg.innerHTML = '<span style="color:var(--green)">등록 완료</span>';
      await ctLoadAll();
      document.getElementById('ct-signal-form').style.display = 'none';
    } else {
      if (msg) msg.innerHTML = `<span style="color:var(--red)">${d.error || '저장 실패'}</span>`;
    }
  } catch (e) {
    if (msg) msg.innerHTML = `<span style="color:var(--red)">오류: ${e.message}</span>`;
  }
}

async function ctToggleSignalStatus(id, currentStatus) {
  const newStatus = currentStatus === 'watching' ? 'paused' : 'watching';
  try {
    const r = await fetch(`/api/coin-signal?id=${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    });
    if (r.ok) await ctLoadAll();
  } catch (e) { alert('오류: ' + e.message); }
}

async function ctDeleteSignalJob(id, name) {
  if (!confirm(`"${name}" 시그널을 삭제하시겠습니까?`)) return;
  try {
    const r = await fetch(`/api/coin-signal?id=${encodeURIComponent(id)}`, { method: 'DELETE' });
    const d = await r.json();
    if (d.ok) await ctLoadAll();
    else alert('삭제 실패: ' + (d.error || ''));
  } catch (e) { alert('오류: ' + e.message); }
}

// ── 시그널 잡 렌더링 ──────────────────────────────────────
function ctRenderSignalJobs() {
  const el = document.getElementById('ct-signal-list');
  if (!el) return;

  const jobs = Array.isArray(_ctSignalJobs) ? _ctSignalJobs : [];
  if (!jobs.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">없음</div>';
    return;
  }

  el.innerHTML = jobs.map(j => {
    const isWatching = j.status === 'watching';
    const isStopped  = j.status === 'stopped';
    const statusColor = isWatching ? 'var(--green)' : isStopped ? 'var(--red)' : 'var(--muted)';
    const statusLabel = isWatching ? '감시중' : isStopped ? '완료' : '일시정지';
    const typeLabel   = SIGNAL_TYPE_LABELS[j.signal_type] || j.signal_type;

    let paramsText = '';
    const p = j.params || {};
    if (j.signal_type === 'btc_pump_lag')
      paramsText = `BTC +${p.btc_rise_pct||2}% 이상 / 코인 +${p.coin_max_pct||1}% 미만`;
    else if (j.signal_type === 'dip_buy')
      paramsText = `고점 대비 -${p.dip_pct||5}% 이상 하락`;
    else if (j.signal_type === 'price_breakout')
      paramsText = `돌파가: ${Number(p.breakout_price||0).toLocaleString()}원`;
    else if (j.signal_type === 'btc_dump_sell')
      paramsText = `BTC -${p.btc_drop_pct||3}% 이하`;

    const lastTriggered = j.last_triggered
      ? new Date(j.last_triggered).toLocaleString('ko-KR', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' })
      : '—';

    return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <div>
          <span style="font-weight:700">${j.name}</span>
          <span style="color:var(--muted);font-size:11px;margin-left:6px">${j.ticker}</span>
          <span style="margin-left:8px;font-size:11px;background:var(--bg);padding:2px 7px;border-radius:10px;border:1px solid var(--border)">${typeLabel}</span>
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <span style="color:${statusColor};font-size:12px;font-weight:600">${statusLabel}</span>
          ${!isStopped ? `<button onclick="ctToggleSignalStatus('${j.id}','${j.status}')"
            style="padding:2px 9px;border:1px solid var(--border);border-radius:5px;background:none;font-size:11px;color:var(--muted);cursor:pointer">${isWatching ? '일시정지' : '재개'}</button>` : ''}
          <button onclick="ctDeleteSignalJob('${j.id}','${j.name}')"
            style="padding:2px 9px;border:1px solid var(--red);border-radius:5px;background:none;font-size:11px;color:var(--red);cursor:pointer">삭제</button>
        </div>
      </div>
      <div style="font-size:12px;color:var(--muted)">
        ${paramsText}
        ${j.signal_type !== 'btc_dump_sell' ? ` | 매수: ${Number(j.krw_amount||0).toLocaleString()}원 | 수익: ${j.take_pct}% / 재매수: -${j.rebuy_drop}%` : ''}
        | 쿨다운: ${j.cooldown_min}분
      </div>
      <div style="font-size:11px;color:var(--muted);margin-top:4px">
        트리거: ${j.trigger_count||0}${j.max_triggers > 0 ? '/'+j.max_triggers : ''}회
        | 마지막: ${lastTriggered}
      </div>
    </div>`;
  }).join('');
}

// ══════════════════════════════════════════════════════════
// 브리핑 관심종목 관리
// ══════════════════════════════════════════════════════════
let _wlItems = [];
let _wlAcTimer = null;

