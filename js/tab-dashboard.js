async function initDashboard() {
  // 재방문: _sharedGistData가 백그라운드 갱신됐으면 _dashData에 동기화 후 즉시 렌더
  if (_dashData) {
    if (_sharedGistData && _sharedGistData !== _dashData) {
      _dashData = { ..._sharedGistData, ipo: _ipoRecords };
    }
    renderDashboard(_dashData);
    startAccountPolling();
    loadWatchlist();
    return;
  }
  // 첫 방문: 데이터 fetch (stale-while-revalidate → 캐시 있으면 즉시 반환)
  try {
    // 대시보드 데이터와 IPO 레코드를 동시에 로드
    // (_ipoRecords가 없으면 캘린더 모달의 청약완료·배정입력 버튼이 동작하지 않음)
    const ipoPromise = _ipoRecords.length > 0
      ? Promise.resolve({ records: _ipoRecords, last_sync: _ipoLastSync })
      : fetch('/api/ipo').then(r => r.ok ? r.json() : {});
    const [dashData, ipoData] = await Promise.all([
      _fetchGistData(),
      ipoPromise,
    ]);
    _dashData = dashData;
    if (ipoData.last_sync !== undefined) _ipoLastSync = ipoData.last_sync;
    // /api/ipo 실패 시 /api/data의 ipo 배열로 폴백 (GH_TOKEN 미설정 등)
    _ipoRecords = (ipoData.records && ipoData.records.length)
      ? ipoData.records
      : (_dashData.ipo || []);
    // id 없는 레코드에 자동 id 부여
    if (ensureIds(_ipoRecords)) saveIpoRecords();
    // 항상 동기화: 캘린더의 ipo 객체와 _ipoRecords가 같은 참조를 공유
    _dashData.ipo = _ipoRecords;
  } catch(e) {
    _dashData = _dashData || { briefing: [], picks: [], signals: [] };
    console.warn('대시보드 데이터 로드 실패:', e.message);
  }
  renderDashboard(_dashData);
  startAccountPolling();
  loadWatchlist();
}

function renderDashboard(data) {
  renderTraderTrades(data.trader_trades || []);
  renderTraderSummary(data.trader_trades || [], data.account_balance || null);
  renderIpoList(data.ipo || []);
  renderCalendar(data);
}

// ── KIS 계좌 실시간 폴링 ─────────────────────────────────
let _accountPollTimer = null;
async function _pollAccount() {
  try {
    const r = await fetch('/api/data?mode=account');
    if (!r.ok) return;
    const { account_balance } = await r.json();
    if (!account_balance) return;
    if (_dashData) _dashData.account_balance = account_balance;
    renderTraderSummary(_dashData?.trader_trades || [], account_balance);
    // 자동매매 탭 계좌 현황도 갱신
    if (typeof atRenderAccount === 'function') {
      _atAccount = account_balance;
      atRenderAccount();
    }
  } catch (e) {
    console.warn('KIS 계좌 폴링 실패:', e.message);
  }
}
function startAccountPolling() {
  if (_accountPollTimer) return;
  // initDashboard에서 이미 account_balance를 로드했으면 즉시 실행 건너뜀
  if (!(_sharedGistData && _sharedGistData.account_balance)) _pollAccount();
  _accountPollTimer = setInterval(_pollAccount, 30000); // 30초마다
}

// ── 브리핑 목록 ─────────────────────────────────────────
let _briefingItems = [], _briefingExpanded = false;

function renderBriefingList(items) { _briefingItems = items; _renderBriefing(); }

function _renderBriefing() {
  const el = document.getElementById('list-briefing');
  if (!el) return;
  const items = _briefingItems;
  if (!items.length) { el.innerHTML = '<div class="empty-msg">아직 브리핑 데이터가 없습니다</div>'; return; }

  const today     = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);

  // 오늘+어제 필터. 없으면 최근 2개 날짜로 폴백
  let visible = items.filter(i => i.date === today || i.date === yesterday);
  if (!visible.length) {
    const dates = [...new Set(items.map(i => i.date))].sort().reverse().slice(0, 2);
    visible = items.filter(i => dates.includes(i.date));
  }
  const toShow = _briefingExpanded ? items.slice(0, 20) : visible;
  const hasMore = items.length > visible.length;

  const moreBtn = hasMore ? `
    <div style="text-align:center;padding:6px 0">
      <button onclick="_briefingExpanded=!_briefingExpanded;_renderBriefing()"
        style="background:none;border:1px solid var(--border);border-radius:8px;padding:5px 18px;font-size:12px;color:var(--muted);cursor:pointer">
        ${_briefingExpanded ? '▲ 접기' : '▼ 더보기'}
      </button>
    </div>` : '';

  el.innerHTML = toShow.map(item => `
    <div class="result-item">
      <div class="result-item-header">
        <span class="result-badge briefing">📊 일일 브리핑</span>
        <span style="font-size:11px;color:var(--muted)">${item.date} ${item.time}</span>
      </div>
      <div class="result-stocks">
        ${(item.stocks || []).map(s => `
          <span class="stock-chip">
            ${s.name}
            <span class="${s.chg_pct >= 0 ? 'up' : 'dn'}">${s.chg_pct >= 0 ? '▲' : '▼'}${Math.abs(s.chg_pct).toFixed(1)}%</span>
            <span style="color:var(--muted);font-size:10px">${s.signal?.replace(/[🟢🔴🟡]/g,'').trim()}</span>
          </span>`).join('')}
      </div>
    </div>`).join('') + moreBtn;
}

// ── 거래 내역 (stock_trader 실거래) ─────────────────────
let _traderTradesExpanded = false;

function renderTraderTrades(items) {
  const el = document.getElementById('list-trader-trades');
  if (!el) return;
  const sorted = [...items].sort((a, b) =>
    (b.date + b.time).localeCompare(a.date + a.time));
  if (!sorted.length) {
    el.innerHTML = '<div class="empty-msg">거래 내역이 없습니다</div>';
    return;
  }

  const toShow  = _traderTradesExpanded ? sorted.slice(0, 50) : sorted.slice(0, 10);
  const hasMore = sorted.length > 10;

  const moreBtn = hasMore ? `
    <div style="text-align:center;padding:6px 0">
      <button onclick="_traderTradesExpanded=!_traderTradesExpanded;renderTraderTrades(${JSON.stringify(items).replace(/</g,'\\u003c')})"
        style="background:none;border:1px solid var(--border);border-radius:8px;padding:5px 18px;font-size:12px;color:var(--muted);cursor:pointer">
        ${_traderTradesExpanded ? '▲ 접기' : `▼ 더보기 (${sorted.length}건)`}
      </button>
    </div>` : '';

  el.innerHTML = toShow.map(t => {
    const isBuy  = t.type === 'buy';
    const badge  = isBuy
      ? `<span style="background:#dcfce7;color:#166534;border-radius:5px;padding:2px 8px;font-size:11px;font-weight:700">매수</span>`
      : `<span style="background:#fee2e2;color:#991b1b;border-radius:5px;padding:2px 8px;font-size:11px;font-weight:700">매도</span>`;
    const reasonHtml = t.reason
      ? `<span style="font-size:11px;color:var(--muted)">[${t.reason}]</span>` : '';

    // 손익 (매도 시)
    let pnlHtml = '';
    if (!isBuy && t.pnl != null) {
      const up   = t.pnl >= 0;
      const sign = up ? '+' : '';
      pnlHtml = `<span style="color:${up?'#16a34a':'#dc2626'};font-size:12px;font-weight:700">
        손익 ${sign}${Number(t.pnl).toLocaleString()}원&nbsp;(${sign}${t.pnl_pct}%)
      </span>`;
    }

    return `
    <div style="display:flex;align-items:flex-start;justify-content:space-between;
                padding:9px 0;border-bottom:1px solid var(--border)">
      <div style="flex:1;min-width:0">
        <!-- 종목명 + 매수/매도 뱃지 -->
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
          ${badge}
          <span style="font-weight:700;font-size:13px">${t.name || t.ticker}</span>
          <span style="font-size:11px;color:var(--muted)">${t.ticker}</span>
          ${reasonHtml}
        </div>
        <!-- 수량 · 단가 · 총금액 -->
        <div style="font-size:12px;color:var(--fg);margin-bottom:2px">
          <span style="color:var(--muted)">수량</span> <b>${Number(t.qty).toLocaleString()}주</b>
          &nbsp;·&nbsp;
          <span style="color:var(--muted)">단가</span> <b>${Number(t.price).toLocaleString()}원</b>
          &nbsp;·&nbsp;
          <span style="color:var(--muted)">금액</span> <b>${Number(t.amount).toLocaleString()}원</b>
        </div>
        <!-- 손익 (매도만) -->
        ${pnlHtml}
      </div>
      <div style="font-size:11px;color:var(--muted);white-space:nowrap;margin-left:10px;text-align:right">
        ${t.date}<br>${t.time}
      </div>
    </div>`;
  }).join('') + moreBtn;
}

// ── 공모주 구글 캘린더 연동 ───────────────────────────────

/** YYYY-MM-DD → YYYYMMDD */
function _gcDate(s) { return (s || '').replace(/-/g, ''); }

/** 하루 뒤 날짜 (종일 이벤트 end는 exclusive) */
function _gcNextDay(s) {
  if (!s) return '';
  const d = new Date(s + 'T00:00:00');
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10).replace(/-/g, '');
}

/**
 * 구글 캘린더 새 탭으로 이벤트 추가
 * type: 'sub' (청약) | 'list' (상장)
 */
function addIpoToGcal(type, name, dateStart, dateEnd, broker, priceIpo) {
  const isSubType = type === 'sub';
  const title     = encodeURIComponent(isSubType ? `[청약] ${name}` : `[상장] ${name}`);
  const startDate = _gcDate(dateStart);
  // 청약: 기간(start~end+1), 상장: 당일만
  const endDate   = isSubType ? _gcNextDay(dateEnd) : _gcNextDay(dateStart);
  const details   = encodeURIComponent([
    isSubType ? `📋 ${name} 공모주 청약` : `🚀 ${name} 코스피/코스닥 상장`,
    broker    ? `증권사: ${broker}` : '',
    priceIpo  ? `공모가: ${Number(priceIpo).toLocaleString()}원` : '',
  ].filter(Boolean).join('\n'));

  const url = `https://calendar.google.com/calendar/render?action=TEMPLATE`
    + `&text=${title}`
    + `&dates=${startDate}/${endDate}`
    + `&details=${details}`;
  window.open(url, '_blank');
}

/** 예정 IPO 전체를 .ics 파일로 다운로드 */
function exportIpoIcs() {
  const items = _ipoListItems.filter(ipo =>
    ['청약예정','청약중','상장예정'].includes(ipo.status || '')
  );
  if (!items.length) { alert('내보낼 예정 일정이 없습니다.'); return; }

  const pad = n => String(n).padStart(2, '0');
  const icsDate = s => (s || '').replace(/-/g, '');
  const nextDay = s => {
    if (!s) return '';
    const d = new Date(s + 'T00:00:00');
    d.setDate(d.getDate() + 1);
    return `${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}`;
  };
  const esc = s => (s || '').replace(/,/g, '\\,').replace(/\n/g, '\\n');
  const uid = () => Math.random().toString(36).slice(2) + '@stock-analyzer';

  const events = [];
  items.forEach(ipo => {
    // 청약 기간 이벤트
    if (ipo.date_sub_start && ipo.date_sub_end && ['청약예정','청약중'].includes(ipo.status)) {
      events.push([
        'BEGIN:VEVENT',
        `UID:sub-${ipo.name}-${uid()}`,
        `DTSTART;VALUE=DATE:${icsDate(ipo.date_sub_start)}`,
        `DTEND;VALUE=DATE:${nextDay(ipo.date_sub_end)}`,
        `SUMMARY:[청약] ${esc(ipo.name)}`,
        `DESCRIPTION:${esc([ipo.broker, ipo.price_ipo ? '공모가 '+Number(ipo.price_ipo).toLocaleString()+'원' : ''].filter(Boolean).join(' | '))}`,
        'END:VEVENT',
      ].join('\r\n'));
    }
    // 상장일 이벤트
    if (ipo.date_list && ipo.status === '상장예정') {
      events.push([
        'BEGIN:VEVENT',
        `UID:list-${ipo.name}-${uid()}`,
        `DTSTART;VALUE=DATE:${icsDate(ipo.date_list)}`,
        `DTEND;VALUE=DATE:${nextDay(ipo.date_list)}`,
        `SUMMARY:[상장] ${esc(ipo.name)}`,
        `DESCRIPTION:${esc(ipo.broker || '')}`,
        'END:VEVENT',
      ].join('\r\n'));
    }
  });

  const ics = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Stock Analyzer//IPO Calendar//KO',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    ...events,
    'END:VCALENDAR',
  ].join('\r\n');

  const blob = new Blob([ics], { type: 'text/calendar;charset=utf-8' });
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(blob);
  a.download = 'ipo_calendar.ics';
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── 공모주 청약 현황 ─────────────────────────────────────
let _ipoListItems = [], _ipoExpanded = false, _ipoLastSync = null;

function renderIpoList(items) { _ipoListItems = items; _renderIpoList(); }

function _ipoSyncWarningHtml() {
  const sync = _ipoLastSync;
  const today = new Date().toISOString().slice(0, 10);

  // last_sync 필드가 있을 때: 명시적 오류 상태
  if (sync) {
    if (!sync.ok) {
      return `<div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:8px 12px;margin-bottom:8px;font-size:12px;color:#b91c1c">
        ⚠️ 공모주 데이터 업데이트 오류 발생 중 (${sync.date || '날짜 미상'}) — 정보가 정확하지 않을 수 있습니다
      </div>`;
    }
    const daysDiff = sync.date ? Math.floor((new Date(today) - new Date(sync.date)) / 86400000) : 99;
    if (daysDiff >= 5) {
      return `<div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:8px 12px;margin-bottom:8px;font-size:12px;color:#92400e">
        ⚠️ 공모주 정보가 ${daysDiff}일째 업데이트되지 않았습니다 — 데이터가 최신이 아닐 수 있습니다
      </div>`;
    }
    return '';
  }

  // last_sync 없을 때: fetched_at 기준 폴백
  const dates = _ipoListItems.map(r => r.fetched_at).filter(Boolean).sort();
  const lastFetch = dates[dates.length - 1];
  if (!lastFetch) return '';
  const daysDiff = Math.floor((new Date(today) - new Date(lastFetch)) / 86400000);
  if (daysDiff >= 5) {
    return `<div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:8px 12px;margin-bottom:8px;font-size:12px;color:#92400e">
      ⚠️ 공모주 정보가 ${daysDiff}일째 업데이트되지 않았습니다 — 데이터가 최신이 아닐 수 있습니다
    </div>`;
  }
  return '';
}

function _renderIpoList() {
  const el = document.getElementById('list-ipo');
  const items = _ipoListItems;
  const STATUS_EMOJI = { '청약예정':'🔵', '청약중':'🟡', '상장예정':'🟠', '상장완료':'🟢', '청약포기':'⚫', '배정실패':'❌' };

  if (!items.length) { el.innerHTML = '<div class="empty-msg">등록된 공모주가 없습니다</div>'; return; }

  const today = new Date().toISOString().slice(0, 10);

  // 일정 기준일 추출 (정렬용)
  function _ipoDate(ipo) {
    if (ipo.status === '청약중')   return ipo.date_sub_end   || '9999';
    if (ipo.status === '청약예정') return ipo.date_sub_start || '9999';
    if (ipo.status === '상장예정') return ipo.date_list      || '9999';
    return null;
  }

  // 금일 이후 가장 가까운 일정 (청약중·청약예정·상장예정)
  const upcoming = items
    .filter(ipo => { const d = _ipoDate(ipo); return d && d >= today; })
    .sort((a, b) => (_ipoDate(a) || '').localeCompare(_ipoDate(b) || ''));

  // 전체 보기용: 진행중 → 상장완료 순
  const order = ['청약중','청약예정','상장예정','상장완료','청약포기','배정실패'];
  const allSorted = [...items].sort((a,b) => order.indexOf(a.status||'') - order.indexOf(b.status||''));

  const toShow = _ipoExpanded ? allSorted : upcoming.slice(0, 1);
  const hasMore = upcoming.length > 1 || items.some(ipo => !_ipoDate(ipo) || _ipoDate(ipo) < today);

  const moreBtn = hasMore ? `
    <div style="text-align:center;padding:6px 0">
      <button onclick="_ipoExpanded=!_ipoExpanded;_renderIpoList()"
        style="background:none;border:1px solid var(--border);border-radius:8px;padding:5px 18px;font-size:12px;color:var(--muted);cursor:pointer">
        ${_ipoExpanded ? '▲ 접기' : '▼ 전체보기'}
      </button>
    </div>` : '';

  el.innerHTML = _ipoSyncWarningHtml() + (toShow.length ? toShow : allSorted.slice(0, 3)).map(ipo => {
    const sub = ipo.subscribed;
    const status = ipo.status || '';
    const emoji = STATUS_EMOJI[status] || '⚪';
    const subBadge = sub
      ? `<span style="background:#a7f3d0;color:#064e3b;font-size:11px;padding:2px 8px;border-radius:20px;font-weight:700">✅ 청약완료</span>`
      : ((['청약예정','청약중'].includes(status))
          ? `<span style="background:#fef3c7;color:#92400e;font-size:11px;padding:2px 8px;border-radius:20px">⏳ 청약대기</span>`
          : '');
    const hasAlloc = ipo.shares_alloc && ipo.shares_alloc > 0;
    const listingBadge = sub && hasAlloc && ipo.date_list
      ? `<span style="background:#fce7f3;color:#9d174d;font-size:11px;padding:2px 8px;border-radius:20px;font-weight:700">🚀 상장: ${ipo.date_list} (${ipo.shares_alloc}주)</span>`
      : '';

    // 구글 캘린더 버튼 (청약예정·청약중·상장예정만 표시)
    const calBtns = [];
    if (ipo.date_sub_start && ipo.date_sub_end && ['청약예정','청약중'].includes(status)) {
      calBtns.push(`<button onclick="event.stopPropagation();addIpoToGcal('sub','${ipo.name}','${ipo.date_sub_start}','${ipo.date_sub_end}','${ipo.broker||''}',${ipo.price_ipo||0})"
        style="background:#4285F4;color:#fff;border:none;border-radius:6px;padding:3px 9px;font-size:11px;cursor:pointer;white-space:nowrap">
        📅 청약일 추가
      </button>`);
    }
    if (ipo.date_list && ['상장예정','청약중','청약완료'].includes(status) || (sub && ipo.date_list)) {
      calBtns.push(`<button onclick="event.stopPropagation();addIpoToGcal('list','${ipo.name}','${ipo.date_list}','${ipo.date_list}','${ipo.broker||''}',${ipo.price_ipo||0})"
        style="background:#34A853;color:#fff;border:none;border-radius:6px;padding:3px 9px;font-size:11px;cursor:pointer;white-space:nowrap">
        🚀 상장일 추가
      </button>`);
    }

    return `<div class="result-item" style="${sub ? 'border-color:#a7f3d0' : ''}">
      <div class="result-item-header">
        <span style="font-weight:700">${emoji} ${ipo.name}</span>
        <span style="font-size:11px;color:var(--muted)">${ipo.broker || ''}</span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;align-items:center">
        <span style="font-size:12px;color:var(--muted)">📅 ${ipo.date_sub_start||''} ~ ${ipo.date_sub_end||''}</span>
        ${subBadge} ${listingBadge}
      </div>
      ${ipo.price_ipo ? `<div style="font-size:11px;color:var(--muted);margin-top:4px">공모가 ${ipo.price_ipo.toLocaleString()}원${ipo.competition_inst ? ` | 기관경쟁률 ${ipo.competition_inst.toLocaleString()}:1` : ''}</div>` : ''}
      ${calBtns.length ? `<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">${calBtns.join('')}</div>` : ''}
    </div>`;
  }).join('') + moreBtn;
}

// ── 매매 현황 (stock_trader 거래 내역 + 계좌 잔액) ────────
function renderTraderSummary(trades, account) {
  const wrap = document.getElementById('trader-summary-wrap');
  if (!wrap) return;

  const c  = v => v >= 0 ? '#16a34a' : '#dc2626';
  const sg = v => v >= 0 ? '+' : '';

  // ── 계좌 잔액 섹션 ──────────────────────────────────────
  let accountHtml = '';
  if (account) {
    const updLabel = account.updated_at
      ? `<span style="font-size:11px;color:var(--muted);margin-left:6px">${account.updated_at} 기준</span>` : '';
    const dayPnlColor = (account.day_pnl || 0) >= 0 ? '#16a34a' : '#dc2626';
    const dayRetColor = (account.day_ret || 0) >= 0 ? '#16a34a' : '#dc2626';

    // 계좌 요약 카드
    accountHtml += `
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:10px">
        <span style="font-size:12px;font-weight:700">🏦 한국투자증권 계좌</span>${updLabel}
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:16px">
        <div style="background:var(--surface2,#1e2d45);border-radius:10px;padding:12px;text-align:center">
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">총 평가금액</div>
          <div style="font-size:16px;font-weight:700;color:#ffffff">${Number(account.total_eval).toLocaleString()}<span style="font-size:11px;color:var(--muted)">원</span></div>
        </div>
        <div style="background:var(--surface2,#1e2d45);border-radius:10px;padding:12px;text-align:center">
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">예수금 (현금)</div>
          <div style="font-size:16px;font-weight:700;color:#ffffff">${Number(account.cash).toLocaleString()}<span style="font-size:11px;color:var(--muted)">원</span></div>
        </div>
        <div style="background:var(--surface2,#1e2d45);border-radius:10px;padding:12px;text-align:center">
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">당일 손익</div>
          <div style="font-size:16px;font-weight:700;color:${dayPnlColor}">${sg(account.day_pnl)}${Number(account.day_pnl||0).toLocaleString()}<span style="font-size:11px">원</span></div>
        </div>
        <div style="background:var(--surface2,#1e2d45);border-radius:10px;padding:12px;text-align:center">
          <div style="font-size:11px;color:var(--muted);margin-bottom:4px">당일 수익률</div>
          <div style="font-size:20px;font-weight:700;color:${dayRetColor}">${sg(account.day_ret||0)}${account.day_ret ?? '—'}<span style="font-size:13px;color:var(--muted)">%</span></div>
        </div>
      </div>`;

    // 보유 종목 (KIS 실시간)
    if (account.holdings && account.holdings.length) {
      const hrows = account.holdings.map(h => {
        const up = h.pnl_pct >= 0;
        const unrealized = (h.eval_price - h.avg_price) * h.qty;
        return `<tr>
          <td style="font-weight:600;white-space:nowrap">${h.name}<br>
            <span style="font-size:11px;color:var(--muted)">${h.ticker}</span></td>
          <td style="text-align:right">${Number(h.qty).toLocaleString()}주</td>
          <td style="text-align:right">${Number(h.avg_price).toLocaleString()}원</td>
          <td style="text-align:right">${Number(h.eval_price).toLocaleString()}원</td>
          <td style="text-align:right;font-weight:600">${Number(h.eval_amt).toLocaleString()}원</td>
          <td style="text-align:right;font-weight:700;color:${up?'#16a34a':'#dc2626'}">
            ${sg(unrealized)}${Math.round(unrealized).toLocaleString()}원<br>
            <span style="font-size:11px">${sg(h.pnl_pct)}${h.pnl_pct}%</span>
          </td>
        </tr>`;
      }).join('');
      accountHtml += `
        <div style="font-size:12px;font-weight:700;color:var(--fg);margin-bottom:6px">📌 현재 보유 종목 (KIS 기준)</div>
        <div style="overflow-x:auto;margin-bottom:20px">
          <table class="inv-table">
            <thead><tr>
              <th style="text-align:left">종목</th>
              <th style="text-align:right">수량</th>
              <th style="text-align:right">평균단가</th>
              <th style="text-align:right">현재가</th>
              <th style="text-align:right">평가금액</th>
              <th style="text-align:right">평가손익</th>
            </tr></thead>
            <tbody>${hrows}</tbody>
          </table>
        </div>`;
    }
    accountHtml += `<hr style="border:none;border-top:1px solid var(--border);margin:0 0 16px">`;
  }

  if (!trades.length) {
    wrap.innerHTML = accountHtml + '<div class="empty-msg">거래 내역이 없습니다</div>';
    return;
  }

  // ── 종결된 거래 손익 집계 ──────────────────────────────
  const closed = trades.filter(t => t.type === 'sell' && t.pnl != null);
  const totalPnl  = closed.reduce((s, t) => s + Number(t.pnl), 0);
  const wins      = closed.filter(t => t.pnl > 0).length;
  const winRate   = closed.length ? Math.round(wins / closed.length * 100) : null;
  const avgPnlPct = closed.length
    ? (closed.reduce((s, t) => s + Number(t.pnl_pct || 0), 0) / closed.length).toFixed(2)
    : null;

  // ── 거래 통계 카드 ─────────────────────────────────────
  const statsHtml = `
    <div style="font-size:12px;font-weight:700;color:var(--fg);margin-bottom:8px">📊 시스템 트레이딩 통계</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:16px">
      <div style="background:var(--surface2,#1e2d45);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">총 거래</div>
        <div style="font-size:22px;font-weight:700;color:#ffffff">${closed.length}<span style="font-size:13px;color:var(--muted)">건</span></div>
      </div>
      <div style="background:var(--surface2,#1e2d45);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">누적 실현손익</div>
        <div style="font-size:15px;font-weight:700;color:${c(totalPnl)}">${sg(totalPnl)}${Math.round(totalPnl).toLocaleString()}<span style="font-size:11px">원</span></div>
      </div>
      <div style="background:var(--surface2,#1e2d45);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">승률</div>
        <div style="font-size:22px;font-weight:700;color:${winRate != null ? c(winRate - 50) : 'var(--fg)'}">${winRate ?? '—'}<span style="font-size:13px;color:var(--muted)">%</span></div>
      </div>
      <div style="background:var(--surface2,#1e2d45);border-radius:10px;padding:12px;text-align:center">
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px">평균 수익률</div>
        <div style="font-size:20px;font-weight:700;color:${avgPnlPct != null ? c(Number(avgPnlPct)) : 'var(--fg)'}">${avgPnlPct != null ? sg(Number(avgPnlPct)) + avgPnlPct : '—'}<span style="font-size:13px;color:var(--muted)">%</span></div>
      </div>
    </div>`;

  // ── 최근 매도 내역 (10건) ──────────────────────────────
  const recentClosed = [...closed]
    .sort((a, b) => (b.date + b.time).localeCompare(a.date + a.time))
    .slice(0, 10);
  let closedHtml = '';
  if (recentClosed.length) {
    const rows = recentClosed.map(t => {
      const up   = Number(t.pnl) >= 0;
      const sign = up ? '+' : '';
      const reasonLabel = t.reason
        ? `<span style="font-size:10px;color:var(--muted);margin-left:4px">[${t.reason}]</span>` : '';
      return `<tr>
        <td style="font-weight:600;white-space:nowrap">${t.name || t.ticker}<br>
          <span style="font-size:11px;color:var(--muted)">${t.ticker}</span>${reasonLabel}</td>
        <td style="text-align:right">${Number(t.qty).toLocaleString()}주</td>
        <td style="text-align:right">${Number(t.price).toLocaleString()}원</td>
        <td style="text-align:right;font-weight:600">${Number(t.amount).toLocaleString()}원</td>
        <td style="text-align:right;font-weight:700;color:${up?'#16a34a':'#dc2626'}">${sign}${Math.round(t.pnl).toLocaleString()}원<br>
          <span style="font-size:11px">${sign}${t.pnl_pct}%</span></td>
        <td style="text-align:right;font-size:11px;color:var(--muted)">${t.date}</td>
      </tr>`;
    }).join('');
    closedHtml = `
      <div style="font-size:12px;font-weight:700;color:var(--fg);margin-bottom:6px">🔁 최근 매도 내역</div>
      <div style="overflow-x:auto">
        <table class="inv-table">
          <thead><tr>
            <th style="text-align:left">종목</th>
            <th style="text-align:right">수량</th>
            <th style="text-align:right">매도단가</th>
            <th style="text-align:right">매도금액</th>
            <th style="text-align:right">손익</th>
            <th style="text-align:right">날짜</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  wrap.innerHTML = accountHtml + statsHtml + closedHtml;
}

// ── 보유 종목 투자자 현황 ─────────────────────────────────
let _investorCache = null, _investorLoading = false;


async function loadInvestorStatus(force = false) {
  if (_investorLoading) return;
  if (_investorCache && !force) { renderInvestorTable(_investorCache); return; }
  _investorLoading = true;
  document.getElementById('investor-table-wrap').innerHTML =
    '<div class="empty-msg">데이터 로드 중...</div>';
  try {
    const r = await fetch('/api/investor');
    _investorCache = r.ok ? await r.json() : { items: [], date: '' };
  } catch(e) {
    _investorCache = { items: [], date: '' };
  }
  _investorLoading = false;
  renderInvestorTable(_investorCache);
}

function renderInvestorTable(data) {
  const wrap  = document.getElementById('investor-table-wrap');
  const label = document.getElementById('investor-date-label');
  const items = data.items || [];
  const d  = data.date        || '';
  const pd = data.pensionDate || '';
  if (d.length === 8)
    label.textContent = `전일 종가 기준 / ${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}`;
  if (!items.length) {
    wrap.innerHTML = '<div class="empty-msg">보유 종목 데이터가 없습니다. 포트폴리오 탭에서 종목을 추가하세요.</div>';
    return;
  }
  // 만 단위 절사: ±10,000 미만은 —, 이상은 +X.X만 형식
  const fmtNet = v => {
    if (v == null) return '<span style="color:var(--muted)">—</span>';
    if (Math.abs(v) < 10000) return '<span style="color:var(--muted)">—</span>';
    const c = v > 0 ? 'var(--green)' : 'var(--red)';
    const man = (v / 10000).toFixed(1);
    return `<span style="color:${c};font-weight:600">${v>0?'+':''}${man}만</span>`;
  };
  const rows = items.map(it => {
    const safeN = (it.name||'').replace(/'/g,"\\'");
    return `<tr style="cursor:pointer" onclick="openStockDetail('${it.ticker}','${safeN}')">
      <td style="white-space:nowrap;min-width:80px">
        <span style="font-weight:600;font-size:13px">${it.name||''}</span><br>
        <span style="font-size:11px;color:var(--muted)">${it.ticker}</span>
      </td>
      <td style="text-align:right;white-space:nowrap">${(it.foreign_rate||0).toFixed(1)}%</td>
      <td style="text-align:right;white-space:nowrap">${fmtNet(it.foreign_net)}</td>
      <td style="text-align:right;white-space:nowrap">${fmtNet(it.inst_net)}</td>
      <td style="text-align:right;white-space:nowrap">${fmtNet(it.indiv_net)}</td>
    </tr>`;
  }).join('');
  const pensionNote = pd.length === 8
    ? `${pd.slice(0,4)}-${pd.slice(4,6)}-${pd.slice(6,8)} 기준 (GitHub Actions 수집)`
    : '수집 중';
  wrap.innerHTML = `
    <div style="overflow-x:auto;-webkit-overflow-scrolling:touch">
      <table class="inv-table">
        <thead>
          <tr>
            <th style="text-align:left">종목</th>
            <th style="text-align:right">외국인%</th>
            <th style="text-align:right">외국인</th>
            <th style="text-align:right">기관</th>
            <th style="text-align:right">개인</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div style="font-size:11px;color:var(--muted);margin-top:8px;padding:0 4px">
      ※ 전일 종가 기준 · 순매수 ±1만주 미만 생략
    </div>`;
}

// ── 종목 상세 모달 ─────────────────────────────────────────
let _detailTicker = '', _detailData = null, _detailTab = 'investor';

async function openStockDetail(ticker, name) {
  _detailTicker = ticker;
  _detailTab    = 'investor';
  _detailData   = null;
  document.getElementById('detail-name').textContent        = name;
  document.getElementById('detail-ticker-label').textContent = ticker;
  document.getElementById('detail-body').innerHTML = '<div class="empty-msg">로딩 중...</div>';
  document.querySelectorAll('.dtab').forEach(b => b.classList.toggle('active', b.dataset.tab === _detailTab));
  document.getElementById('stock-detail-overlay').classList.add('open');
  document.body.style.overflow = 'hidden';
  try {
    const r = await fetch('/api/investor?ticker=' + ticker);
    _detailData = r.ok ? await r.json() : null;
  } catch(_) { _detailData = null; }
  _renderDetailContent();
}

function closeStockDetail() {
  document.getElementById('stock-detail-overlay').classList.remove('open');
  document.body.style.overflow = '';
}

function switchDetailTab(tab) {
  _detailTab = tab;
  document.querySelectorAll('.dtab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  _renderDetailContent();
}

function _renderDetailContent() {
  const body = document.getElementById('detail-body');
  if (!_detailData) { body.innerHTML = '<div class="empty-msg">데이터를 불러올 수 없습니다.</div>'; return; }
  if (_detailTab === 'investor') body.innerHTML = _renderInvestorTab(_detailData);
  else if (_detailTab === 'price')   body.innerHTML = _renderPriceTab(_detailData);
  else if (_detailTab === 'finance') body.innerHTML = _renderFinanceTab(_detailData);
}

function _fmtC(v) {
  if (v == null) return '<span style="color:var(--muted)">—</span>';
  if (v === 0)   return '<span style="color:var(--muted)">0</span>';
  const c = v > 0 ? 'var(--green)' : 'var(--red)';
  return '<span style="color:' + c + ';font-weight:600">' + (v>0?'+':'') + v.toLocaleString() + '</span>';
}

function _renderInvestorTab(data) {
  const trend   = data.investorTrend || [];
  const pension = data.pensionTrend  || [];
  if (!trend.length) return '<div class="empty-msg">투자자 데이터가 없습니다.</div>';
  const pMap = {};
  pension.forEach(function(p) { pMap[p.date] = p.pension_net; });
  const chartData = trend.slice().reverse().slice(-20);
  const chartHtml = _svgBarChart(chartData, [
    { key:'foreign_net', color:'#6366f1', label:'외국인' },
    { key:'inst_net',    color:'#f59e0b', label:'기관'   }
  ], { height:150 });
  const rows = trend.map(function(r) {
    const pnet = pMap[r.date] != null ? pMap[r.date] : null;
    return '<tr>'
      + '<td style="white-space:nowrap">' + r.date.slice(0,4) + '-' + r.date.slice(4,6) + '-' + r.date.slice(6,8) + '</td>'
      + '<td style="text-align:right;white-space:nowrap">' + (r.close ? r.close.toLocaleString() + '원' : '—') + '</td>'
      + '<td style="text-align:right;white-space:nowrap">' + _fmtC(r.foreign_net) + '</td>'
      + '<td style="text-align:right;white-space:nowrap">' + _fmtC(r.inst_net) + '</td>'
      + '<td style="text-align:right;white-space:nowrap">' + _fmtC(pnet) + '</td>'
      + '<td style="text-align:right;white-space:nowrap">' + (r.foreign_rate||0).toFixed(1) + '%</td>'
      + '</tr>';
  }).join('');
  return chartHtml
    + '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:14px">'
    + '<table class="inv-table"><thead><tr>'
    + '<th style="text-align:left">날짜</th><th style="text-align:right">종가</th>'
    + '<th style="text-align:right">외국인</th><th style="text-align:right">기관</th>'
    + '<th style="text-align:right">연기금</th><th style="text-align:right">외국인 지분율</th>'
    + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
}

function _renderPriceTab(data) {
  const price = data.priceData || [];
  if (!price.length) return '<div class="empty-msg">주가 데이터가 없습니다.</div>';
  return '<div style="margin-bottom:16px">'
    + '<div style="font-size:12px;color:var(--muted);margin-bottom:4px;font-weight:600">종가 추이 (최근 30거래일)</div>'
    + _svgLineChart(price, 'close', '#6366f1', '종가(원)', {height:140})
    + '</div><div>'
    + '<div style="font-size:12px;color:var(--muted);margin-bottom:4px;font-weight:600">외국인 소진율 (%)</div>'
    + _svgLineChart(price, 'foreign_rate', '#f59e0b', '외국인 소진율', {height:100})
    + '</div>';
}

function _renderFinanceTab(data) {
  const f = data.fundamental || {};
  const r = f.ratios || {};
  const a = f.annual  || [];
  const mets = [
    { label:'PER',      val: r.per      ? r.per + '배'                           : '—' },
    { label:'PBR',      val: r.pbr      ? r.pbr + '배'                           : '—' },
    { label:'EPS',      val: r.eps      ? parseInt(r.eps).toLocaleString() + '원' : '—' },
    { label:'BPS',      val: r.bps      ? parseInt(r.bps).toLocaleString() + '원' : '—' },
    { label:'배당수익률', val: r.div_yield ? r.div_yield + '%'                      : '—' }
  ];
  const metricsHtml = '<div class="detail-metrics">'
    + mets.map(function(m) {
        return '<div class="dm"><div class="dm-label">' + m.label + '</div><div class="dm-val">' + m.val + '</div></div>';
      }).join('')
    + '</div>';
  if (!a.length) return metricsHtml + '<div class="empty-msg" style="margin-top:12px">연간 재무 데이터가 없습니다.</div>';
  const revData = a.filter(function(y) { return y.revenue; }).map(function(y) {
    return { date: y.year, close: parseFloat((y.revenue||'').replace(/,/g,'')) || 0 };
  });
  const revenueChart = revData.length >= 2
    ? '<div style="margin:14px 0 4px;font-size:12px;color:var(--muted);font-weight:600">연간 매출액 추이</div>'
      + _svgLineChart(revData, 'close', '#10b981', '매출액(억)', {height:110})
    : '';
  const COLS = [
    {label:'매출액', k:'revenue'}, {label:'영업이익', k:'op_income'}, {label:'순이익', k:'net_income'},
    {label:'영업이익률', k:'op_margin'}, {label:'ROE', k:'roe'},
    {label:'EPS', k:'eps'}, {label:'PER', k:'per'}, {label:'PBR', k:'pbr'}, {label:'주당배당금', k:'div'}
  ];
  const thead = '<tr><th style="text-align:left">항목</th>'
    + a.map(function(y) { return '<th style="text-align:right;white-space:nowrap">' + y.year + '</th>'; }).join('')
    + '</tr>';
  const tbody = COLS.map(function(col) {
    return '<tr><td style="font-weight:600;white-space:nowrap">' + col.label + '</td>'
      + a.map(function(y) { return '<td style="text-align:right;white-space:nowrap">' + (y[col.k]||'—') + '</td>'; }).join('')
      + '</tr>';
  }).join('');
  return metricsHtml + revenueChart
    + '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:14px">'
    + '<table class="detail-annual-table"><thead>' + thead + '</thead><tbody>' + tbody + '</tbody></table></div>';
}

// ── SVG 차트 헬퍼 ──────────────────────────────────────────
function _svgLineChart(data, key, color, label, opts) {
  opts = opts || {};
  var W=560, H=opts.height||140, Pl=46, Pr=12, Pt=14, Pb=28;
  var vals = data.map(function(d) { return +(d[key]) || 0; });
  var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
  var rng = mx - mn || 1;
  var xs = function(i) { return Pl + i*(W-Pl-Pr)/Math.max(data.length-1,1); };
  var ys = function(v) { return Pt + (1-(v-mn)/rng)*(H-Pt-Pb); };
  var pts = data.map(function(d,i) { return xs(i).toFixed(1)+','+ys(+(d[key])||0).toFixed(1); }).join(' ');
  var bot = H-Pb;
  var area = xs(0).toFixed(1)+','+bot+' '+pts+' '+xs(data.length-1).toFixed(1)+','+bot;
  var uid = key + '_' + Math.random().toString(36).slice(2,6);
  var yTicks = [mn, mn+rng/2, mx];
  var yLbls = yTicks.map(function(v) {
    var lbl = Math.abs(v)>=10000 ? (v/1000).toFixed(0)+'K' : v.toFixed(Math.abs(v)%1?1:0);
    return '<text x="'+(Pl-4)+'" y="'+ys(v).toFixed(1)+'" text-anchor="end" font-size="9" fill="var(--muted)" dominant-baseline="middle">'+lbl+'</text>';
  }).join('');
  var step = Math.max(1, Math.ceil(data.length/5));
  var xLbls = data.filter(function(_,i) { return i%step===0 || i===data.length-1; }).map(function(d) {
    var i=data.indexOf(d), ds=String(d.date||'');
    var lbl = ds.length===8 ? ds.slice(4,6)+'/'+ds.slice(6,8) : ds.slice(-5);
    return '<text x="'+xs(i).toFixed(1)+'" y="'+(bot+14)+'" text-anchor="middle" font-size="9" fill="var(--muted)">'+lbl+'</text>';
  }).join('');
  return '<div style="overflow-x:auto"><svg viewBox="0 0 '+W+' '+H+'" style="width:100%;min-width:260px;display:block">'
    + '<defs><linearGradient id="'+uid+'" x1="0" y1="0" x2="0" y2="1">'
    + '<stop offset="0%" stop-color="'+color+'" stop-opacity="0.25"/>'
    + '<stop offset="100%" stop-color="'+color+'" stop-opacity="0.02"/>'
    + '</linearGradient></defs>'
    + '<polygon points="'+area+'" fill="url(#'+uid+')"/>'
    + '<polyline points="'+pts+'" fill="none" stroke="'+color+'" stroke-width="1.8" stroke-linejoin="round"/>'
    + yLbls + xLbls
    + '<text x="'+Pl+'" y="'+Pt+'" font-size="10" fill="'+color+'">'+label+'</text>'
    + '</svg></div>';
}

function _svgBarChart(data, series, opts) {
  opts = opts || {};
  var W=560, H=opts.height||140, Pl=10, Pr=12, Pt=24, Pb=28;
  var allV = [];
  data.forEach(function(d) { series.forEach(function(s) { allV.push(d[s.key]||0); }); });
  var mx = Math.max.apply(null, allV.map(Math.abs).concat([1]));
  var innerW = W-Pl-Pr;
  var grpW = innerW/Math.max(data.length,1);
  var barW = grpW/series.length*0.82;
  var zero = Pt+(H-Pt-Pb)/2;
  var bars = '';
  data.forEach(function(d,di) {
    series.forEach(function(s,si) {
      var v=d[s.key]||0;
      var bh=Math.min(Math.abs(v)/mx*(H-Pt-Pb)/2, (H-Pt-Pb)/2);
      var x=Pl+di*grpW+si*barW+(grpW-series.length*barW)/2;
      var y=v>=0?zero-bh:zero;
      bars += '<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+barW.toFixed(1)+'" height="'+Math.max(bh,0).toFixed(1)+'" fill="'+s.color+'" opacity="0.85" rx="1.5"/>';
    });
  });
  var step = Math.max(1, Math.ceil(data.length/6));
  var xLbls = data.filter(function(_,i) { return i%step===0 || i===data.length-1; }).map(function(d) {
    var i=data.indexOf(d), ds=String(d.date||'');
    var lbl=ds.length===8?ds.slice(4,6)+'/'+ds.slice(6,8):ds.slice(-5);
    return '<text x="'+(Pl+i*grpW+grpW/2).toFixed(1)+'" y="'+(H-Pb+13)+'" text-anchor="middle" font-size="9" fill="var(--muted)">'+lbl+'</text>';
  }).join('');
  var legend = series.map(function(s,i) {
    return '<text x="'+(Pl+i*64)+'" y="13" font-size="10" fill="'+s.color+'">■ '+s.label+'</text>';
  }).join('');
  return '<div style="overflow-x:auto"><svg viewBox="0 0 '+W+' '+H+'" style="width:100%;min-width:260px;display:block">'
    + '<line x1="'+Pl+'" y1="'+zero.toFixed(1)+'" x2="'+(W-Pr)+'" y2="'+zero.toFixed(1)+'" stroke="var(--border)" stroke-width="1"/>'
    + bars + xLbls + legend
    + '</svg></div>';
}

// ══════════════════════════════════════════════════════════
// 캘린더
// ══════════════════════════════════════════════════════════
let _calYear  = new Date().getFullYear();
let _calMonth = new Date().getMonth();

function calMove(dir) {
  // 팝업이 열려 있으면 먼저 닫고 월 이동 (오버레이가 클릭을 가로채는 문제 방지)
  document.getElementById('day-modal-overlay').classList.remove('open');
  _calMonth += dir;
  if (_calMonth > 11) { _calMonth = 0; _calYear++; }
  if (_calMonth < 0)  { _calMonth = 11; _calYear--; }
  if (_dashData) renderCalendar(_dashData); else renderCalendar({ ipo: _ipoRecords });
}

// 영업일 계산 공통 함수 (calcAllotDate / calcAllotDateIpo / calcEstListDate 통합)
// ── 한국 공휴일 (주말 제외, 별도 isHoliday에서 주말 병행 체크) ──
const KR_HOLIDAYS = new Set([
  // 2025
  '2025-01-01',                                     // 신정
  '2025-01-28','2025-01-29','2025-01-30',           // 설날 연휴 (설날=1/29)
  '2025-03-01',                                     // 삼일절
  '2025-05-05','2025-05-06',                        // 어린이날 + 대체(석가탄신일 겹침)
  '2025-06-06',                                     // 현충일
  '2025-08-15',                                     // 광복절
  '2025-10-03',                                     // 개천절
  '2025-10-05','2025-10-06','2025-10-07','2025-10-08', // 추석 연휴 + 대체
  '2025-10-09',                                     // 한글날
  '2025-12-25',                                     // 성탄절
  // 2026
  '2026-01-01',                                     // 신정
  '2026-02-16','2026-02-17','2026-02-18',           // 설날 연휴 (설날=2/17)
  '2026-03-01','2026-03-02',                        // 삼일절(일) + 대체
  '2026-05-05',                                     // 어린이날
  '2026-05-24',                                     // 부처님오신날
  '2026-06-06','2026-06-08',                        // 현충일(토) + 대체
  '2026-08-15','2026-08-17',                        // 광복절(토) + 대체
  '2026-09-24','2026-09-25','2026-09-26','2026-09-28', // 추석 연휴 + 대체
  '2026-10-03','2026-10-05',                        // 개천절(토) + 대체
  '2026-10-09',                                     // 한글날
  '2026-12-25',                                     // 성탄절
]);

function isHoliday(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  const dow = d.getDay();
  return dow === 0 || dow === 6 || KR_HOLIDAYS.has(dateStr);
}

function addBizDays(dateStr, n) {
  if (!dateStr) return '';
  try {
    let d = new Date(dateStr + 'T00:00:00');
    let bdays = 0;
    while (bdays < n) {
      d.setDate(d.getDate() + 1);
      const key = d.toISOString().slice(0, 10);
      if (!isHoliday(key)) bdays++;
    }
    return d.toISOString().slice(0, 10);
  } catch(_) { return ''; }
}

function renderCalendar(data) {
  const DOWS = ['SUN','MON','TUE','WED','THU','FRI','SAT'];
  const today = new Date();
  const y = _calYear, m = _calMonth;
  const monthNames = ['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월'];

  document.getElementById('cal-title').textContent = `${y}년 ${monthNames[m]}`;

  // 이벤트 인덱스 (날짜 → 배열)
  const events = {};
  const add = (date, type, text, detail = null) => {
    if (!date) return;
    if (!events[date]) events[date] = [];
    events[date].push({ type, text, detail });
  };

  (data.briefing || []).forEach(b => add(b.date, 'briefing', `📊 브리핑 (${(b.stocks||[]).length}종목)`, b));
  (data.signals  || []).forEach(s => add(s.date, 'signal',   `📡 ${s.name} ${s.alerts?.[0]?.substring(0,10)||''}`, s));

  // 공모주 청약일 / 상장일 (로컬 시간 기준 날짜 파싱)
  (data.ipo || []).forEach(ipo => {
    const name = ipo.name || '';
    const subscribed = ipo.subscribed;
    const status = ipo.status || '';

    // 청약 기간 (시작~마감 중 영업일만 표시 — 주말·공휴일 제외)
    if (ipo.date_sub_start && ipo.date_sub_end) {
      let cur = new Date(ipo.date_sub_start + 'T00:00:00');
      const end = new Date(ipo.date_sub_end + 'T00:00:00');
      while (cur <= end) {
        const cy = cur.getFullYear();
        const cm = String(cur.getMonth()+1).padStart(2,'0');
        const cd = String(cur.getDate()).padStart(2,'0');
        const key = `${cy}-${cm}-${cd}`;
        if (!isHoliday(key)) { // 주말·공휴일 제외
          if (subscribed) {
            add(key, 'ipo-sub-done', `✅ ${name} 청약완료`, ipo);
          } else if (!['상장완료','청약포기'].includes(status)) {
            const sc = ipo.score;
            const star = sc >= 70 ? '⭐' : sc >= 50 ? '🔹' : '';
            const scoreTag = sc != null ? ` ${star}${sc}점` : '';
            add(key, 'ipo-sub', `🏢 ${name}${scoreTag}`, ipo);
          }
        }
        cur.setDate(cur.getDate() + 1);
      }
    }

    // 배정일 — 청약완료 시에만 표시 (step2)
    // 배정주수 미입력 OR 배정은 됐지만 상장일이 아직 없는 경우에도 표시
    if (ipo.subscribed && ipo.date_sub_end && !['상장완료','청약포기','배정실패'].includes(status)) {
      const allotDate = ipo.date_allot || addBizDays(ipo.date_sub_end, 2);
      const hasAlloc  = ipo.shares_alloc && ipo.shares_alloc > 0;
      const needsListDate = hasAlloc && !ipo.date_list;
      if (!hasAlloc || needsListDate) {
        const label = needsListDate ? `📬 ${name} 배정완료 (상장일 미입력)` : `📬 ${name} 배정확인`;
        // 상장일 미입력 상태면 오늘 날짜에 표시해 현재 월 캘린더에서 바로 찾을 수 있게 함
        const eventDate = needsListDate ? new Date().toISOString().slice(0, 10) : allotDate;
        if (eventDate) add(eventDate, 'ipo-allot', label, ipo);
      }
    }

    // 상장일 — 배정주수가 있는 경우에만 표시
    if (ipo.date_list && (ipo.shares_alloc > 0) && !['청약포기','배정실패'].includes(status)) {
      add(ipo.date_list, 'ipo-list', `🚀 ${name} 상장 (${ipo.shares_alloc}주)`, ipo);
    }
  });

  // 스케줄 (영업일 고정 이벤트 — 주말·공휴일 제외)
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  for (let d = 1; d <= daysInMonth; d++) {
    const key = `${y}-${String(m+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    if (!isHoliday(key)) { // 주말·공휴일 제외
      if (!events[key]) events[key] = [];
      events[key].unshift({ type: 'briefing', text: '⏰ 07:30 브리핑', detail: null });
    }
  }

  // 전역 이벤트 저장 (클릭 상세보기용)
  _calEvents = events;

  // 그리기
  const grid = document.getElementById('cal-grid');
  const firstDay = new Date(y, m, 1).getDay();
  const lastDate = new Date(y, m + 1, 0).getDate();
  const prevLast = new Date(y, m, 0).getDate();

  let html = DOWS.map(d => `<div class="cal-dow">${d}</div>`).join('');

  // 이전달 빈 칸
  for (let i = firstDay - 1; i >= 0; i--) {
    html += `<div class="cal-cell other-month"><div class="cal-day">${prevLast - i}</div></div>`;
  }

  for (let d = 1; d <= lastDate; d++) {
    const dt = new Date(y, m, d);
    const dow = dt.getDay();
    const key = `${y}-${String(m+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const isToday = y === today.getFullYear() && m === today.getMonth() && d === today.getDate();
    const evs = events[key] || [];
    const hasReal = evs.some(e => !e.text.startsWith('⏰'));
    // 캘린더 셀에는 공모주 이벤트만 표시 (브리핑/추천/신호는 클릭 시 모달에서만 확인)
    const cellEvs = evs.filter(e => e.type.startsWith('ipo'));

    const shortLabel = t => {
      const m = t.match(/^([\p{Emoji}]\s?)(.+)/u);
      if (!m) return t.slice(0, 5);
      return m[1] + m[2].replace(/ .*/, '').slice(0, 4);
    };
    html += `<div class="cal-cell${isToday ? ' today' : ''}${hasReal ? ' has-event' : ''}" onclick="showDayDetail('${key}')">
      <div class="cal-day${dow===0?' sun':dow===6?' sat':''}">${d}</div>
      ${cellEvs.slice(0,3).map(e => `<div class="cal-event ${e.type}" title="${e.text}"><span class="cal-ev-full">${e.text}</span><span class="cal-ev-short">${shortLabel(e.text)}</span></div>`).join('')}
      ${cellEvs.length > 3 ? `<div style="font-size:9px;color:var(--muted)">+${cellEvs.length-3}</div>` : ''}
    </div>`;
  }

  // 다음달 빈 칸
  const total = firstDay + lastDate;
  const remain = (7 - (total % 7)) % 7;
  for (let d = 1; d <= remain; d++) {
    html += `<div class="cal-cell other-month"><div class="cal-day">${d}</div></div>`;
  }

  grid.innerHTML = html;
}

// ══════════════════════════════════════════════════════════
// 날짜 상세 모달
// ══════════════════════════════════════════════════════════
let _calEvents = {};

function showDayDetail(key) {
  const allEvs = _calEvents[key] || [];
  if (!allEvs.length) return;
  const evs = allEvs.filter(e => !e.text.startsWith('⏰'));
  const schedEvs = allEvs.filter(e => e.text.startsWith('⏰'));

  const [y, mo, d] = key.split('-');
  document.getElementById('day-modal-title').textContent =
    `${y}년 ${parseInt(mo)}월 ${parseInt(d)}일`;

  let html = '';

  // 브리핑
  const briefings = evs.filter(e => e.type === 'briefing' && e.detail);
  if (briefings.length) {
    html += `<div class="day-modal-section"><div class="day-modal-section-title">📊 브리핑</div>`;
    briefings.forEach(ev => {
      const b = ev.detail;
      html += `<div class="day-modal-ev">`;
      (b.stocks || []).forEach(s => {
        const cc = (s.chg_pct||0) >= 0 ? 'var(--green)' : 'var(--red)';
        const ar = (s.chg_pct||0) >= 0 ? '▲' : '▼';
        html += `<div style="display:flex;align-items:center;justify-content:space-between;
          padding:4px 0;border-bottom:1px solid var(--border)">
          <span style="font-weight:600">${s.name||s.code||''}</span>
          <span>${(s.price||0).toLocaleString()}원
            <span style="color:${cc}">${ar}${Math.abs(s.chg_pct||0).toFixed(2)}%</span>
          </span></div>`;
        (s.alerts||[]).forEach(a => {
          html += `<div style="font-size:11px;color:var(--muted);padding-left:8px">• ${a}</div>`;
        });
      });
      if (!(b.stocks||[]).length) html += `<div style="color:var(--muted)">종목 데이터 없음</div>`;
      html += `</div>`;
    });
    html += `</div>`;
  }

  // 신호 알림
  const signals = evs.filter(e => e.type === 'signal' && e.detail);
  if (signals.length) {
    html += `<div class="day-modal-section"><div class="day-modal-section-title">📡 신호 알림</div>`;
    signals.forEach(ev => {
      const s = ev.detail;
      const cc = (s.chg_pct||0) >= 0 ? 'var(--green)' : 'var(--red)';
      const ar = (s.chg_pct||0) >= 0 ? '▲' : '▼';
      html += `<div class="day-modal-ev">
        <div style="font-weight:600;margin-bottom:4px">${s.name||''}</div>
        ${(s.alerts||[]).map(a=>`<div style="font-size:12px">• ${a}</div>`).join('')}
        <div style="font-size:11px;color:var(--muted);margin-top:4px">
          ${(s.price||0).toLocaleString()}원
          <span style="color:${cc}">${ar}${Math.abs(s.chg_pct||0).toFixed(2)}%</span>
        </div>
      </div>`;
    });
    html += `</div>`;
  }

  // 공모주
  const ipoEvs = evs.filter(e => e.type.startsWith('ipo') && e.detail);
  if (ipoEvs.length) {
    html += `<div class="day-modal-section"><div class="day-modal-section-title">🏢 공모주</div>`;
    const typeLabel = {
      'ipo-sub':'청약기간','ipo-sub-done':'청약완료',
      'ipo-allot':'배정확인일','ipo-list':'상장일'
    };
    ipoEvs.forEach(ev => {
      const ipo = ev.detail;
      const _sellQty  = ipo.sell_qty || ipo.shares_alloc || 0;
      const ipoProfit = (ipo.price_open && ipo.price_ipo && _sellQty > 0)
        ? (ipo.price_open - ipo.price_ipo) * _sellQty - 2000 : null;
      const ipoRetPct = (ipo.price_open && ipo.price_ipo && ipo.shares_alloc > 0)
        ? ((ipo.price_open - ipo.price_ipo) / ipo.price_ipo * 100) : null;
      html += `<div class="day-modal-ev">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <span style="font-weight:700">${ipo.name||''}</span>
          <span class="cal-event ${ev.type}" style="display:inline-block">${typeLabel[ev.type]||ev.type}</span>
        </div>
        <div style="font-size:12px;color:var(--muted)">
          공모가: <strong>${(ipo.price_ipo||0).toLocaleString()}원</strong>
          ${ipo.date_sub_start ? ` · 청약: ${ipo.date_sub_start}~${ipo.date_sub_end||''}` : ''}
          ${ipo.date_list ? ` · 상장: ${ipo.date_list}` : ''}
          ${ipo.broker ? ` · ${ipo.broker}` : ''}
        </div>
        ${(ipo.shares_alloc||0) > 0
          ? `<div style="font-size:12px;margin-top:4px;color:var(--green);font-weight:600">✅ 배정: ${ipo.shares_alloc}주</div>` : ''}

        ${ev.type === 'ipo-sub' ? `
        <div style="margin-top:12px;padding:12px;background:#1e2d45;border-radius:8px">
          <div style="font-size:12px;color:#94a3b8;margin-bottom:10px">이 공모주에 청약하셨나요?</div>
          <button onclick="markSubscribed(${ipo.id})"
            style="width:100%;background:#3b82f6;color:#fff;border:none;border-radius:6px;padding:10px;font-size:14px;font-weight:700;cursor:pointer">
            ✅ 청약했어요 → 배정일 캘린더에 추가
          </button>
        </div>` : ''}

        ${ev.type === 'ipo-allot' ? `
        <div style="margin-top:12px;padding:12px;background:#1e2d45;border-radius:8px">
          <div style="font-size:12px;color:#fde68a;font-weight:600;margin-bottom:10px">📬 배정 결과 입력 (step 3)</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
            <div>
              <label style="font-size:11px;color:#94a3b8;display:block;margin-bottom:4px">배정주수 (0 = 배정실패)</label>
              <input type="number" id="allot-shares-${ipo.id}" min="0" value="${ipo.shares_alloc||0}"
                style="width:100%;padding:8px;background:#111827;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:14px;box-sizing:border-box">
            </div>
            <div>
              <label style="font-size:11px;color:#94a3b8;display:block;margin-bottom:4px">상장일 <span style="color:#64748b">(모르면 추정값)</span></label>
              <input type="date" id="allot-listdate-${ipo.id}" value="${ipo.date_list || addBizDays(ipo.date_sub_end, 5)}"
                style="width:100%;padding:8px;background:#111827;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:13px;box-sizing:border-box">
            </div>
          </div>
          <div style="display:grid;grid-template-columns:1fr auto;gap:8px">
            <button onclick="saveAllotFromModal(${ipo.id})"
              style="background:#f59e0b;color:#000;border:none;border-radius:6px;padding:10px;font-size:14px;font-weight:700;cursor:pointer">
              ✅ 저장 → 상장일 캘린더 등록
            </button>
            <button onclick="cancelAllot(${ipo.id})" title="배정 입력 취소 (배정전 상태로 되돌림)"
              style="background:#374151;color:#9ca3af;border:none;border-radius:6px;padding:10px;font-size:13px;cursor:pointer">
              ↩ 취소
            </button>
          </div>
        </div>` : ''}

        ${ev.type === 'ipo-list' ? `
        <div style="margin-top:12px;padding:12px;background:#0d2010;border-radius:8px;border:1px solid #166534">
          <div style="font-size:12px;color:#86efac;font-weight:600;margin-bottom:10px">💰 매도 결과 입력</div>
          <div style="display:grid;grid-template-columns:1fr 70px;gap:8px;margin-bottom:8px">
            <div>
              <label style="font-size:11px;color:#94a3b8;display:block;margin-bottom:4px">매도가 (원)</label>
              <input type="number" id="list-price-${ipo.id}" min="0" value="${ipo.price_open||''}" placeholder="매도한 가격"
                style="width:100%;padding:8px;background:#111827;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:14px;box-sizing:border-box">
            </div>
            <div>
              <label style="font-size:11px;color:#94a3b8;display:block;margin-bottom:4px">수량</label>
              <input type="number" id="list-qty-${ipo.id}" min="0" value="${ipo.sell_qty||ipo.shares_alloc||''}"
                style="width:100%;padding:8px;background:#111827;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:14px;box-sizing:border-box;text-align:center">
            </div>
          </div>
          <div style="margin-bottom:8px">
            <label style="font-size:11px;color:#94a3b8;display:block;margin-bottom:4px">매도일</label>
            <input type="date" id="list-date-${ipo.id}" value="${ipo.sell_date||ipo.date_list||''}"
              style="width:100%;padding:8px;background:#111827;border:1px solid #334155;border-radius:6px;color:#f1f5f9;font-size:13px;box-sizing:border-box">
          </div>
          ${ipoProfit !== null ? `
          <div style="padding:8px;background:#111827;border-radius:6px;margin-bottom:8px;font-size:13px">
            순수익 (수수료 2,000원 차감):
            <strong style="color:${ipoProfit >= 0 ? '#34d399':'#f87171'}">${ipoProfit >= 0 ? '+' : ''}${ipoProfit.toLocaleString()}원</strong>
            ${ipoRetPct !== null ? `<span style="color:${ipoProfit >= 0 ? '#34d399':'#f87171'};margin-left:8px">(${ipoRetPct >= 0 ? '+' : ''}${ipoRetPct.toFixed(1)}%)</span>` : ''}
          </div>` : ''}
          <div style="display:grid;grid-template-columns:1fr auto;gap:8px">
            <button onclick="saveListPriceFromModal(${ipo.id})"
              style="background:#16a34a;color:#fff;border:none;border-radius:6px;padding:10px;font-size:14px;font-weight:700;cursor:pointer">
              💾 매도 기록 저장
            </button>
            <button onclick="removeListDate(${ipo.id})" title="상장일 등록 삭제 (배정 완료 상태로 되돌림)"
              style="background:#374151;color:#9ca3af;border:none;border-radius:6px;padding:10px;font-size:13px;cursor:pointer">
              🗑 상장일 삭제
            </button>
          </div>
          <div style="font-size:10px;color:#475569;margin-top:6px;text-align:center">투자금 = 배정수량 × 공모가 · 수수료 2,000원 자동 차감</div>
        </div>` : ''}

        ${ipo.score != null ? (() => {
          const sc = ipo.score;
          const color = sc >= 70 ? 'var(--green)' : sc >= 50 ? '#f59e0b' : 'var(--muted)';
          const rec = ipo.recommendation || '';
          const sd = ipo.score_detail || {};
          return `<div style="margin-top:8px;padding:8px;background:#fff;border-radius:8px;border:1px solid var(--border)">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
              <span style="font-weight:700;color:${color};font-size:15px">${sc}점</span>
              <span style="font-size:12px">${rec}</span>
            </div>
            ${Object.keys(sd).length ? `<div style="font-size:11px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap">
              ${sd.inst_comp != null ? `<span>기관경쟁률 ${sd.inst_comp}점</span>` : ''}
              ${sd.lock_up != null ? `<span>의무확약 ${sd.lock_up}점</span>` : ''}
              ${sd.band != null ? `<span>밴드위치 ${sd.band}점</span>` : ''}
              ${sd.premium != null ? `<span>수익기대 ${sd.premium}점</span>` : ''}
            </div>` : ''}
          </div>`;
        })() : ''}
      </div>`;
    });
    html += `</div>`;
  }

  if (!html) {
    if (schedEvs.length) {
      html = `<div class="day-modal-section">
        <div class="day-modal-section-title">📅 예정 스케줄</div>
        ${schedEvs.map(e => `<div class="day-modal-ev" style="display:flex;align-items:center;gap:8px">
          <span style="font-size:18px">⏰</span>
          <div>
            <div style="font-weight:600">${e.text.replace('⏰ ','')}</div>
            <div style="font-size:12px;color:var(--muted)">GitHub Actions 실행 후 실제 데이터가 표시됩니다</div>
          </div>
        </div>`).join('')}
      </div>`;
    } else {
      html = `<div class="day-modal-ev" style="color:var(--muted);text-align:center">상세 데이터가 없습니다</div>`;
    }
  }

  document.getElementById('day-modal-body').innerHTML = html;
  document.getElementById('day-modal-overlay').classList.add('open');
}

function closeModal(evt) {
  if (evt && evt.target !== document.getElementById('day-modal-overlay')) return;
  document.getElementById('day-modal-overlay').classList.remove('open');
}
// ══════════════════════════════════════════════════════════
// 포트폴리오 대시보드
// ══════════════════════════════════════════════════════════
let _portAutoTimer  = null;
let _portGeneration = 0;   // initPortfolio() 호출마다 증가 — 구 refresh 결과 무시에 사용
let _stockRecords  = [];
let _portEtf       = [];
let _portIpo       = [];
let _portDiv       = [];
let _portCash      = 0;
let _stockMdDown   = null;

async function saveCash() {
  const raw = (document.getElementById('cash-input')?.value || '').replace(/,/g, '');
  const cash = parseInt(raw, 10) || 0;
  try {
    const r = await fetch('/api/data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cash }),
    });
    if (!(await r.json()).ok && !r.ok) throw new Error('저장 실패');
    _portCash = cash;
    // 입력란 포맷 갱신
    const ci = document.getElementById('cash-input');
    if (ci) ci.value = cash ? cash.toLocaleString() : '';
    // 저장 확인 메시지
    const msg = document.getElementById('cash-save-msg');
    if (msg) { msg.style.display = 'inline'; setTimeout(() => msg.style.display = 'none', 2000); }
    // 차트 즉시 갱신
    _renderPortAsset();
    _renderPortKpi();
  } catch(e) { alert('예수금 저장 실패: ' + e.message); }
}
const PORT_REFRESH_MS = 20 * 60 * 1000; // 20분

// ══════════════════════════════════════════════════════════
// 포트폴리오 섹션 접기/펼치기
// ══════════════════════════════════════════════════════════

function togglePortSection(key) {
  const body   = document.getElementById(`port-${key}-body`);
  const toggle = document.getElementById(`port-${key}-toggle`);
  if (!body || !toggle) return;
  const isCollapsed = body.classList.toggle('collapsed');
  toggle.classList.toggle('collapsed', isCollapsed);
  // 'ipo'는 항상 접힘 시작이므로 localStorage 저장 안 함
  if (key !== 'ipo') {
    try { localStorage.setItem(`port-collapse-${key}`, isCollapsed ? '1' : '0'); } catch(_) {}
  }
}

function _initPortCollapse() {
  // etf-div: localStorage 상태 복원
  try {
    if (localStorage.getItem('port-collapse-etf-div') === '1') {
      document.getElementById('port-etf-div-body')?.classList.add('collapsed');
      document.getElementById('port-etf-div-toggle')?.classList.add('collapsed');
    }
  } catch(_) {}
  // ipo: 항상 접힘 (localStorage 무시)
  document.getElementById('port-ipo-body')?.classList.add('collapsed');
  document.getElementById('port-ipo-toggle')?.classList.add('collapsed');
}

// ══════════════════════════════════════════════════════════
// 포트폴리오 (ETF Gist + IPO Gist + 개별주 Gist 기반)
// ══════════════════════════════════════════════════════════

function _showPortLoading() {
  const loading = '<tr><td colspan="4" class="port-loading">로딩 중...</td></tr>';
  const el = id => document.getElementById(id);
  ['pk-stk-eval','pk-etf-eval','pk-ipo-profit','pk-div-total'].forEach(id => { const e = el(id); if (e) e.textContent = '—'; });
  if (el('port-etf-tbody'))   el('port-etf-tbody').innerHTML   = loading;
  if (el('port-stock-tbody')) el('port-stock-tbody').innerHTML = loading;
  if (el('port-ipo-tbody'))   el('port-ipo-tbody').innerHTML   = '';
  if (el('port-ipo-card'))    el('port-ipo-card').style.display = 'none';
  if (el('port-asset-wrap'))  el('port-asset-wrap').innerHTML  = '<div class="port-loading" style="padding:20px;text-align:center">로딩 중...</div>';
  if (el('port-donut-wrap'))  el('port-donut-wrap').innerHTML  = '<div class="port-loading" style="padding:20px;text-align:center">로딩 중...</div>';
  if (el('port-etf-div-chart')) el('port-etf-div-chart').innerHTML = '<div class="port-loading" style="padding:20px">로딩 중...</div>';
  if (el('cash-input')) el('cash-input').value = '';
}

