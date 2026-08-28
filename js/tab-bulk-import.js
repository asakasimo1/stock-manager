// ══════════════════════════════════════════════════════════
// 거래내역 일괄 가져오기 (ETF+개별주 공용) — 2026-08-28
// KB증권 등 증권사 화면/엑셀에서 복사해온 표를 붙여넣어 과거 거래를
// 한 번에 등록. 정확한 열 구성을 알 수 없어(로그인 필요) 자동 감지 +
// 사용자가 직접 열을 매핑하는 방식으로 구현 — 어떤 증권사 포맷이든 대응.
//
// ⚠️ 설계상 중요: 이 기능은 거래 기록(history)만 저장하고, 종목의 현재
// 보유수량·평균매수가·예수금은 건드리지 않는다. 단건 입력 모달
// (saveTransaction/saveStkTransaction)과 달리 여러 건을 한 번에 넣다보면
// 파싱 오류나 누락으로 현재값이 조용히 망가질 위험이 커서, 과거 기록
// 보관 용도로만 쓰고 현재 수치는 사용자가 각 종목에서 직접 확인/수정하게
// 분리함.
// ══════════════════════════════════════════════════════════

let _bulkTxDown;
let _bulkRows    = [];  // 파싱된 2차원 배열 (헤더 제외)
let _bulkMapping = [];  // 열 index → 'ignore'|'date'|'name'|'ticker'|'type'|'qty'|'price'|'note'
let _bulkParsed  = [];  // 검증 완료된 행 (매칭 결과 + 오류 포함)

const _BULK_FIELD_OPTIONS = [
  ['ignore', '사용 안 함'],
  ['date',   '거래일'],
  ['name',   '종목명'],
  ['ticker', '종목코드'],
  ['type',   '매매구분'],
  ['qty',    '수량'],
  ['price',  '단가'],
  ['note',   '메모'],
];

const _BULK_HEADER_WORDS = ['일자','날짜','종목','구분','수량','단가','가격','금액','거래','매매','코드','메모','비고'];
const _BULK_FIELD_GUESS = [
  { key: 'date',   words: ['일자', '날짜', '체결일'] },
  { key: 'name',   words: ['종목명', '상품명', '종목'] },
  { key: 'ticker', words: ['종목코드', '단축코드', '코드', '티커'] },
  { key: 'type',   words: ['매매구분', '거래구분', '매매유형', '구분'] },
  { key: 'qty',    words: ['체결수량', '거래수량', '주식수', '수량'] },
  { key: 'price',  words: ['체결단가', '거래단가', '단가', '가격'] },
  { key: 'note',   words: ['메모', '비고', '적요'] },
];

function openBulkImportModal() {
  _bulkRows = []; _bulkMapping = []; _bulkParsed = [];
  document.getElementById('bulk-tx-modal').style.display = 'block';
  _bulkRenderStep1();
}

function closeBulkImportModal() {
  document.getElementById('bulk-tx-modal').style.display = 'none';
}

function _bulkRenderStep1() {
  const el = document.getElementById('bulk-tx-body');
  el.innerHTML = `
    <div style="font-size:12px;color:var(--muted);line-height:1.7;margin-bottom:12px">
      증권사 앱/HTS의 <b style="color:var(--text)">거래내역조회</b> 화면에서 표를 드래그해 복사(Ctrl+C)하거나,
      엑셀로 내려받은 표를 복사해서 아래에 붙여넣으세요(Ctrl+V). 첫 줄이 헤더(종목명·수량 등)여도 자동으로 인식합니다.<br>
      <b style="color:var(--text)">⚠️ 거래 기록만 저장됩니다.</b> 현재 보유수량·평균매수가·예수금은 바뀌지 않으니,
      최신 상태는 각 종목 카드에서 직접 확인해주세요.
    </div>
    <textarea id="bulk-tx-paste" rows="10" placeholder="예)
2026-01-15	삼성전자	매수	10	72000
2026-02-03	KODEX 200	매도	5	38500"
      style="width:100%;box-sizing:border-box;padding:12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:12px;font-family:monospace;resize:vertical"></textarea>
    <div style="display:flex;justify-content:flex-end;margin-top:12px">
      <button onclick="_bulkParseText()" style="background:var(--primary);color:#fff;border:none;border-radius:8px;padding:10px 20px;font-size:13px;font-weight:700;cursor:pointer">다음 →</button>
    </div>`;
}

function _bulkDetectDelim(lines) {
  const sample = lines.slice(0, 5);
  if (sample.some(l => l.includes('\t'))) return '\t';
  const commaCounts = sample.map(l => (l.match(/,/g) || []).length);
  if (commaCounts[0] > 0 && commaCounts.every(c => c === commaCounts[0])) return ',';
  return /\s{2,}/;
}

function _bulkSplitLine(line, delim) {
  const cells = delim instanceof RegExp ? line.trim().split(delim) : line.split(delim);
  return cells.map(c => c.trim());
}

function _bulkLooksLikeHeader(cells) {
  return cells.some(c => _BULK_HEADER_WORDS.some(w => c.includes(w)));
}

function _bulkGuessField(headerCell) {
  if (!headerCell) return 'ignore';
  for (const f of _BULK_FIELD_GUESS) {
    if (f.words.some(w => headerCell.includes(w))) return f.key;
  }
  return 'ignore';
}

function _bulkParseText() {
  const text = document.getElementById('bulk-tx-paste').value;
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (!lines.length) { alert('붙여넣은 내용이 없습니다'); return; }

  const delim = _bulkDetectDelim(lines);
  let rows = lines.map(l => _bulkSplitLine(l, delim));

  // 가장 흔한 열 개수를 기준으로, 깨진(다른 열 개수) 줄은 제외
  const freq = {};
  rows.forEach(r => { freq[r.length] = (freq[r.length] || 0) + 1; });
  const commonLen = Number(Object.entries(freq).sort((a, b) => b[1] - a[1])[0][0]);
  rows = rows.filter(r => r.length === commonLen);
  if (!rows.length) { alert('열 구성을 인식하지 못했습니다. 표 형태 그대로 다시 붙여넣어보세요.'); return; }

  let header = null;
  if (rows.length > 1 && _bulkLooksLikeHeader(rows[0])) {
    header = rows[0];
    rows = rows.slice(1);
  }

  _bulkRows    = rows;
  _bulkMapping = Array.from({ length: commonLen }, (_, i) => _bulkGuessField(header ? header[i] : ''));
  _bulkRenderStep2();
}

function _bulkRenderStep2() {
  const el = document.getElementById('bulk-tx-body');
  const previewRows = _bulkRows.slice(0, 6);

  const headCells = _bulkMapping.map((cur, i) => `
    <th style="padding:4px 6px">
      <select onchange="_bulkMapping[${i}]=this.value" style="width:100%;font-size:11px;padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
        ${_BULK_FIELD_OPTIONS.map(([k, label]) => `<option value="${k}" ${cur === k ? 'selected' : ''}>${label}</option>`).join('')}
      </select>
    </th>`).join('');

  const bodyRows = previewRows.map(r => `<tr>${r.map(c =>
    `<td style="padding:5px 6px;font-size:11px;color:var(--muted);border-top:1px solid var(--border);white-space:nowrap">${c || '-'}</td>`
  ).join('')}</tr>`).join('');

  el.innerHTML = `
    <div style="font-size:12px;color:var(--muted);margin-bottom:10px">
      총 <b style="color:var(--text)">${_bulkRows.length}행</b> 인식됨. 각 열이 어떤 항목인지 선택하세요
      (<b style="color:var(--text)">거래일·종목명 또는 종목코드·매매구분·수량</b>은 필수, 단가·메모는 선택).
    </div>
    <div style="overflow-x:auto;border:1px solid var(--border);border-radius:8px">
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>${headCells}</tr></thead>
        <tbody>${bodyRows}</tbody>
      </table>
    </div>
    <div style="display:flex;justify-content:space-between;margin-top:14px">
      <button onclick="_bulkRenderStep1()" style="background:none;color:var(--muted);border:1px solid var(--border);border-radius:8px;padding:10px 20px;font-size:13px;cursor:pointer">← 다시 붙여넣기</button>
      <button onclick="_bulkBuildPreview()" style="background:var(--primary);color:#fff;border:none;border-radius:8px;padding:10px 20px;font-size:13px;font-weight:700;cursor:pointer">미리보기 →</button>
    </div>`;
}

function _bulkNormalizeDate(raw) {
  if (!raw) return null;
  const m = String(raw).trim().match(/(\d{4})[.\-/]?\s*(\d{1,2})[.\-/]?\s*(\d{1,2})/);
  if (!m) return null;
  const date = `${m[1]}-${String(m[2]).padStart(2, '0')}-${String(m[3]).padStart(2, '0')}`;
  return isNaN(new Date(date + 'T00:00:00').getTime()) ? null : date;
}

function _bulkNormalizeType(raw) {
  if (!raw) return null;
  const s = String(raw).trim();
  if (/매수|buy|입고|매입/i.test(s)) return 'buy';
  if (/매도|sell|출고|매각/i.test(s)) return 'sell';
  return null;
}

function _bulkParseNum(raw) {
  if (raw == null || raw === '') return null;
  const s = String(raw).replace(/[,원주\s]/g, '');
  if (!s || isNaN(Number(s))) return null;
  return Number(s);
}

function _bulkNormalizeTicker(raw) {
  if (!raw) return '';
  let s = String(raw).trim();
  if (/^\d+$/.test(s) && s.length < 6) s = s.padStart(6, '0'); // 엑셀에서 숫자로 인식돼 앞 0이 잘린 경우 보정
  return s;
}

// 등록된 ETF/개별주 레코드 중에서 종목코드 → 종목명 순으로 매칭
function _bulkMatchStock(nameRaw, tickerRaw) {
  const name   = (nameRaw || '').trim();
  const ticker = _bulkNormalizeTicker(tickerRaw);
  const all = [
    ...(typeof _etfRecords !== 'undefined' ? _etfRecords.map(r => ({ ...r, kind: 'etf' })) : []),
    ...(typeof _stockRecords !== 'undefined' ? _stockRecords.map(r => ({ ...r, kind: 'stock' })) : []),
  ];
  if (ticker) {
    const hit = all.find(r => r.ticker && _bulkNormalizeTicker(r.ticker) === ticker);
    if (hit) return hit;
  }
  if (name) {
    let hit = all.find(r => r.name && r.name === name);
    if (hit) return hit;
    hit = all.find(r => r.name && (r.name.includes(name) || name.includes(r.name)));
    if (hit) return hit;
  }
  return null;
}

function _bulkBuildPreview() {
  const dateIdx  = _bulkMapping.indexOf('date');
  const nameIdx  = _bulkMapping.indexOf('name');
  const tickIdx  = _bulkMapping.indexOf('ticker');
  const typeIdx  = _bulkMapping.indexOf('type');
  const qtyIdx   = _bulkMapping.indexOf('qty');
  const priceIdx = _bulkMapping.indexOf('price');
  const noteIdx  = _bulkMapping.indexOf('note');

  if (dateIdx < 0 || typeIdx < 0 || qtyIdx < 0 || (nameIdx < 0 && tickIdx < 0)) {
    alert('거래일 · 매매구분 · 수량 · (종목명 또는 종목코드)는 반드시 선택해야 합니다');
    return;
  }

  _bulkParsed = _bulkRows.map(r => {
    const date  = _bulkNormalizeDate(r[dateIdx]);
    const type  = _bulkNormalizeType(r[typeIdx]);
    const qty   = _bulkParseNum(r[qtyIdx]);
    const price = priceIdx >= 0 ? _bulkParseNum(r[priceIdx]) : null;
    const note  = noteIdx >= 0 ? (r[noteIdx] || '').trim() : '';
    const match = _bulkMatchStock(nameIdx >= 0 ? r[nameIdx] : '', tickIdx >= 0 ? r[tickIdx] : '');

    let error = null;
    if (!date) error = '거래일 인식 실패';
    else if (!type) error = '매매구분 인식 실패(매수/매도만 가능)';
    else if (!qty) error = '수량 인식 실패';
    else if (!match) error = '미등록 종목 (ETF/개별주 탭에서 먼저 등록하세요)';

    return {
      date, type, qty, price, note,
      matchId:    match?.id   ?? null,
      matchKind:  match?.kind ?? null,
      matchLabel: match ? `${match.name}${match.ticker ? ' (' + match.ticker + ')' : ''}` : (r[nameIdx >= 0 ? nameIdx : tickIdx] || '-'),
      error,
    };
  });

  _bulkRenderStep3();
}

function _bulkRenderStep3() {
  const el = document.getElementById('bulk-tx-body');
  const valid  = _bulkParsed.filter(p => !p.error);
  const errors = _bulkParsed.filter(p => p.error);

  const rows = _bulkParsed.map(p => `
    <tr style="${p.error ? 'opacity:.55' : ''}">
      <td style="padding:5px 6px;font-size:11px">${p.error ? '❌' : '✅'}</td>
      <td style="padding:5px 6px;font-size:11px;white-space:nowrap">${p.date || '-'}</td>
      <td style="padding:5px 6px;font-size:11px;white-space:nowrap">${p.matchLabel}</td>
      <td style="padding:5px 6px;font-size:11px">${p.type === 'buy' ? '매수' : p.type === 'sell' ? '매도' : '-'}</td>
      <td style="padding:5px 6px;font-size:11px;text-align:right">${p.qty ?? '-'}</td>
      <td style="padding:5px 6px;font-size:11px;text-align:right">${p.price != null ? p.price.toLocaleString() : '-'}</td>
      <td style="padding:5px 6px;font-size:11px;color:var(--red)">${p.error || ''}</td>
    </tr>`).join('');

  el.innerHTML = `
    <div style="font-size:13px;margin-bottom:10px">
      <b style="color:var(--green)">${valid.length}건</b> 가져오기 가능
      ${errors.length ? ` · <b style="color:var(--red)">${errors.length}건</b> 오류(제외됨)` : ''}
    </div>
    <div style="overflow-x:auto;border:1px solid var(--border);border-radius:8px;max-height:340px;overflow-y:auto">
      <table style="width:100%;border-collapse:collapse">
        <thead style="position:sticky;top:0;background:var(--surface)">
          <tr style="color:var(--muted);font-size:11px;text-align:left">
            <th style="padding:5px 6px">상태</th><th style="padding:5px 6px">거래일</th><th style="padding:5px 6px">종목</th>
            <th style="padding:5px 6px">구분</th><th style="padding:5px 6px">수량</th><th style="padding:5px 6px">단가</th><th style="padding:5px 6px">사유</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-top:14px">
      <button onclick="_bulkRenderStep2()" style="background:none;color:var(--muted);border:1px solid var(--border);border-radius:8px;padding:10px 20px;font-size:13px;cursor:pointer">← 열 다시 선택</button>
      <button id="bulk-save-btn" onclick="saveBulkImport()" ${valid.length ? '' : 'disabled'}
        style="background:var(--primary);color:#fff;border:none;border-radius:8px;padding:10px 20px;font-size:13px;font-weight:700;cursor:pointer;opacity:${valid.length ? 1 : .5}">
        ${valid.length}건 가져오기
      </button>
    </div>`;
}

async function saveBulkImport() {
  const valid = _bulkParsed.filter(p => !p.error);
  if (!valid.length) return;

  const btn = document.getElementById('bulk-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = '저장 중...'; }

  const records = valid.map((p, i) => {
    const isEtf = p.matchKind === 'etf';
    const src   = isEtf
      ? _etfRecords.find(r => r.id == p.matchId)
      : _stockRecords.find(r => r.id == p.matchId);
    return {
      id: Date.now() + i,
      ...(isEtf ? { etf_id: p.matchId } : { stock_id: p.matchId }),
      ticker: src?.ticker || '',
      name:   src?.name   || '',
      date: p.date, type: p.type, qty_change: p.qty, price: p.price || null,
      note: p.note || '일괄 가져오기',
    };
  });

  try {
    const resp = await fetch('/api/transactions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ records }),
    });
    if (!resp.ok) throw new Error('저장 실패');

    // 화면 즉시 갱신 (보유수량·평균매수가·예수금은 건드리지 않고 거래 목록만 반영)
    records.forEach(r => {
      if (r.etf_id != null) { if (typeof _transactions !== 'undefined') _transactions.push(r); }
      else { if (typeof _stkTransactions !== 'undefined') _stkTransactions.push(r); }
    });
    if (typeof renderEtfCards === 'function') renderEtfCards();
    if (typeof renderStockCards === 'function') renderStockCards();

    closeBulkImportModal();
    alert(`✅ ${records.length}건의 거래내역을 가져왔습니다.\n(보유수량·평균매수가·예수금은 변경되지 않았습니다 — 필요하면 각 종목에서 직접 확인해주세요)`);
  } catch (e) {
    alert('가져오기 실패: ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = `${valid.length}건 가져오기`; }
  }
}
