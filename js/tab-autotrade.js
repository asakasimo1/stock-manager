async function initAutoTrade() {
  _restoreTradeSection('ac', true);
  _restoreTradeSection('ag', false);
  atLoadConfig();
  await atLoadAll();

  // 잡 목록 30초마다 재로드
  clearInterval(_atRefreshTimer);
  _atRefreshTimer = setInterval(() => {
    if (document.querySelector('.tab-btn.active')?.getAttribute('onclick')?.includes('autotrade')) {
      atLoadAll();
    } else {
      clearInterval(_atRefreshTimer);
      clearInterval(_atPriceTimer);
    }
  }, 30000);

  // 현재가·수익률 30초마다 갱신
  clearInterval(_atPriceTimer);
  atRefreshPrices();
  acRefreshPrices();
  _atPriceTimer = setInterval(() => {
    if (document.querySelector('.tab-btn.active')?.getAttribute('onclick')?.includes('autotrade')) {
      atRefreshPrices();
      acRefreshPrices();
    }
  }, 30000);

  // 잔고 자동 갱신 타이머
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

  atCheckDaemonStatus();
}

async function atRefreshPrices() {
  const active = _atJobs.filter(j => j.status === 'active');
  for (const job of active) {
    try {
      const r = await fetch(`/api/stock?ticker=${job.ticker}`);
      const d = await r.json();
      if (!d.price) continue;

      const cur      = d.price;
      const buyTotal = job.buy_price * job.qty * (1 + AT_BUY_FEE);
      const sellNet  = cur * job.qty * (1 - AT_SELL_FEE);
      const netPnl   = sellNet - buyTotal;
      const netPct   = netPnl / buyTotal * 100;
      const toTarget = (job.target_price || 0) - cur;
      const pnlColor = netPnl >= 0 ? 'var(--green)' : 'var(--red)';

      const priceEl  = document.getElementById(`at-price-${job.ticker}`);
      const pnlEl    = document.getElementById(`at-pnl-${job.ticker}`);
      const ttEl     = document.getElementById(`at-tt-${job.ticker}`);
      const barEl    = document.getElementById(`at-bar-${job.ticker}`);

      if (priceEl) priceEl.textContent = `${cur.toLocaleString()}원  (${d.chgPct >= 0 ? '+' : ''}${d.chgPct}%)`;
      if (pnlEl)   pnlEl.innerHTML = `<span style="color:${pnlColor};font-weight:700">${netPnl >= 0 ? '+' : ''}${Math.round(netPnl).toLocaleString()}원 (${netPct >= 0 ? '+' : ''}${netPct.toFixed(2)}%)</span>`;
      if (ttEl) {
        ttEl.textContent = toTarget > 0
          ? `목표까지 +${toTarget.toLocaleString()}원`
          : `🎯 목표 초과! +${Math.abs(toTarget).toLocaleString()}원`;
        ttEl.style.color = toTarget <= 0 ? 'var(--green)' : 'var(--muted)';
      }
      // 진행률 바: 매수가 → 목표가 기준
      if (barEl && job.target_price && job.buy_price) {
        const range    = job.target_price - job.buy_price;
        const progress = range > 0 ? Math.min(100, Math.max(0, (cur - job.buy_price) / range * 100)) : 0;
        barEl.style.width = `${progress}%`;
        barEl.style.background = toTarget <= 0 ? 'var(--green)' : 'var(--primary)';
      }
    } catch (_) {}
  }
}

async function atLoadAll() {
  try {
    // /api/data는 전역 캐시 재사용 (dashboard/portfolio 탭이 이미 로드했으면 네트워크 불필요)
    const [rSell, rBuy, rCycle, rGrid, gistData] = await Promise.all([
      fetch('/api/profit-sell'),
      fetch('/api/profit-buy'),
      fetch('/api/profit-cycle'),
      fetch('/api/stock-grid'),
      _fetchGistData(),
    ]);
    _atJobs    = rSell.ok  ? await rSell.json()  : [];
    _abJobs    = rBuy.ok   ? await rBuy.json()   : [];
    _acJobs    = rCycle.ok ? await rCycle.json() : [];
    _agJobs    = rGrid.ok  ? await rGrid.json()  : [];
    _atAccount = gistData.account_balance || null;
  } catch (e) {
    console.warn('자동매매 데이터 로드 실패:', e);
  }
  try { atRenderAccount(); } catch (e) { console.warn('atRenderAccount 오류:', e); }
  try { abRenderJobs();    } catch (e) { console.warn('abRenderJobs 오류:', e); }
  try { acRenderJobs();    } catch (e) { console.warn('acRenderJobs 오류:', e); }
  try { atRenderJobs();    } catch (e) { console.warn('atRenderJobs 오류:', e); }
  try { agRenderJobs();    } catch (e) { console.warn('agRenderJobs 오류:', e); }
  atLoadToday();
}

// ── 잔고 새로고침 (GitHub Actions balance job 트리거 → 30초 후 Gist 재조회) ──
async function atRefreshBalance() {
  const btn = document.getElementById('at-balance-refresh-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⟳ 요청중...'; }
  try {
    const r = await fetch('/api/data?mode=trigger_balance');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (data.error) throw new Error(data.error);

    // 30초 카운트다운 후 Gist 재조회
    let sec = 30;
    const tick = setInterval(() => {
      if (btn) btn.textContent = `⟳ 조회중... (${--sec}초)`;
      if (sec <= 0) {
        clearInterval(tick);
        fetch('/api/data').then(res => res.json()).then(d => {
          _atAccount = d.account_balance || null;
          atRenderAccount();
          if (btn) { btn.disabled = false; btn.textContent = '⟳ 잔고 새로고침'; }
        }).catch(() => {
          if (btn) { btn.disabled = false; btn.textContent = '⟳ 잔고 새로고침'; }
        });
      }
    }, 1000);
  } catch (e) {
    alert('잔고 조회 실패: ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = '⟳ 잔고 새로고침'; }
  }
}

// ── 계좌 현황 ────────────────────────────────────────────
function atRenderAccount() {
  const el = document.getElementById('at-account');
  if (!el) return;
  if (!_atAccount) {
    el.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:8px 0">계좌 정보 없음 (15:35 일간 리포트 후 업데이트)</div>';
    // 보유종목 없으면 폼 자동입력도 초기화
    return;
  }
  const a = _atAccount;
  const BUY_FEE  = 0.00015;   // 매수 수수료 0.015%
  const SELL_FEE = 0.00195;   // 매도 수수료 0.015% + 증권거래세 0.18%
  const pnlColor   = a.day_pnl >= 0 ? 'var(--green)' : 'var(--red)';

  // 평가손익 합계 (수수료제외)
  const totalNetAmt = Math.round((a.holdings || []).reduce((sum, h) => {
    return sum + (h.eval_price * (1 - SELL_FEE) * h.qty) - (h.avg_price * (1 + BUY_FEE) * h.qty);
  }, 0));
  const totalNetColor = totalNetAmt >= 0 ? 'var(--green)' : 'var(--red)';

  const holdings = (a.holdings || []).map(h => {
    // 평가손익(수수료제외): 실제 매도 시 수수료+거래세 차감 후 손익금액
    const costBasis = h.avg_price * (1 + BUY_FEE) * h.qty;
    const netProc   = h.eval_price * (1 - SELL_FEE) * h.qty;
    const netAmt    = Math.round(netProc - costBasis);
    const netPct    = (netProc - costBasis) / costBasis * 100;
    const netColor  = netAmt >= 0 ? 'var(--green)' : 'var(--red)';
    return `
    <tr style="border-bottom:1px solid var(--border)">
      <td style="padding:5px 6px;white-space:nowrap">
        <div style="cursor:pointer;color:var(--primary);font-size:11px" onclick="atFillFromHolding('${h.ticker}','${h.name}',${h.qty},${h.avg_price})">${h.ticker}</div>
        <div style="color:var(--text);font-size:11px">${h.name}</div>
      </td>
      <td style="padding:5px 6px;text-align:right;white-space:nowrap;color:var(--text);font-size:11px">${h.qty}주</td>
      <td style="padding:5px 6px;text-align:right;white-space:nowrap;font-size:11px">
        <div style="color:var(--muted)">${h.avg_price.toLocaleString()}</div>
        <div style="color:var(--text)">${h.eval_price.toLocaleString()}</div>
      </td>
      <td style="padding:5px 6px;text-align:right;white-space:nowrap">
        <div style="color:${netColor};font-weight:600;font-size:12px">${netAmt>=0?'+':''}${netAmt.toLocaleString()}원</div>
        <div style="color:${netColor};font-size:10px">${netPct>=0?'+':''}${netPct.toFixed(2)}%</div>
      </td>
    </tr>`;
  }).join('');

  el.innerHTML = `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px 20px">
      <div style="font-size:13px;font-weight:700;color:var(--text);margin-bottom:10px">🏦 한국투자증권 계좌 <span style="font-size:11px;color:var(--muted);font-weight:400">기준: ${a.updated_at}</span></div>
      <div style="display:flex;gap:0;flex-wrap:nowrap;margin-bottom:12px;border:1px solid var(--border);border-radius:10px;overflow:hidden">
        <div style="flex:1;padding:6px 4px;text-align:center;border-right:1px solid var(--border);min-width:0">
          <div style="font-size:9px;color:var(--muted);margin-bottom:2px;white-space:nowrap">총평가금액</div>
          <div style="font-size:11px;font-weight:700;color:var(--text);white-space:nowrap">${a.total_eval.toLocaleString()}원</div>
        </div>
        <div style="flex:1;padding:6px 4px;text-align:center;border-right:1px solid var(--border);min-width:0">
          <div style="font-size:9px;color:var(--muted);margin-bottom:2px;white-space:nowrap">예수금</div>
          <div style="font-size:11px;font-weight:700;color:var(--text);white-space:nowrap">${a.cash.toLocaleString()}원</div>
        </div>
        <div style="flex:1;padding:6px 4px;text-align:center;border-right:1px solid var(--border);min-width:0">
          <div style="font-size:9px;color:var(--muted);margin-bottom:2px;white-space:nowrap">당일손익</div>
          <div style="font-size:11px;font-weight:700;color:${pnlColor};white-space:nowrap">${a.day_pnl>=0?'+':''}${a.day_pnl.toLocaleString()}원</div>
          <div style="font-size:9px;color:${pnlColor};font-weight:600">${a.day_ret>=0?'+':''}${(a.day_ret||0).toFixed(2)}%</div>
        </div>
        <div style="flex:1;padding:6px 4px;text-align:center;min-width:0">
          <div style="font-size:9px;color:var(--muted);margin-bottom:2px;white-space:nowrap">평가손익</div>
          <div style="font-size:11px;font-weight:700;color:${totalNetColor};white-space:nowrap">${totalNetAmt>=0?'+':''}${totalNetAmt.toLocaleString()}원</div>
          <div style="font-size:9px;color:var(--muted)">수수료제외</div>
        </div>
      </div>
      ${holdings ? `<table style="width:100%;border-collapse:collapse">
        <thead><tr style="color:var(--muted);border-bottom:1px solid var(--border);font-size:11px">
          <th style="padding:4px 6px;text-align:left;font-weight:500">코드 / 종목명</th>
          <th style="padding:4px 6px;text-align:right;font-weight:500">수량</th>
          <th style="padding:4px 6px;text-align:right;font-weight:500">평균→현재(원)</th>
          <th style="padding:4px 6px;text-align:right;font-weight:500">평가손익<span style="font-size:9px;font-weight:400;margin-left:2px">(수수료제외)</span></th>
        </tr></thead>
        <tbody>${holdings}</tbody>
      </table>
      <div style="font-size:10px;color:var(--muted);margin-top:6px">수수료(0.015%)+거래세(0.18%) 차감 기준 · 코드 클릭 시 폼 자동입력</div>` : '<div style="color:var(--muted);font-size:12px">보유 종목 없음</div>'}
    </div>`;

  // 수익매도 폼 위에 보유종목 칩 렌더링
  atRenderHoldingChips();
}

function atRenderHoldingChips() {
  const wrap  = document.getElementById('at-holding-chips');
  const inner = document.getElementById('at-holding-chips-inner');
  if (!wrap || !inner) return;
  const holdings = _atAccount?.holdings || [];
  if (!holdings.length) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';
  inner.innerHTML = holdings.map(h => {
    const safeN = h.name.replace(/'/g, "\\'");
    const pnlColor = h.pnl_pct >= 0 ? 'var(--green)' : 'var(--red)';
    return `<button onmousedown="atFillFromHolding('${h.ticker}','${safeN}',${h.qty},${h.avg_price})"
      style="padding:4px 10px;border-radius:20px;border:1px solid var(--border);background:var(--bg);
        color:var(--text);font-size:12px;cursor:pointer;display:flex;align-items:center;gap:5px">
      ${h.name}
      <span style="color:${pnlColor};font-size:10px;font-weight:700">${h.pnl_pct>=0?'+':''}${h.pnl_pct.toFixed(1)}%</span>
    </button>`;
  }).join('');
}

// ── 종목명 자동완성 ──────────────────────────────────────
let _atAcTimer = null;

function onAtNameFocus() {
  const holdings = _atAccount?.holdings || [];
  if (holdings.length) {
    showAtAcLocal(holdings);
  } else {
    const box = document.getElementById('at-ac-list');
    if (box) {
      box.innerHTML = `<div style="padding:10px 14px;font-size:12px;color:var(--muted)">종목명을 입력하면 검색됩니다</div>`;
      box.style.display = 'block';
    }
  }
}

function onAtNameInput(val) {
  clearTimeout(_atAcTimer);
  const holdings = _atAccount?.holdings || [];
  if (!val.trim()) {
    // 입력 지우면 보유종목 전체 다시 표시
    if (holdings.length) showAtAcLocal(holdings);
    else hideAtAc();
    return;
  }
  // 보유종목 먼저 로컬 필터
  const matched = holdings.filter(h =>
    h.name.includes(val.trim()) || h.ticker.startsWith(val.trim())
  );
  if (matched.length) { showAtAcLocal(matched); return; }
  _atAcTimer = setTimeout(() => fetchAtAc(val.trim()), 220);
}

async function showAtAcLocal(holdings) {
  const list = document.getElementById('at-ac-list');
  if (!list) return;
  if (!holdings.length) { hideAtAc(); return; }
  list.innerHTML = holdings.map(h => {
    const safeN = h.name.replace(/'/g, "\\'");
    const pnlColor = h.pnl_pct >= 0 ? 'var(--green)' : 'var(--red)';
    return `<div onmousedown="selectAtAcItem('${safeN}','${h.ticker}',${h.qty},${h.avg_price})"
      style="padding:9px 12px;font-size:13px;cursor:pointer;display:flex;justify-content:space-between;
             align-items:center;border-bottom:1px solid var(--border)"
      onmouseover="this.style.background='var(--secondary)'" onmouseout="this.style.background=''">
      <span>${h.name} <span style="font-size:11px;color:var(--green)">★보유</span></span>
      <span style="text-align:right;font-size:11px">
        <span id="at-lp-${h.ticker}" style="color:var(--text);font-weight:600">조회중</span>
        <span style="color:${pnlColor};margin-left:4px">${h.pnl_pct >= 0 ? '+' : ''}${h.pnl_pct.toFixed(1)}%</span>
      </span>
    </div>`;
  }).join('');
  list.style.display = 'block';

  for (const h of holdings) {
    try {
      const r = await fetch(`/api/stock?ticker=${h.ticker}`);
      const d = await r.json();
      const el = document.getElementById(`at-lp-${h.ticker}`);
      if (el && d.price) el.textContent = `${d.price.toLocaleString()}원`;
    } catch { /* ignore */ }
  }
}

async function fetchAtAc(q) {
  try {
    const r = await fetch(`/api/stock?q=${encodeURIComponent(q)}`);
    const d = await r.json();
    showAtAc(d.items || []);
  } catch { hideAtAc(); }
}

function showAtAc(items) {
  const list = document.getElementById('at-ac-list');
  if (!list || !items.length) { hideAtAc(); return; }
  list.innerHTML = items.map(it => {
    const safeN = it.name.replace(/'/g, "\\'");
    const mkt = it.market ? `<span style="font-size:10px;color:var(--muted);margin-left:4px">${it.market}</span>` : '';
    return `<div onmousedown="selectAtAcItem('${safeN}','${it.ticker}')"
      style="padding:9px 12px;font-size:13px;cursor:pointer;display:flex;justify-content:space-between;
             align-items:center;border-bottom:1px solid var(--border)"
      onmouseover="this.style.background='var(--secondary)'" onmouseout="this.style.background=''">
      <span>${it.name}${mkt}</span>
      <span style="font-size:11px;color:var(--primary);font-weight:600">${it.ticker}</span>
    </div>`;
  }).join('');
  list.style.display = 'block';
}

function hideAtAc() {
  setTimeout(() => {
    const list = document.getElementById('at-ac-list');
    if (list) list.style.display = 'none';
  }, 150);
}

async function atShowCurrentPrice(ticker) {
  const wrap = document.getElementById('at-cur-price-wrap');
  const el   = document.getElementById('at-cur-price');
  const pct  = document.getElementById('at-cur-pct');
  if (!wrap || !el) return;
  wrap.style.display = 'inline';
  el.textContent = '조회 중...';
  if (pct) pct.textContent = '';
  try {
    const r = await fetch(`/api/stock?ticker=${ticker}`);
    const d = await r.json();
    if (d.price) {
      el.textContent = `${d.price.toLocaleString()}원`;
      if (pct && d.chgPct !== undefined) {
        const c = parseFloat(d.chgPct) || 0;
        pct.textContent = `${c >= 0 ? '+' : ''}${c.toFixed(2)}%`;
        pct.style.color = c >= 0 ? 'var(--green)' : 'var(--red)';
      }
    } else {
      el.textContent = '—';
    }
  } catch { el.textContent = '—'; }
}

function selectAtAcItem(name, ticker, qty, buyPrice) {
  document.getElementById('at-name').value = name;
  document.getElementById('at-ticker').value = ticker;
  document.getElementById('at-ticker-display').textContent = ticker;
  hideAtAc();
  if (qty !== undefined)      document.getElementById('at-qty').value      = qty;
  if (buyPrice !== undefined) document.getElementById('at-buyprice').value = buyPrice;
  atShowCurrentPrice(ticker);
  atUpdateHint();
}

// ── 계좌 보유종목에서 폼 자동입력 ───────────────────────
function atFillFromHolding(ticker, name, qty, buyPrice) {
  document.getElementById('at-name').value          = name;
  document.getElementById('at-ticker').value        = ticker;
  document.getElementById('at-ticker-display').textContent = ticker;
  document.getElementById('at-qty').value           = qty;
  document.getElementById('at-buyprice').value      = buyPrice;
  atShowCurrentPrice(ticker);
  atUpdateHint();
}

function atTypeChange() {
  const type      = document.querySelector('input[name="at-type"]:checked')?.value || 'amount';
  const buyRow    = document.getElementById('at-buyprice')?.closest('div')?.parentElement;
  const targetEl  = document.getElementById('at-target');
  const bpEl      = document.getElementById('at-buyprice');
  const bpWrap    = bpEl?.closest('div[style*="grid"]') || bpEl?.parentElement;

  // 지정가 선택 시 매수단가 불필요 → 흐리게 표시, placeholder 변경
  if (type === 'price') {
    if (targetEl) targetEl.placeholder = '예: 85000';
    if (bpEl) { bpEl.style.opacity = '0.4'; bpEl.readOnly = true; }
  } else {
    if (targetEl) targetEl.placeholder = type === 'amount' ? '예: 50' : '예: 3';
    if (bpEl) { bpEl.style.opacity = '1'; bpEl.readOnly = false; }
  }
  atUpdateHint();
}

function atUpdateHint() {
  const type   = document.querySelector('input[name="at-type"]:checked')?.value || 'amount';
  const target = parseFloat(document.getElementById('at-target').value) || 0;
  const bp     = parseInt(document.getElementById('at-buyprice').value) || 0;
  const qty    = parseInt(document.getElementById('at-qty').value) || 1;
  const hint   = document.getElementById('at-target-hint');
  if (!hint) return;

  if (type === 'price') {
    hint.textContent = target
      ? `현재가 ≥ ${target.toLocaleString()}원 도달 시 즉시 매도`
      : '매도를 원하는 지정 가격 (원) 입력';
  } else if (type === 'amount') {
    if (bp && target) {
      const tp = Math.ceil((bp * (1 + AT_BUY_FEE) * qty + target) / qty / (1 - AT_SELL_FEE));
      hint.textContent = `수수료 차감 후 순이익 ${target.toLocaleString()}원 → 목표 매도단가 ${tp.toLocaleString()}원`;
    } else {
      hint.textContent = '수수료 차감 후 순이익 금액 (원) 기준';
    }
  } else {
    if (bp && target) {
      const tp = Math.ceil(bp * (1 + target / 100) / (1 - AT_SELL_FEE));
      hint.textContent = `수익률 ${target}% → 목표 매도단가 ${tp.toLocaleString()}원`;
    } else {
      hint.textContent = '매수단가 대비 수익률 (%) 기준';
    }
  }
}

// ── 잡 등록 ──────────────────────────────────────────────
async function atRegister() {
  const ticker   = document.getElementById('at-ticker').value.trim().toUpperCase();
  const name     = document.getElementById('at-name').value.trim() || ticker;
  const qty      = parseInt(document.getElementById('at-qty').value);
  const buyPrice = parseInt(document.getElementById('at-buyprice').value);
  const type     = document.querySelector('input[name="at-type"]:checked')?.value || 'amount';
  const target   = parseFloat(document.getElementById('at-target').value);
  const msg      = document.getElementById('at-msg');

  const needBuyPrice = type !== 'price';
  if (!ticker || !qty || (needBuyPrice && !buyPrice) || isNaN(target) || target <= 0) {
    msg.style.color = 'var(--red)';
    msg.textContent = '모든 항목을 올바르게 입력하세요';
    return;
  }

  let targetPrice;
  if (type === 'price') {
    targetPrice = parseInt(target);
  } else if (type === 'amount') {
    targetPrice = Math.ceil((buyPrice * (1 + AT_BUY_FEE) * qty + target) / qty / (1 - AT_SELL_FEE));
  } else {
    targetPrice = Math.ceil(buyPrice * (1 + target / 100) / (1 - AT_SELL_FEE));
  }

  const job = { ticker, name, qty, buy_price: buyPrice || 0, target_type: type, target_value: target, target_price: targetPrice };

  msg.style.color = 'var(--muted)';
  msg.textContent = '등록 중...';

  try {
    const r = await fetch('/api/profit-sell', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(job),
    });
    if (r.ok) {
      msg.style.color = 'var(--green)';
      msg.textContent = `✅ ${name}(${ticker}) 잡 등록 완료 — 목표단가 ${targetPrice.toLocaleString()}원`;
      await atLoadAll();
    } else {
      msg.style.color = 'var(--red)';
      msg.textContent = '등록 실패 — 다시 시도하세요';
    }
  } catch (e) {
    msg.style.color = 'var(--red)';
    msg.textContent = `오류: ${e.message}`;
  }
}

// ── 잡 수정 ──────────────────────────────────────────────
function atEdit(ticker) {
  const j = _atJobs.find(j => j.ticker === ticker && j.status === 'active');
  if (!j) return;
  switchTab('autotrade');
  document.getElementById('at-name').value     = j.name || '';
  document.getElementById('at-ticker').value   = j.ticker;
  document.getElementById('at-ticker-display').textContent = j.ticker;
  document.getElementById('at-qty').value      = j.qty || '';
  document.getElementById('at-buyprice').value = j.buy_price || '';
  const type = j.target_type || 'amount';
  document.querySelectorAll('input[name="at-type"]').forEach(r => { r.checked = r.value === type; });
  document.getElementById('at-target').value   = j.target_value || '';
  atUpdateHint();
  document.getElementById('at-name').scrollIntoView({ behavior: 'smooth', block: 'center' });
  document.getElementById('at-name').focus();
}

// ── 잡 취소 ──────────────────────────────────────────────
async function atCancel(ticker) {
  if (!confirm(`${ticker} 잡을 취소하시겠습니까?`)) return;
  try {
    const r = await fetch(`/api/profit-sell?ticker=${ticker}`, { method: 'DELETE' });
    if (r.ok) await atLoadAll();
  } catch (e) {
    alert('취소 실패: ' + e.message);
  }
}

// ── 즉시 매도 ─────────────────────────────────────────────
async function atSellNow(ticker, name, qty) {
  if (!confirm(`${name}(${ticker}) ${qty}주를 지금 즉시 시장가 매도하시겠습니까?\n\n목표 미달성이어도 즉시 체결 요청됩니다.`)) return;
  const job = _atJobs.find(j => j.ticker === ticker && j.status === 'active');
  if (!job) { alert('활성 잡을 찾을 수 없습니다.'); return; }
  const btn = document.getElementById(`at-sell-btn-${ticker}`);
  if (btn) { btn.disabled = true; btn.textContent = '요청 중...'; }
  try {
    const r = await fetch('/api/profit-sell', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...job, force_sell: true }),
    });
    if (r.ok) {
      const d = await r.json();
      if (d.triggered === false) {
        if (btn) { btn.textContent = '✓ 등록됨'; btn.style.background = '#f59e0b'; btn.style.color = '#fff'; }
        const proceed = confirm(
          '⚠️ 즉시 트리거 실패 (GH_TOKEN에 workflow 권한 필요)\n\n' +
          'GitHub Actions에서 수동 실행이 필요합니다.\n' +
          '지금 GitHub Actions 페이지를 여시겠습니까?'
        );
        if (proceed) window.open('https://github.com/asakasimo1/stock-trader/actions', '_blank');
      } else {
        if (btn) { btn.textContent = '✓ 요청완료'; btn.style.background = '#16a34a'; btn.style.color = '#fff'; }
      }
      setTimeout(() => atLoadAll(), 2000);
    } else {
      if (btn) { btn.disabled = false; btn.textContent = '즉시 매도'; }
      alert('요청 실패');
    }
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '즉시 매도'; }
    alert('요청 실패: ' + e.message);
  }
}

// ── 잡 목록 렌더링 ───────────────────────────────────────
function atRenderJobs() {
  const active  = _atJobs.filter(j => j.status === 'active' || j.status === 'submitted');
  const history = _atJobs.filter(j => j.status !== 'active' && j.status !== 'submitted');

  const elActive = document.getElementById('at-active-list');
  if (elActive) {
    if (!active.length) {
      elActive.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:12px 0">등록된 활성 잡 없음</div>';
    } else {
      elActive.innerHTML = active.map(j => {
        const isSubmitted = j.status === 'submitted';
        const typeLabel = j.target_type === 'price'
          ? `지정가 ${Number(j.target_price).toLocaleString()}원`
          : j.target_type === 'amount'
          ? `순이익 +${Number(j.target_value).toLocaleString()}원`
          : `수익률 +${j.target_value}%`;
        const submittedBadge = isSubmitted
          ? `<span style="font-size:11px;background:#1a3a7a33;color:var(--primary);border-radius:5px;padding:2px 7px;margin-left:6px;font-weight:500">📋 MTS 주문 대기중</span>`
          : '';
        const orderNoHint = isSubmitted && j.order_no
          ? `<div style="font-size:11px;color:var(--muted);margin-top:2px">주문번호: ${j.order_no} · ${j.submitted_at||''}</div>`
          : '';
        return `
          <div style="background:var(--surface);border:1px solid ${isSubmitted ? 'var(--primary)44' : 'var(--border)'};border-radius:12px;padding:14px 16px;margin-bottom:8px">
            <!-- 헤더 행 -->
            <div style="display:flex;align-items:flex-start;gap:10px">
              <div style="flex:1">
                <div style="font-size:14px;font-weight:700;color:var(--text)">${j.name}
                  <span style="font-size:12px;color:var(--muted);font-weight:400">${j.ticker}</span>
                  ${submittedBadge}
                </div>
                <div style="font-size:12px;color:var(--muted);margin-top:3px">
                  ${j.qty}주 · 매수 ${Number(j.buy_price).toLocaleString()}원 · 목표 ${typeLabel}
                </div>
                ${orderNoHint}
              </div>
              <div style="display:flex;flex-direction:column;gap:5px;flex-shrink:0">
                ${isSubmitted ? '' : `<button id="at-sell-btn-${j.ticker}"
                  onclick="atSellNow('${j.ticker}','${j.name.replace(/'/g,"\\'")}',${j.qty})"
                  style="background:var(--red);border:none;color:#fff;
                         border-radius:8px;padding:5px 14px;font-size:12px;cursor:pointer;white-space:nowrap;font-weight:600">
                  즉시 매도
                </button>
                <button onclick="atEdit('${j.ticker}')"
                  style="background:none;border:1px solid var(--primary);color:var(--primary);
                         border-radius:8px;padding:5px 14px;font-size:12px;cursor:pointer;white-space:nowrap">
                  ✎ 수정
                </button>`}
                <button onclick="atCancel('${j.ticker}')"
                  style="background:none;border:1px solid var(--border);color:var(--muted);
                         border-radius:8px;padding:5px 14px;font-size:12px;cursor:pointer;white-space:nowrap">
                  ✕ 취소
                </button>
              </div>
            </div>

            <!-- 현재가·수익 행 -->
            <div style="display:flex;gap:16px;margin-top:10px;flex-wrap:wrap">
              <div>
                <div style="font-size:10px;color:var(--muted)">현재가</div>
                <div id="at-price-${j.ticker}" style="font-size:13px;font-weight:600;color:var(--text)">조회 중...</div>
              </div>
              <div>
                <div style="font-size:10px;color:var(--muted)">예상 순손익</div>
                <div id="at-pnl-${j.ticker}" style="font-size:13px">—</div>
              </div>
              <div>
                <div style="font-size:10px;color:var(--muted)">목표단가</div>
                <div style="font-size:13px;color:var(--primary);font-weight:600">${(j.target_price||0).toLocaleString()}원</div>
              </div>
              <div style="margin-left:auto;text-align:right">
                <div id="at-tt-${j.ticker}" style="font-size:11px;color:var(--muted)">—</div>
              </div>
            </div>
            <div style="font-size:10px;color:var(--muted);margin-top:4px">등록: ${j.created_at||''}</div>

            <!-- 진행률 바 -->
            <div style="margin-top:8px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">
              <div id="at-bar-${j.ticker}" style="height:100%;width:0%;background:var(--primary);transition:width .4s;border-radius:2px"></div>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--muted);margin-top:2px">
              <span>매수 ${Number(j.buy_price).toLocaleString()}원</span>
              <span>목표 ${(j.target_price||0).toLocaleString()}원</span>
            </div>
          </div>`;
      }).join('');
      // 렌더 직후 현재가 즉시 반영
      atRefreshPrices();
    }
  }

  const elHistory = document.getElementById('at-history-list');
  if (elHistory) {
    if (!history.length) {
      elHistory.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:12px 0">내역 없음</div>';
    } else {
      elHistory.innerHTML = history.slice(0, 20).map(j => {
        const isDone = j.status === 'done';
        const badge  = isDone
          ? '<span style="background:#1a7a3a22;color:var(--green);border-radius:5px;padding:2px 7px;font-size:11px">완료</span>'
          : '<span style="background:#7a1a1a22;color:var(--red);border-radius:5px;padding:2px 7px;font-size:11px">취소</span>';
        const detail = isDone
          ? `매도 ${(j.sell_price||0).toLocaleString()}원 · ${j.executed_at || ''}`
          : `취소일 ${j.cancelled_at || ''}`;
        return `
          <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 14px;margin-bottom:6px;display:flex;align-items:center;gap:10px">
            <div style="flex:1">
              <div style="font-size:13px;font-weight:600;color:var(--text)">${j.name} <span style="font-size:11px;color:var(--muted)">${j.ticker}</span> ${badge}</div>
              <div style="font-size:11px;color:var(--muted);margin-top:2px">${detail}</div>
            </div>
          </div>`;
      }).join('');
    }
  }
}


// ══════════════════════════════════════════════════════════
// 자동매매 — 매수 잡 (ab = auto buy)
// ══════════════════════════════════════════════════════════
let _atJobs = [], _abJobs = [], _acJobs = [];

// ── 종목명 자동완성 ──────────────────────────────────────
let _abAcTimer = null;

function onAbNameInput(val) {
  clearTimeout(_abAcTimer);
  if (!val.trim()) { hideAbAc(); return; }
  _abAcTimer = setTimeout(() => fetchAbAc(val.trim()), 220);
}

async function fetchAbAc(q) {
  try {
    const r = await fetch(`/api/stock?q=${encodeURIComponent(q)}`);
    const d = await r.json();
    showAbAc(d.items || []);
  } catch { hideAbAc(); }
}

function showAbAc(items) {
  const list = document.getElementById('ab-ac-list');
  if (!list || !items.length) { hideAbAc(); return; }
  list.innerHTML = items.map(it => {
    const safeN = it.name.replace(/'/g, "\\'");
    const mkt = it.market ? `<span style="font-size:10px;color:var(--muted);margin-left:4px">${it.market}</span>` : '';
    return `<div onmousedown="selectAbAcItem('${safeN}','${it.ticker}')"
      style="padding:9px 12px;font-size:13px;cursor:pointer;display:flex;justify-content:space-between;
             align-items:center;border-bottom:1px solid var(--border)"
      onmouseover="this.style.background='var(--secondary)'" onmouseout="this.style.background=''">
      <span>${it.name}${mkt}</span>
      <span style="font-size:11px;color:var(--primary);font-weight:600">${it.ticker}</span>
    </div>`;
  }).join('');
  list.style.display = 'block';
}

function hideAbAc() {
  setTimeout(() => {
    const list = document.getElementById('ab-ac-list');
    if (list) list.style.display = 'none';
  }, 150);
}

function selectAbAcItem(name, ticker) {
  document.getElementById('ab-name').value = name;
  document.getElementById('ab-ticker').value = ticker;
  document.getElementById('ab-ticker-display').textContent = ticker;
  hideAbAc();
  abUpdateHint();
}

// ── 폼 UI 인터랙션 ───────────────────────────────────────
function abCondChange() {
  const cond = document.querySelector('input[name="ab-cond"]:checked')?.value;
  const row  = document.getElementById('ab-limit-row');
  if (row) row.style.display = cond === 'limit' ? 'block' : 'none';
  abUpdateHint();
}

function abQtyTypeChange() {
  const type  = document.querySelector('input[name="ab-qty-type"]:checked')?.value;
  const hint  = document.getElementById('ab-qty-hint');
  const input = document.getElementById('ab-qty-val');
  if (type === 'amount') {
    if (hint)  hint.textContent  = '매수 금액 (원) — 현재가 기준 수량 자동 계산';
    if (input) input.placeholder = '예: 100000';
  } else {
    if (hint)  hint.textContent  = '매수할 수량 (주)';
    if (input) input.placeholder = '예: 2';
  }
  abUpdateHint();
}

function abUpdateHint() {
  const cond    = document.querySelector('input[name="ab-cond"]:checked')?.value || 'limit';
  const tp      = parseInt(document.getElementById('ab-target-price')?.value) || 0;
  const qtyType = document.querySelector('input[name="ab-qty-type"]:checked')?.value || 'qty';
  const qtyVal  = parseInt(document.getElementById('ab-qty-val')?.value) || 0;
  const hint    = document.getElementById('ab-hint');
  if (!hint) return;

  if (cond === 'market') {
    hint.textContent = '다음 GitHub Actions 실행(최대 5분) 시 즉시 시장가 매수';
    hint.style.color = 'var(--primary)';
  } else if (tp && qtyVal) {
    const label = qtyType === 'amount'
      ? `${tp.toLocaleString()}원 이하 시 ${tp.toLocaleString()}원어치 매수`
      : `${tp.toLocaleString()}원 이하 시 ${qtyVal}주 매수`;
    hint.textContent = label;
    hint.style.color = 'var(--muted)';
  } else {
    hint.textContent = '';
  }
}

// ── 잡 등록 ──────────────────────────────────────────────
async function abRegister() {
  const ticker    = document.getElementById('ab-ticker').value.trim();
  const name      = document.getElementById('ab-name').value.trim() || ticker;
  const cond      = document.querySelector('input[name="ab-cond"]:checked')?.value || 'limit';
  const targetPrc = parseInt(document.getElementById('ab-target-price')?.value) || 0;
  const qtyType   = document.querySelector('input[name="ab-qty-type"]:checked')?.value || 'qty';
  const qtyVal    = parseInt(document.getElementById('ab-qty-val')?.value) || 0;
  const msg       = document.getElementById('ab-msg');

  if (!ticker) { msg.style.color='var(--red)'; msg.textContent='종목을 선택하세요'; return; }
  if (cond === 'limit' && !targetPrc) { msg.style.color='var(--red)'; msg.textContent='목표 매수가를 입력하세요'; return; }
  if (!qtyVal) { msg.style.color='var(--red)'; msg.textContent='수량 또는 금액을 입력하세요'; return; }

  const job = {
    ticker,
    name,
    condition_type: cond,
    target_price: cond === 'limit' ? targetPrc : 0,
    qty:    qtyType === 'qty'    ? qtyVal : 0,
    amount: qtyType === 'amount' ? qtyVal : 0,
  };

  msg.style.color = 'var(--muted)';
  msg.textContent = '등록 중...';

  try {
    const r = await fetch('/api/profit-buy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(job),
    });
    if (r.ok) {
      const d = await r.json();
      const condLabel = cond === 'market' ? '즉시 시장가' : `${targetPrc.toLocaleString()}원 이하 시`;
      msg.style.color = 'var(--green)';
      if (cond === 'market' && d.triggered === false) {
        msg.innerHTML = `✅ 등록 완료 — <span style="color:var(--red)">즉시 트리거 실패 (GH_TOKEN workflow 권한 확인)</span>
          <a href="https://github.com/asakasimo1/stock-trader/actions" target="_blank"
             style="color:var(--primary);margin-left:6px;font-size:11px">수동 실행 →</a>`;
      } else {
        msg.textContent = `✅ ${name}(${ticker}) 매수 잡 등록 — ${condLabel}${cond==='market'?' (즉시 실행 요청됨)':''}`;
      }
      await atLoadAll();
    } else {
      msg.style.color = 'var(--red)';
      msg.textContent = '등록 실패';
    }
  } catch (e) {
    msg.style.color = 'var(--red)';
    msg.textContent = `오류: ${e.message}`;
  }
}

// ── 잡 취소 ──────────────────────────────────────────────
async function abCancel(ticker) {
  if (!confirm(`${ticker} 매수 잡을 취소하시겠습니까?`)) return;
  try {
    const r = await fetch(`/api/profit-buy?ticker=${ticker}`, { method: 'DELETE' });
    if (r.ok) await atLoadAll();
  } catch (e) { alert('취소 실패: ' + e.message); }
}

// ── 잡 수정 (폼에 기존 값 채우기) ────────────────────────
function abEdit(ticker) {
  const j = _abJobs.find(j => j.ticker === ticker && j.status === 'active');
  if (!j) return;
  switchTab('autotrade');
  document.getElementById('ab-name').value  = j.name || '';
  document.getElementById('ab-ticker').value = j.ticker;
  document.getElementById('ab-ticker-display').textContent = j.ticker;
  // 매수 조건
  const cond = j.condition_type || 'limit';
  document.querySelectorAll('input[name="ab-cond"]').forEach(r => { r.checked = r.value === cond; });
  document.getElementById('ab-limit-row').style.display = cond === 'limit' ? 'block' : 'none';
  if (cond === 'limit') document.getElementById('ab-target-price').value = j.target_price || '';
  // 수량/금액
  const qtyType = j.amount > 0 ? 'amount' : 'qty';
  document.querySelectorAll('input[name="ab-qty-type"]').forEach(r => { r.checked = r.value === qtyType; });
  document.getElementById('ab-qty-val').value = qtyType === 'amount' ? j.amount : j.qty;
  abQtyTypeChange(); abUpdateHint();
  document.getElementById('ab-name').scrollIntoView({ behavior: 'smooth', block: 'center' });
  document.getElementById('ab-name').focus();
}

// ── 매수 잡 목록 렌더링 + 현재가 ────────────────────────
function abRenderJobs() {
  const active  = _abJobs.filter(j => j.status === 'active');
  const elActive = document.getElementById('ab-active-list');
  if (!elActive) return;

  if (!active.length) {
    elActive.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">등록된 매수 잡 없음</div>';
    return;
  }

  elActive.innerHTML = active.map(j => {
    const condLabel = j.condition_type === 'market'
      ? '<span style="color:var(--primary)">즉시 시장가</span>'
      : `현재가 ≤ <span style="color:var(--primary);font-weight:700">${Number(j.target_price).toLocaleString()}원</span>`;
    const qtyLabel = j.qty > 0
      ? `${j.qty}주`
      : `${Number(j.amount).toLocaleString()}원어치`;

    return `
      <div style="background:var(--surface);border:1px solid #2563eb33;border-radius:12px;padding:14px 16px;margin-bottom:8px">
        <div style="display:flex;align-items:flex-start;gap:10px">
          <div style="flex:1">
            <div style="font-size:14px;font-weight:700;color:var(--text)">${j.name}
              <span style="font-size:12px;color:var(--muted);font-weight:400">${j.ticker}</span>
            </div>
            <div style="font-size:12px;color:var(--muted);margin-top:3px">
              ${qtyLabel} 매수 · 조건: ${condLabel}
            </div>
          </div>
          <div style="display:flex;flex-direction:column;gap:5px;flex-shrink:0">
            <button onclick="abEdit('${j.ticker}')"
              style="background:none;border:1px solid var(--primary);color:var(--primary);
                     border-radius:8px;padding:5px 14px;font-size:12px;cursor:pointer;white-space:nowrap">
              ✎ 수정
            </button>
            <button onclick="abCancel('${j.ticker}')"
              style="background:none;border:1px solid var(--border);color:var(--muted);
                     border-radius:8px;padding:5px 14px;font-size:12px;cursor:pointer;white-space:nowrap">
              ✕ 취소
            </button>
          </div>
        </div>
        <div style="display:flex;gap:16px;margin-top:10px;flex-wrap:wrap">
          <div>
            <div style="font-size:10px;color:var(--muted)">현재가</div>
            <div id="ab-price-${j.ticker}" style="font-size:13px;font-weight:600;color:var(--text)">조회 중...</div>
          </div>
          ${j.condition_type === 'limit' ? `
          <div>
            <div style="font-size:10px;color:var(--muted)">목표 대비</div>
            <div id="ab-diff-${j.ticker}" style="font-size:13px">—</div>
          </div>` : ''}
          <div style="margin-left:auto;text-align:right">
            <div id="ab-status-${j.ticker}" style="font-size:12px;color:var(--muted)">대기 중</div>
          </div>
        </div>
        <div style="font-size:10px;color:var(--muted);margin-top:4px">등록: ${j.created_at||''}</div>
        ${j.condition_type === 'limit' ? `
        <div style="margin-top:8px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">
          <div id="ab-bar-${j.ticker}" style="height:100%;width:0%;background:#2563eb;transition:width .4s;border-radius:2px"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--muted);margin-top:2px">
          <span>목표 ${Number(j.target_price).toLocaleString()}원</span>
          <span id="ab-bar-label-${j.ticker}"></span>
        </div>` : ''}
      </div>`;
  }).join('');

  // 렌더 직후 현재가 반영
  abRefreshPrices();

  // 완료/취소 내역도 합산
  abRenderHistory();
}

function abRenderHistory() {
  const sellHistory = _atJobs.filter(j => j.status !== 'active');
  const buyHistory  = _abJobs.filter(j => j.status !== 'active');
  const all = [
    ...buyHistory.map(j => ({ ...j, _side: 'buy' })),
    ...sellHistory.map(j => ({ ...j, _side: 'sell' })),
  ].sort((a, b) => (b.executed_at || b.cancelled_at || b.created_at || '').localeCompare(
                    a.executed_at || a.cancelled_at || a.created_at || ''));

  const el = document.getElementById('at-history-list');
  if (!el) return;
  if (!all.length) { el.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:12px 0">내역 없음</div>'; return; }

  el.innerHTML = all.slice(0, 30).map(j => {
    const isDone = j.status === 'done';
    const isBuy  = j._side === 'buy';
    const sideLabel = isBuy
      ? `<span style="color:#2563eb;font-size:11px">🛒 매수</span>`
      : `<span style="color:var(--primary);font-size:11px">🎯 매도</span>`;
    const badge = isDone
      ? '<span style="background:#1a7a3a22;color:var(--green);border-radius:5px;padding:2px 7px;font-size:11px">완료</span>'
      : '<span style="background:#7a1a1a22;color:var(--red);border-radius:5px;padding:2px 7px;font-size:11px">취소</span>';
    const detail = isDone
      ? (isBuy
          ? `매수 ${(j.buy_price||0).toLocaleString()}원 × ${j.buy_qty||j.qty||'?'}주 · ${j.executed_at||''}`
          : `매도 ${(j.sell_price||0).toLocaleString()}원 · ${j.executed_at||''}`)
      : `취소일 ${j.cancelled_at||''}`;

    return `
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;
                  padding:10px 14px;margin-bottom:6px;display:flex;align-items:center;gap:10px">
        <div style="flex:1">
          <div style="font-size:13px;font-weight:600;color:var(--text)">
            ${sideLabel} ${j.name} <span style="font-size:11px;color:var(--muted)">${j.ticker}</span> ${badge}
          </div>
          <div style="font-size:11px;color:var(--muted);margin-top:2px">${detail}</div>
        </div>
      </div>`;
  }).join('');
}

// ════════════════════════════════════════════════════════════
//  사이클 트레이딩 (ac*)
// ════════════════════════════════════════════════════════════

// ── 자동완성 ──────────────────────────────────────────────
let _acAcTimer = null;

function onAcNameFocus() {
  const holdings = _atAccount?.holdings || [];
  if (holdings.length) {
    showAcAcLocal(holdings);
  } else {
    // 계좌 정보 없으면 힌트 표시
    const box = document.getElementById('ac-ac-list');
    if (box) {
      box.innerHTML = `<div style="padding:10px 14px;font-size:12px;color:var(--muted)">종목명을 입력하면 검색됩니다</div>`;
      box.style.display = 'block';
    }
  }
}

function onAcNameInput(v) {
  clearTimeout(_acAcTimer);
  const holdings = _atAccount?.holdings || [];
  if (!v.trim()) {
    if (holdings.length) showAcAcLocal(holdings);
    else hideAcAc();
    return;
  }
  const matched = holdings.filter(h =>
    h.name.includes(v.trim()) || h.ticker.startsWith(v.trim())
  );
  if (matched.length) { showAcAcLocal(matched); return; }
  _acAcTimer = setTimeout(() => fetchAcAc(v.trim()), 280);
}

async function showAcAcLocal(holdings) {
  const box = document.getElementById('ac-ac-list');
  if (!box) return;
  if (!holdings.length) { hideAcAc(); return; }

  // 현재가 비동기 조회 (순서대로 렌더 후 업데이트)
  box.innerHTML = holdings.map(h => {
    const safeN = h.name.replace(/'/g, "\\'");
    const pnlColor = h.pnl_pct >= 0 ? 'var(--green)' : 'var(--red)';
    return `<div onmousedown="selectAcAcItem('${h.ticker}','${safeN}',event)"
      style="padding:9px 14px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--border);
             display:flex;justify-content:space-between;align-items:center"
      onmouseover="this.style.background='var(--hover)'" onmouseout="this.style.background=''">
      <span>${h.name} <span style="font-size:11px;color:var(--green)">★보유</span></span>
      <span style="text-align:right;font-size:11px">
        <span id="ac-lp-${h.ticker}" style="color:var(--text);font-weight:600">조회중</span>
        <span style="color:${pnlColor};margin-left:4px">${h.pnl_pct >= 0 ? '+' : ''}${h.pnl_pct.toFixed(1)}%</span>
      </span>
    </div>`;
  }).join('');
  box.style.display = 'block';

  // 현재가 업데이트
  for (const h of holdings) {
    try {
      const r = await fetch(`/api/stock?ticker=${h.ticker}`);
      const d = await r.json();
      const el = document.getElementById(`ac-lp-${h.ticker}`);
      if (el && d.price) el.textContent = `${d.price.toLocaleString()}원`;
    } catch { /* ignore */ }
  }
}

async function fetchAcAc(q) {
  try {
    const r = await fetch(`/api/stock?q=${encodeURIComponent(q)}`);
    const d = await r.json();
    const items = d.items || [];
    if (!items.length) { hideAcAc(); return; }
    const box = document.getElementById('ac-ac-list');
    if (!box) return;
    box.innerHTML = items.slice(0, 8).map(it =>
      `<div onmousedown="selectAcAcItem('${it.ticker}','${it.name.replace(/'/g,"\\'")}',event)"
        style="padding:10px 14px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--border);
               display:flex;justify-content:space-between;align-items:center"
        onmouseover="this.style.background='var(--hover)'" onmouseout="this.style.background=''">
        <span>${it.name}</span>
        <span style="font-size:11px;color:var(--muted)">${it.ticker}</span>
      </div>`
    ).join('');
    box.style.display = 'block';
  } catch { hideAcAc(); }
}
function hideAcAc() {
  setTimeout(() => {
    const b = document.getElementById('ac-ac-list');
    if (b) b.style.display = 'none';
  }, 150);
}
function selectAcAcItem(ticker, name, e) {
  if (e) e.preventDefault();
  document.getElementById('ac-name').value = name;
  document.getElementById('ac-ticker').value = ticker;
  document.getElementById('ac-ticker-display').textContent = ticker;
  hideAcAc();
}

// ── 폼 UI ─────────────────────────────────────────────────
function acCondChange() {
  const v = document.querySelector('input[name="ac-cond"]:checked')?.value;
  const row = document.getElementById('ac-limit-row');
  if (row) row.style.display = v === 'limit' ? 'block' : 'none';
}
function acQtyTypeChange() {
  const v = document.querySelector('input[name="ac-qty-type"]:checked')?.value;
  const hint = document.getElementById('ac-qty-hint');
  if (hint) hint.textContent = v === 'amount' ? '매수 금액 (원)' : '매수할 수량 (주)';
}
function acOrderDvsnChange() {
  const v = document.querySelector('input[name="ac-order-dvsn"]:checked')?.value;
  const hint = document.getElementById('ac-order-dvsn-hint');
  const priceRow = document.getElementById('ac-limit-price-row');
  if (!hint) return;
  if (v === 'limit') {
    hint.textContent = '지정가 — 가격 미입력 시 현재가로 자동 적용 (NXT 포함)';
    hint.style.color = '#16a34a';
    if (priceRow) priceRow.style.display = 'block';
  } else {
    hint.textContent = 'NXT 시간대(장전 08:00~09:00 / 장후 15:30~20:00)에는 지정가만 허용됩니다';
    hint.style.color = 'var(--muted)';
    if (priceRow) priceRow.style.display = 'none';
  }
}

// ── 잡 등록 ───────────────────────────────────────────────
async function acRegister() {
  const ticker = document.getElementById('ac-ticker').value.trim();
  const name   = document.getElementById('ac-name').value.trim();
  if (!ticker) { document.getElementById('ac-msg').textContent = '종목을 선택해주세요'; return; }

  const condType   = document.querySelector('input[name="ac-cond"]:checked')?.value || 'market';
  const buyPrice   = parseInt(document.getElementById('ac-buy-price')?.value || '0') || 0;
  const orderDvsn  = document.querySelector('input[name="ac-order-dvsn"]:checked')?.value || 'market';
  const limitPrice = parseInt(document.getElementById('ac-limit-price')?.value || '0') || 0;
  const qtyType   = document.querySelector('input[name="ac-qty-type"]:checked')?.value || 'qty';
  const qtyVal    = parseInt(document.getElementById('ac-qty-val').value) || 0;
  const takePct   = parseFloat(document.getElementById('ac-take-pct').value) / 100 || 0.03;
  const rebuyDrop = parseFloat(document.getElementById('ac-rebuy-drop').value) / 100 || 0.02;
  const repeatTake= parseFloat(document.getElementById('ac-repeat-take').value) / 100 || 0.02;
  const maxCycles = parseInt(document.getElementById('ac-max-cycles').value) || 0;

  if (condType === 'limit' && !buyPrice) { document.getElementById('ac-msg').textContent = '목표 매수가를 입력해주세요'; return; }
  if (qtyVal <= 0) { document.getElementById('ac-msg').textContent = '수량/금액을 입력해주세요'; return; }

  let job;
  if (_acEditTicker) {
    // ── 수정 모드: 기존 잡 상태(phase/cycle_no/sell_price 등) 보존, 파라미터만 교체
    const existing = _acJobs.find(j => j.ticker === _acEditTicker && !['done','cancelled','stopped'].includes(j.status));
    if (!existing) { document.getElementById('ac-msg').textContent = '❌ 수정할 잡을 찾을 수 없음'; return; }
    job = {
      ...existing,
      name,
      order_dvsn:  orderDvsn,
      take_pct:    takePct,
      rebuy_drop:  rebuyDrop,
      repeat_take: repeatTake,
      max_cycles:  maxCycles,
    };
    // phase가 holding이면 새 take_pct로 sell_price 재계산 (프론트 예측치, 실제는 백엔드 계산)
    // → 단순히 파라미터만 저장; 백엔드가 다음 체크 시 반영
  } else {
    // ── 신규 등록
    job = {
      ticker, name,
      condition_type: condType,
      buy_price:   condType === 'limit' ? buyPrice : 0,
      order_dvsn:  orderDvsn,
      limit_price: orderDvsn === 'limit' && limitPrice > 0 ? limitPrice : 0,
      qty:      qtyType === 'qty' ? qtyVal : 0,
      amount:   qtyType === 'amount' ? qtyVal : 0,
      take_pct:    takePct,
      rebuy_drop:  rebuyDrop,
      repeat_take: repeatTake,
      max_cycles:  maxCycles,
      phase:  'waiting_buy',
      status: 'active',
      cycle_no: 0,
    };
  }

  const isEditMode = !!_acEditTicker;
  document.getElementById('ac-msg').textContent = isEditMode ? '수정 중...' : '등록 중...';
  try {
    const r = await fetch('/api/profit-cycle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(job),
    });
    if (r.ok) {
      const d = await r.json();
      const msgEl = document.getElementById('ac-msg');
      if (!isEditMode && condType === 'market' && d.triggered === false) {
        msgEl.innerHTML = `✅ 등록 완료 — <span style="color:var(--red)">즉시 트리거 실패 (GH_TOKEN workflow 권한 확인)</span>
          <a href="https://github.com/asakasimo1/stock-trader/actions" target="_blank"
             style="color:var(--primary);margin-left:6px;font-size:11px">수동 실행 →</a>`;
      } else {
        msgEl.textContent = isEditMode ? `✅ 수정 완료` : `✅ 등록 완료${condType==='market'?' — 즉시 실행 요청됨':''}`;
      }
      // 폼 초기화
      _acEditTicker = null;
      acResetEditMode();
      document.getElementById('ac-name').value = '';
      document.getElementById('ac-ticker').value = '';
      document.getElementById('ac-ticker-display').textContent = '—';
      document.getElementById('ac-qty-val').value = '';
      await atLoadAll();
    } else {
      document.getElementById('ac-msg').textContent = isEditMode ? '❌ 수정 실패' : '❌ 등록 실패';
    }
  } catch { document.getElementById('ac-msg').textContent = '❌ 네트워크 오류'; }
}

// ── 잡 수정 ───────────────────────────────────────────────
function acEdit(ticker) {
  const j = _acJobs.find(j => j.ticker === ticker && !['done','cancelled','stopped'].includes(j.status));
  if (!j) return;
  switchTab('autotrade');
  document.getElementById('ac-name').value  = j.name || '';
  document.getElementById('ac-ticker').value = j.ticker;
  document.getElementById('ac-ticker-display').textContent = j.ticker;
  // 최초 매수 방식
  const cond = j.condition_type || 'market';
  document.querySelectorAll('input[name="ac-cond"]').forEach(r => { r.checked = r.value === cond; });
  if (document.getElementById('ac-limit-row'))
    document.getElementById('ac-limit-row').style.display = cond === 'limit' ? 'block' : 'none';
  if (cond === 'limit' && j.buy_price)
    document.getElementById('ac-buy-price').value = j.buy_price;
  // 주문 방식
  const orderDvsn = j.order_dvsn || 'market';
  document.querySelectorAll('input[name="ac-order-dvsn"]').forEach(r => { r.checked = r.value === orderDvsn; });
  acOrderDvsnChange();
  if (orderDvsn === 'limit' && j.limit_price)
    document.getElementById('ac-limit-price').value = j.limit_price;
  // 수량/금액
  const qtyType = j.amount > 0 ? 'amount' : 'qty';
  document.querySelectorAll('input[name="ac-qty-type"]').forEach(r => { r.checked = r.value === qtyType; });
  document.getElementById('ac-qty-val').value = qtyType === 'amount' ? j.amount : (j.qty || '');
  acQtyTypeChange();
  // 수익률 파라미터
  if (j.take_pct    !== undefined) document.getElementById('ac-take-pct').value    = ((j.take_pct    || 0.03) * 100).toFixed(1);
  if (j.rebuy_drop  !== undefined) document.getElementById('ac-rebuy-drop').value  = ((j.rebuy_drop  || 0.02) * 100).toFixed(1);
  if (j.repeat_take !== undefined) document.getElementById('ac-repeat-take').value = ((j.repeat_take || 0.02) * 100).toFixed(1);
  document.getElementById('ac-max-cycles').value = j.max_cycles || 0;
  // 수정 모드 활성화
  _acEditTicker = ticker;
  const btn = document.getElementById('ac-register-btn');
  if (btn) btn.textContent = '수정 완료';
  const cancelBtn = document.getElementById('ac-cancel-edit-btn');
  if (cancelBtn) cancelBtn.style.display = 'block';
  document.getElementById('ac-name').scrollIntoView({ behavior: 'smooth', block: 'center' });
  document.getElementById('ac-name').focus();
}

// ── 수정 모드 UI 초기화 ────────────────────────────────────
function acResetEditMode() {
  const btn = document.getElementById('ac-register-btn');
  if (btn) btn.textContent = '사이클 잡 등록';
  const cancelBtn = document.getElementById('ac-cancel-edit-btn');
  if (cancelBtn) cancelBtn.style.display = 'none';
}

// ── 수정 취소 ─────────────────────────────────────────────
function acCancelEdit() {
  _acEditTicker = null;
  acResetEditMode();
  document.getElementById('ac-msg').textContent = '';
  document.getElementById('ac-name').value = '';
  document.getElementById('ac-ticker').value = '';
  document.getElementById('ac-ticker-display').textContent = '—';
  document.getElementById('ac-qty-val').value = '';
}

// ── 잡 취소 ───────────────────────────────────────────────
async function acCancel(ticker) {
  if (!confirm(`사이클 잡 취소: ${ticker}`)) return;
  try {
    const r = await fetch(`/api/profit-cycle?ticker=${ticker}`, { method: 'DELETE' });
    if (r.ok) await atLoadAll();
  } catch { alert('취소 실패'); }
}

// ── 활성 잡 렌더링 ────────────────────────────────────────
const AC_PHASE_LABEL = {
  waiting_buy:   '매수 대기',
  holding:       '보유 중',
  waiting_rebuy: '재매수 대기',
  done:          '완료',
  cancelled:     '취소',
  stopped:       '중단',
};
function acRenderJobs() {
  const el = document.getElementById('ac-active-list');
  if (!el) return;
  const active = _acJobs.filter(j => !['done','cancelled','stopped'].includes(j.status));
  if (!active.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">없음</div>';
    return;
  }
  el.innerHTML = active.map(j => {
    const phaseLabel = AC_PHASE_LABEL[j.phase] || j.phase;
    const phaseColor = j.phase === 'holding' ? 'var(--green)' : j.phase === 'waiting_rebuy' ? '#f59e0b' : '#2563eb';
    const cycleInfo  = j.cycle_no > 0 ? ` (사이클 ${j.cycle_no}회)` : '';
    const sellTarget = j.sell_price ? `목표매도 ${Number(j.sell_price).toLocaleString()}원` : '';
    const rebuyTarget= j.rebuy_price ? `재매수 목표 ${Number(j.rebuy_price).toLocaleString()}원` : '';
    const targetInfo = j.phase === 'holding' ? sellTarget : j.phase === 'waiting_rebuy' ? rebuyTarget : '';
    const qtyInfo    = j.qty > 0 ? `${j.qty}주` : j.amount > 0 ? `${Number(j.amount).toLocaleString()}원` : '';

    return `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div style="flex:1">
          <div style="font-size:13px;font-weight:600;color:var(--text)">
            ${j.name} <span style="font-size:11px;color:var(--muted)">${j.ticker}</span>
            <span style="margin-left:6px;padding:2px 7px;border-radius:10px;font-size:10px;font-weight:700;
              background:${phaseColor}22;color:${phaseColor}">${phaseLabel}${cycleInfo}</span>
          </div>
          <div style="font-size:11px;color:var(--muted);margin-top:4px">
            ${qtyInfo}
            ${j.buy_price ? ` | 매수가 ${Number(j.buy_price).toLocaleString()}원` : ''}
            ${targetInfo ? ` | ${targetInfo}` : ''}
          </div>
          <div style="font-size:11px;color:var(--muted);margin-top:2px">
            첫수익 ${((j.take_pct||0.03)*100).toFixed(1)}% → 하락${((j.rebuy_drop||0.02)*100).toFixed(1)}% 재매수 → 반복수익 ${((j.repeat_take||0.02)*100).toFixed(1)}%
            ${j.max_cycles > 0 ? ` | 최대 ${j.max_cycles}회` : ' | 무제한'}
          </div>
          <div id="ac-price-${j.ticker}" style="font-size:12px;color:var(--muted);margin-top:4px">—</div>
          <div id="ac-pnl-${j.ticker}" style="font-size:12px;margin-top:2px"></div>
          <div style="margin-top:6px;height:5px;background:var(--border);border-radius:3px;overflow:hidden">
            <div id="ac-bar-${j.ticker}" style="height:100%;width:0%;background:#16a34a;transition:width .4s"></div>
          </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:5px;margin-left:10px">
          <button onclick="acEdit('${j.ticker}')"
            style="padding:4px 10px;background:none;color:var(--primary);
              border:1px solid var(--primary)88;border-radius:6px;font-size:11px;cursor:pointer;white-space:nowrap">✎수정</button>
          <button onclick="acCancel('${j.ticker}')"
            style="padding:4px 10px;background:var(--red)22;color:var(--red);
              border:1px solid var(--red)44;border-radius:6px;font-size:11px;cursor:pointer;white-space:nowrap">✕중단</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

// ── 30초 가격 갱신 ────────────────────────────────────────
async function acRefreshPrices() {
  const active = _acJobs.filter(j => !['done','cancelled','stopped'].includes(j.status));
  for (const job of active) {
    try {
      const r = await fetch(`/api/stock?ticker=${job.ticker}`);
      const d = await r.json();
      if (!d.price) continue;
      const cur      = d.price;
      const priceEl  = document.getElementById(`ac-price-${job.ticker}`);
      const pnlEl    = document.getElementById(`ac-pnl-${job.ticker}`);
      const barEl    = document.getElementById(`ac-bar-${job.ticker}`);

      if (priceEl) priceEl.textContent = `현재가: ${cur.toLocaleString()}원  (${d.chgPct >= 0 ? '+' : ''}${d.chgPct}%)`;

      if (job.phase === 'holding' && job.buy_price && job.sell_price) {
        const buyTotal = job.buy_price * job.qty * (1 + AT_BUY_FEE);
        const sellNet  = cur * job.qty * (1 - AT_SELL_FEE);
        const netPnl   = sellNet - buyTotal;
        const netPct   = netPnl / buyTotal * 100;
        if (pnlEl) pnlEl.innerHTML = `<span style="color:${netPnl>=0?'var(--green)':'var(--red)'};font-weight:700">${netPnl>=0?'+':''}${Math.round(netPnl).toLocaleString()}원 (${netPct>=0?'+':''}${netPct.toFixed(2)}%)</span>`;
        const range = job.sell_price - job.buy_price;
        if (barEl && range > 0) {
          const pct = Math.min(100, Math.max(0, (cur - job.buy_price) / range * 100));
          barEl.style.width = `${pct}%`;
          barEl.style.background = cur >= job.sell_price ? 'var(--green)' : '#16a34a';
        }
      } else if (job.phase === 'waiting_rebuy' && job.rebuy_price) {
        const diff = cur - job.rebuy_price;
        if (pnlEl) pnlEl.innerHTML = diff <= 0
          ? `<span style="color:var(--green);font-weight:700">🎯 재매수 조건 달성!</span>`
          : `<span style="color:var(--muted)">재매수까지 -${diff.toLocaleString()}원 남음</span>`;
        if (barEl) {
          const ref = job.last_sell || cur * 1.05;
          const pct = Math.min(100, Math.max(0, (1 - (cur - job.rebuy_price) / (ref - job.rebuy_price)) * 100));
          barEl.style.width = `${pct}%`;
          barEl.style.background = diff <= 0 ? 'var(--green)' : '#f59e0b';
        }
      }
    } catch (_) {}
  }
}

async function abRefreshPrices() {
  const active = _abJobs.filter(j => j.status === 'active');
  for (const job of active) {
    try {
      const r = await fetch(`/api/stock?ticker=${job.ticker}`);
      const d = await r.json();
      if (!d.price) continue;
      const cur = d.price;
      const priceEl  = document.getElementById(`ab-price-${job.ticker}`);
      const diffEl   = document.getElementById(`ab-diff-${job.ticker}`);
      const statusEl = document.getElementById(`ab-status-${job.ticker}`);
      const barEl    = document.getElementById(`ab-bar-${job.ticker}`);
      const barLabel = document.getElementById(`ab-bar-label-${job.ticker}`);

      if (priceEl) priceEl.textContent = `${cur.toLocaleString()}원  (${d.chgPct >= 0 ? '+' : ''}${d.chgPct}%)`;

      if (job.condition_type === 'limit' && job.target_price) {
        const tp   = Number(job.target_price);
        const diff = cur - tp;
        const reached = cur <= tp;
        if (diffEl) {
          diffEl.innerHTML = reached
            ? `<span style="color:var(--green);font-weight:700">🎯 조건 달성!</span>`
            : `<span style="color:var(--muted)">목표까지 ${diff.toLocaleString()}원 남음</span>`;
        }
        if (statusEl) {
          statusEl.textContent = reached ? '✅ 다음 실행 시 매수 예정' : '대기 중';
          statusEl.style.color = reached ? 'var(--green)' : 'var(--muted)';
        }
        // 진행률 바: 현재가가 목표가에 가까울수록 100%
        if (barEl) {
          const ref   = Math.max(cur, tp) * 1.05;
          const pct   = Math.min(100, Math.max(0, (1 - (cur - tp) / (ref - tp)) * 100));
          barEl.style.width = `${pct}%`;
          barEl.style.background = reached ? 'var(--green)' : '#2563eb';
        }
        if (barLabel) barLabel.textContent = `현재가 ${cur.toLocaleString()}원`;
      } else if (job.condition_type === 'market') {
        if (statusEl) { statusEl.textContent = '⏳ 다음 실행 대기 중'; statusEl.style.color = 'var(--primary)'; }
      }
    } catch (_) {}
  }
}


// ══════════════════════════════════════════════════════════
// 자동코인매매 탭
// ══════════════════════════════════════════════════════════

// ── 섹션 접기/펼치기 ─────────────────────────────────────
function toggleTradeSection(id) {
  const body = document.getElementById(id + '-body');
  const icon = document.getElementById(id + '-toggle-icon');
  if (!body) return;
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : 'block';
  if (icon) icon.textContent = isOpen ? '▶' : '▼';
  try { localStorage.setItem('trade-section-' + id, isOpen ? '0' : '1'); } catch (_) {}
}

function _restoreTradeSection(id, defaultOpen) {
  try {
    const saved = localStorage.getItem('trade-section-' + id);
    const open  = saved !== null ? saved === '1' : defaultOpen;
    const body  = document.getElementById(id + '-body');
    const icon  = document.getElementById(id + '-toggle-icon');
    if (body) body.style.display = open ? 'block' : 'none';
    if (icon) icon.textContent   = open ? '▼' : '▶';
  } catch (_) {}
}

const COIN_LIST = [
  {ticker:'KRW-BTC',  name:'비트코인',       symbol:'BTC'},
  {ticker:'KRW-ETH',  name:'이더리움',        symbol:'ETH'},
  {ticker:'KRW-XRP',  name:'리플',            symbol:'XRP'},
  {ticker:'KRW-SOL',  name:'솔라나',          symbol:'SOL'},
  {ticker:'KRW-USDT', name:'테더',            symbol:'USDT'},
  {ticker:'KRW-DOGE', name:'도지코인',        symbol:'DOGE'},
  {ticker:'KRW-ADA',  name:'에이다',          symbol:'ADA'},
  {ticker:'KRW-AVAX', name:'아발란체',        symbol:'AVAX'},
  {ticker:'KRW-DOT',  name:'폴카닷',          symbol:'DOT'},
  {ticker:'KRW-LINK', name:'체인링크',        symbol:'LINK'},
  {ticker:'KRW-ATOM', name:'코스모스',        symbol:'ATOM'},
  {ticker:'KRW-MATIC',name:'폴리곤',          symbol:'MATIC'},
  {ticker:'KRW-TRX',  name:'트론',            symbol:'TRX'},
  {ticker:'KRW-SHIB', name:'시바이누',        symbol:'SHIB'},
  {ticker:'KRW-LTC',  name:'라이트코인',      symbol:'LTC'},
  {ticker:'KRW-BCH',  name:'비트코인캐시',    symbol:'BCH'},
  {ticker:'KRW-ETC',  name:'이더리움클래식',  symbol:'ETC'},
  {ticker:'KRW-NEAR', name:'니어프로토콜',    symbol:'NEAR'},
  {ticker:'KRW-AAVE', name:'에이브',          symbol:'AAVE'},
  {ticker:'KRW-UNI',  name:'유니스왑',        symbol:'UNI'},
  {ticker:'KRW-SAND', name:'샌드박스',        symbol:'SAND'},
  {ticker:'KRW-SUI',  name:'수이',            symbol:'SUI'},
  {ticker:'KRW-HBAR', name:'헤데라',          symbol:'HBAR'},
  {ticker:'KRW-ARB',  name:'아비트럼',        symbol:'ARB'},
  {ticker:'KRW-OP',   name:'옵티미즘',        symbol:'OP'},
  {ticker:'KRW-XLM',  name:'스텔라루멘',      symbol:'XLM'},
  {ticker:'KRW-ALGO', name:'알고랜드',        symbol:'ALGO'},
  {ticker:'KRW-FLOW', name:'플로우',          symbol:'FLOW'},
  {ticker:'KRW-MANA', name:'디센트럴랜드',    symbol:'MANA'},
  {ticker:'KRW-CHZ',  name:'칠리즈',          symbol:'CHZ'},
  {ticker:'KRW-KLAY', name:'클레이튼',        symbol:'KLAY'},
  {ticker:'KRW-FIL',  name:'파일코인',        symbol:'FIL'},
  {ticker:'KRW-ICP',  name:'인터넷컴퓨터',    symbol:'ICP'},
  {ticker:'KRW-SEI',  name:'세이',            symbol:'SEI'},
];

const COIN_FEE = 0.0005;  // 업비트 수수료 0.05%

let _ctBuyJobs    = [];
let _ctSellJobs   = [];
let _ctCycleJobs  = [];
let _ctSignalJobs = [];
let _ctGridJobs   = [];
let _ctAccount   = null;
let _ctRefreshTimer = null;
let _ctPriceTimer   = null;
let _ctBalanceTimer = null;
let _ctPriceCache   = {};  // ticker → { priceText, pnlHtml }
let _ctConfig = { autoRefreshSec: 0 };

function ctLoadConfig() {
  try {
    const saved = JSON.parse(localStorage.getItem('ct_config') || '{}');
    _ctConfig = { autoRefreshSec: 0, ...saved };
  } catch (_) {}
  const arEl = document.getElementById('ct-cfg-auto-refresh');
  if (arEl) arEl.value = _ctConfig.autoRefreshSec;
}

function ctSaveConfig() {
  _ctConfig.autoRefreshSec = parseInt(document.getElementById('ct-cfg-auto-refresh')?.value) || 0;
  localStorage.setItem('ct_config', JSON.stringify(_ctConfig));
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
  const msg = document.getElementById('ct-cfg-msg');
  if (msg) { msg.textContent = '✓ 저장 완료'; msg.style.color = 'var(--green)'; setTimeout(() => { msg.textContent = ''; }, 2000); }
}

function ctCheckDaemonStatus() {
  const label = document.getElementById('ct-daemon-status-label');
  const updEl = document.getElementById('ct-daemon-updated-at');
  if (!_ctAccount) {
    if (label) { label.textContent = '데이터 없음'; label.style.color = 'var(--muted)'; }
    if (updEl) updEl.textContent = '잔고 새로고침 후 확인하세요';
    return;
  }
  const updatedAt = _ctAccount.updated_at || '';
  if (!updatedAt) {
    if (label) { label.textContent = '갱신 시각 없음'; label.style.color = 'var(--muted)'; }
    return;
  }
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


// ═══════════════════════════════════════════════════════════════
// 주식 그리드 트레이딩  (ag- prefix)
// ═══════════════════════════════════════════════════════════════
let _agJobs = [];
let _agAcTimer = null;

// ── 종목 자동완성 ──────────────────────────────────────────────
function onAgNameInput(val) {
  if (!val || val.length < 1) { hideAgAc(); return; }
  const results = (_stockList || []).filter(s =>
    s.name.includes(val) || s.ticker.startsWith(val)
  ).slice(0, 8);
  const box = document.getElementById('ag-ac-list');
  if (!box) return;
  if (!results.length) { box.style.display = 'none'; return; }
  box.innerHTML = results.map(s =>
    `<div style="padding:8px 12px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--border)"
       onmousedown="selectAgStock('${s.ticker}','${s.name}')">${s.name} <span style="color:var(--muted);font-size:11px">${s.ticker}</span></div>`
  ).join('');
  box.style.display = 'block';
}
function hideAgAc() { setTimeout(() => { const b = document.getElementById('ag-ac-list'); if(b) b.style.display='none'; }, 150); }
function selectAgStock(ticker, name) {
  document.getElementById('ag-ticker').value = ticker;
  document.getElementById('ag-name').value   = name;
  document.getElementById('ag-ticker-display').textContent = ticker;
  hideAgAc();
  // 현재가 조회 힌트
  fetch(`/api/stock?ticker=${ticker}`).then(r=>r.json()).then(d=>{
    const el = document.getElementById('ag-price-hint');
    if (el && d.price) {
      el.textContent = `현재가 ${d.price.toLocaleString()}원`;
      // 상하한 자동 제안
      const low  = Math.floor(d.price * 0.9 / 100) * 100;
      const high = Math.ceil(d.price * 1.1 / 100) * 100;
      const loEl = document.getElementById('ag-lower'); if(loEl && !loEl.value) loEl.value = low;
      const hiEl = document.getElementById('ag-upper'); if(hiEl && !hiEl.value) hiEl.value = high;
    }
  }).catch(()=>{});
}

// ── 폼 토글 ───────────────────────────────────────────────────
function agToggleForm() {
  const form = document.getElementById('ag-form');
  const btn  = document.getElementById('ag-add-btn');
  if (!form) return;
  const open = form.style.display === 'none' || !form.style.display;
  form.style.display = open ? 'block' : 'none';
  if (btn) btn.textContent = open ? '✕ 닫기' : '+ 추가';
}

// ── 등록 ──────────────────────────────────────────────────────
async function agRegister() {
  const ticker  = document.getElementById('ag-ticker').value.trim();
  const name    = document.getElementById('ag-name').value.trim();
  const lower   = parseInt(document.getElementById('ag-lower')?.value) || 0;
  const upper   = parseInt(document.getElementById('ag-upper')?.value) || 0;
  const gridPct = parseFloat(document.getElementById('ag-pct')?.value) || 1.5;
  const krw     = parseInt(document.getElementById('ag-krw')?.value) || 0;
  const reinit  = parseInt(document.getElementById('ag-reinit')?.value) || 0;
  const msgEl   = document.getElementById('ag-msg');

  if (!ticker) { if(msgEl) msgEl.innerHTML='<span style="color:var(--red)">종목을 검색해 선택하세요</span>'; return; }
  if (lower <= 0 || upper <= 0) { if(msgEl) msgEl.innerHTML='<span style="color:var(--red)">하한/상한 가격을 입력하세요</span>'; return; }
  if (upper <= lower) { if(msgEl) msgEl.innerHTML='<span style="color:var(--red)">상한이 하한보다 커야 합니다</span>'; return; }
  if (krw < 10000) { if(msgEl) msgEl.innerHTML='<span style="color:var(--red)">격자당 금액 1만원 이상</span>'; return; }
  if (gridPct < 0.5) { if(msgEl) msgEl.innerHTML='<span style="color:var(--red)">격자 간격 0.5% 이상</span>'; return; }

  if(msgEl) msgEl.textContent = '등록 중...';
  const payload = { ticker, name, lower_price: lower, upper_price: upper, grid_pct: gridPct, krw_per_grid: krw };
  if (reinit >= 10) payload.auto_reinit_minutes = reinit;

  try {
    const r = await fetch('/api/stock-grid', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (r.ok && !d.error) {
      if(msgEl) msgEl.innerHTML = '<span style="color:var(--green)">✅ 등록 완료 — 다음 30초 폴링에서 초기화 시작</span>';
      document.getElementById('ag-form').style.display = 'none';
      document.getElementById('ag-add-btn').textContent = '+ 추가';
      await atLoadAll();
    } else {
      if(msgEl) msgEl.innerHTML = `<span style="color:var(--red)">${d.error || '저장 실패'}</span>`;
    }
  } catch(e) {
    if(msgEl) msgEl.innerHTML = `<span style="color:var(--red)">오류: ${e.message}</span>`;
  }
}

// ── 중단 ──────────────────────────────────────────────────────
async function agStop(id) {
  if (!confirm('그리드를 중단하고 모든 미체결 주문을 취소하시겠습니까?')) return;
  const r = await fetch(`/api/stock-grid?id=${id}`, { method: 'DELETE' });
  if (r.ok) await atLoadAll();
}

// ── 렌더 ──────────────────────────────────────────────────────
function agRenderJobs() {
  const el = document.getElementById('ag-list');
  if (!el) return;
  const jobs = Array.isArray(_agJobs) ? _agJobs : [];
  const active  = jobs.filter(j => ['init','active','reinit','stopping'].includes(j.status));
  const stopped = jobs.filter(j => ['stopped'].includes(j.status)).slice(0, 3);

  if (!active.length && !stopped.length) {
    el.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">없음</div>';
    return;
  }

  const statusLabel = { init:'초기화중', active:'운영중', reinit:'재초기화중', stopping:'중단중', stopped:'중단됨' };
  const statusColor = { init:'var(--primary)', active:'var(--green)', reinit:'var(--orange)', stopping:'var(--red)', stopped:'var(--muted)' };

  const renderJob = (job) => {
    const grids = job.grids || [];
    const buyCnt  = grids.filter(g=>g.state==='buy_waiting').length;
    const sellCnt = grids.filter(g=>g.state==='sell_waiting').length;
    const idleCnt = grids.filter(g=>g.state==='idle').length;
    const pnl = job.total_profit_krw || 0;
    const pnlColor = pnl >= 0 ? 'var(--green)' : 'var(--red)';
    const canStop = ['init','active','reinit'].includes(job.status);

    return `<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <div>
          <span style="font-weight:700;font-size:13px">${job.name || job.ticker}</span>
          <span style="font-size:11px;color:var(--muted);margin-left:6px">${job.ticker}</span>
          <span style="font-size:11px;color:${statusColor[job.status]};margin-left:6px;font-weight:600">${statusLabel[job.status]||job.status}</span>
        </div>
        ${canStop ? `<button onclick="agStop('${job.id}')" style="padding:3px 10px;background:none;border:1px solid var(--red);border-radius:6px;font-size:11px;color:var(--red);cursor:pointer">중단</button>` : ''}
      </div>
      <div style="font-size:11px;color:var(--muted);display:flex;gap:12px;flex-wrap:wrap">
        <span>범위 ${(job.lower_price||0).toLocaleString()}~${(job.upper_price||0).toLocaleString()}원</span>
        <span>간격 ${job.grid_pct||1.5}%</span>
        <span>격자당 ${(job.krw_per_grid||0).toLocaleString()}원</span>
      </div>
      ${grids.length ? `<div style="font-size:11px;color:var(--text);margin-top:6px;display:flex;gap:12px;flex-wrap:wrap">
        <span style="color:var(--primary)">매수대기 ${buyCnt}개</span>
        <span style="color:var(--orange)">매도대기 ${sellCnt}개</span>
        <span style="color:var(--muted)">대기 ${idleCnt}개</span>
        <span style="color:${pnlColor};font-weight:600">누적수익 ${pnl>=0?'+':''}${Math.round(pnl).toLocaleString()}원 (${job.trade_count||0}회)</span>
      </div>` : ''}
    </div>`;
  };

  el.innerHTML = active.map(renderJob).join('') +
    (stopped.length ? `<div style="font-size:12px;color:var(--muted);margin-top:4px">종료된 잡</div>${stopped.map(renderJob).join('')}` : '');
}


// ── 자동 주식매매 일별 수익 현황 ──────────────────────────────────

function atCalcDayProfit(dateStr) {
  const BUY_FEE  = 0.00015;  // 매수 수수료 0.015%
  const SELL_FEE = 0.00195;  // 매도 수수료 0.015% + 증권거래세 0.18%

  // 매도잡: sell_price는 실제 체결가, buy_price는 잡 등록 시 평단 → 정확한 실현손익
  const sells = (_atJobs || []).filter(j =>
    j.status === 'done' && (j.executed_at || '').startsWith(dateStr)
  ).map(j => {
    const sellPrice = j.sell_price || j.exec_price || j.target_price || 0;
    const buyPrice  = j.buy_price  || 0;
    const qty       = j.qty        || 0;
    const sellNet   = sellPrice * qty * (1 - SELL_FEE);
    const buyCost   = buyPrice  * qty * (1 + BUY_FEE);
    return { name: j.name || j.ticker, ticker: j.ticker, qty, buyPrice, sellPrice,
      profit: buyPrice > 0 ? Math.round(sellNet - buyCost) : null,
      time: (j.executed_at || '').slice(11, 16), buyTime: null };
  });

  // 사이클 매도: buy_price + sell_price_exec 모두 저장되어 있음
  const cycleSells = (_acJobs || []).filter(j =>
    j.sold_at && j.sold_at.startsWith(dateStr) && j.sell_price_exec && j.hold_qty
  ).map(j => {
    const sellPrice = j.sell_price_exec;
    const buyPrice  = j.buy_price || 0;
    const qty       = j.hold_qty;
    const sellNet   = sellPrice * qty * (1 - SELL_FEE);
    const buyCost   = buyPrice  * qty * (1 + BUY_FEE);
    return { name: j.name || j.ticker, ticker: j.ticker, qty, buyPrice, sellPrice,
      profit: buyPrice > 0 ? Math.round(sellNet - buyCost) : null,
      time: (j.sold_at || '').slice(11, 16),
      buyTime: (j.bought_at || '').slice(11, 16) || null };
  });

  // 주식 그리드 당일 체결 내역 (trade_history 배열)
  const gridSells = (Array.isArray(_agJobs) ? _agJobs : []).flatMap(job => {
    const hist = Array.isArray(job.trade_history) ? job.trade_history : [];
    return hist
      .filter(h => h.date === dateStr)
      .map(h => ({
        name:      job.name || job.ticker,
        ticker:    job.ticker,
        qty:       h.qty,
        buyPrice:  h.buy_price,
        sellPrice: h.sell_price,
        profit:    h.buy_price ? Math.round(h.profit) : null,
        time:      (h.time || '').slice(0, 5),
        buyTime:   (h.buy_time || '').slice(0, 5) || null,
        source:    'grid',
      }));
  });

  const items = [...sells, ...cycleSells, ...gridSells].sort((a, b) => a.time.localeCompare(b.time));
  return {
    date:      dateStr,
    sells:     items,
    netProfit: items.reduce((s, o) => s + (o.profit ?? 0), 0),
  };
}

function atRenderDailyCard(data, idx) {
  const card = document.getElementById(`at-day-card-${idx}`);
  if (!card) return;

  if (!data || !data.sells.length) {
    card.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:4px 0">체결 없음</div>';
    return;
  }
  const net    = data.netProfit;
  const netCls = net > 0 ? '#22c55e' : net < 0 ? '#ef4444' : 'var(--muted)';
  const netStr = (net >= 0 ? '+' : '') + net.toLocaleString() + '원';
  const cnt    = data.sells.length;

  card.innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px">
      <div style="text-align:center">
        <div style="font-size:9px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">순수익</div>
        <div style="font-size:17px;font-weight:800;color:${netCls}">${netStr}</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:9px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">매도 체결</div>
        <div style="font-size:17px;font-weight:800;color:var(--text)">${cnt}건</div>
      </div>
    </div>
    <div id="at-day-detail-${idx}" style="display:none;margin-top:10px">
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
            ${data.sells.map(o => {
              const p = o.profit;
              const hasProfit = p !== null && p !== undefined;
              const pRounded  = hasProfit ? Math.round(p) : null;
              const pCls      = !hasProfit ? 'var(--muted)' : pRounded > 0 ? '#22c55e' : pRounded < 0 ? '#ef4444' : 'var(--muted)';
              const pStr      = hasProfit ? `${pRounded >= 0 ? '+' : ''}${pRounded.toLocaleString()}원` : '<span style="font-size:10px">매수가 미기록</span>';
              const isGrid    = o.source === 'grid';
              const isLoss    = hasProfit && pRounded < 0;
              const sellLabel = isLoss ? '손절' : '매도';
              const sellColor = isLoss ? '#ef4444' : (isGrid ? '#3b82f6' : '#22c55e');
              const buyPriceStr = o.buyPrice ? `${o.buyPrice.toLocaleString()}원` : '—';
              const qty = typeof o.qty === 'number'
                ? (Number.isInteger(o.qty) ? o.qty.toLocaleString() : o.qty.toLocaleString(undefined, {maximumFractionDigits:6}))
                : o.qty;
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
                  <td style="padding:5px 8px;text-align:right;color:var(--text);font-variant-numeric:tabular-nums">${(o.sellPrice||0).toLocaleString()}원</td>
                  <td style="padding:5px 8px;text-align:right;font-weight:700;color:${pCls}">${pStr}</td>
                </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
    </div>
    <button onclick="atToggleDayDetail(${idx})" id="at-day-toggle-${idx}"
      style="margin-top:8px;background:none;border:none;color:var(--muted);font-size:11px;cursor:pointer;padding:0">▼ 상세 보기</button>
  `;
}

function atToggleDayDetail(idx) {
  const el  = document.getElementById(`at-day-detail-${idx}`);
  const btn = document.getElementById(`at-day-toggle-${idx}`);
  if (!el) return;
  const open = el.style.display === 'none';
  el.style.display = open ? 'block' : 'none';
  if (btn) btn.textContent = open ? '▲ 접기' : '▼ 상세 보기';
}

function atLoadToday() {
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

  const wrap = document.getElementById('at-daily-wrap');
  if (!wrap) return;
  const todayStr = kstNow.toISOString().slice(0, 10);
  wrap.innerHTML = dates.map((_, i) => `
    <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px">
      <div style="font-size:10px;font-weight:700;color:var(--muted);margin-bottom:10px;display:flex;justify-content:space-between;align-items:center">
        <span style="text-transform:uppercase;letter-spacing:.06em">${labels[i]}</span>
      </div>
      <div id="at-day-card-${i}"></div>
    </div>
  `).join('');

  dates.forEach((date, i) => {
    const targetDate = date === null ? todayStr : date;
    const dayData = atCalcDayProfit(targetDate);
    atRenderDailyCard(dayData, i);
  });
}
