#!/usr/bin/env node
/**
 * IPO 크롤링 스크립트 (GitHub Actions용)
 * 38.co.kr → 파싱 → Gist + JSONBin 업데이트
 */
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36';
const TODAY = () => new Date().toISOString().slice(0, 10);
const GIST_ID     = process.env.GIST_ID;
const GH_TOKEN    = process.env.GH_TOKEN;
const JSONBIN_ID  = process.env.JSONBIN_BIN_ID;
const JSONBIN_KEY = process.env.JSONBIN_KEY;

// ── EUC-KR fetch ─────────────────────────────────────────────
async function fetchEucKr(url) {
  const res = await fetch(url, {
    headers: { 'User-Agent': UA, 'Accept-Language': 'ko-KR,ko;q=0.9' },
    signal: AbortSignal.timeout(20000),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} from ${url}`);
  const buf = await res.arrayBuffer();
  return new TextDecoder('euc-kr').decode(buf);
}

function stripHtml(html) {
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&#\d+;/g, '')
    .replace(/[ \t]+/g, ' ');
}

// ── 점수 계산 ────────────────────────────────────────────────
function calcScore(name, inst, lock, price, low, high) {
  const sInst = inst == null ? 0 : inst < 100 ? 5 : inst < 500 ? 15 : inst < 1000 ? 25 : inst < 2000 ? 35 : 40;
  const sLock = lock == null ? 0 : lock < 5 ? 5 : lock < 10 ? 15 : lock < 20 ? 25 : lock < 40 ? 35 : 40;
  const sBand = (!price || !low || !high) ? 5 :
    price > high ? 10 :
    high === low ? (price >= high ? 10 : 5) :
    ((price - low) / (high - low)) >= 1.0 ? 10 :
    ((price - low) / (high - low)) >= 0.5 ? 7 : 3;
  const sPrem = name.includes('스팩') ? 0 : inst == null ? 5 : inst >= 1000 ? 10 : inst >= 500 ? 7 : 3;
  return sInst + sLock + sBand + sPrem;
}
function recommend(name, score) {
  if (name.includes('스팩')) return '스팩주: 원금보장형, 수익 낮음';
  if (score >= 90) return '⭐⭐⭐ 적극 청약 추천';
  if (score >= 70) return '⭐⭐ 청약 추천';
  if (score >= 50) return '⭐ 청약 고려';
  return '⚪ 청약 보류';
}

// ── 날짜 유틸 ────────────────────────────────────────────────
function parseDate(year, mmdd) {
  const [m, d] = mmdd.split('.');
  return `${year}-${m.padStart(2,'0')}-${d.padStart(2,'0')}`;
}
function calcStatus(start, end) {
  const today = TODAY();
  if (!start) return '청약예정';
  if (end < today) return '청약완료';
  if (start <= today && today <= end) return '청약중';
  return '청약예정';
}
function parseNum(s) {
  if (!s || s === '-') return 0;
  return parseInt(String(s).replace(/,/g, ''), 10) || 0;
}
function parseRate(s) {
  if (!s || s === '-') return null;
  const m = s.match(/([\d,]+\.?\d*)\s*:\s*1/);
  return m ? parseFloat(m[1].replace(/,/g, '')) : null;
}

// ── 상장일 파싱 ──────────────────────────────────────────────
function parseListingDates(plain) {
  const idx = plain.indexOf('IPO 신규상장 일정');
  if (idx === -1) return {};
  const section = plain.slice(idx, idx + 3000);
  const map = {};
  const today = TODAY();
  const yearNow = new Date().getFullYear();
  for (const m of section.matchAll(/(\d{2})\/(\d{2})\s+([^\n\r\d][^\n\r]*)/g)) {
    const mm = parseInt(m[1]), dd = parseInt(m[2]);
    const name = m[3].split(/\s{2,}/)[0].trim();
    if (!name) continue;
    try {
      let d = new Date(yearNow, mm - 1, dd);
      const daysDiff = (new Date(today) - d) / 86400000;
      if (daysDiff > 90) d = new Date(yearNow + 1, mm - 1, dd);
      map[name] = d.toISOString().slice(0, 10);
    } catch (_) {}
  }
  return map;
}

// ── 수요예측 파싱 ────────────────────────────────────────────
async function fetchDemandData() {
  const result = {};
  try {
    const plain = stripHtml(await fetchEucKr('https://www.38.co.kr/html/fund/index.htm?o=r'));
    const lines = plain.split(/[\n\r]+/).map(l => l.trim()).filter(Boolean);
    const DATE_RE = /20\d\d\.\d{2}\.\d{2}~\d{2}\.\d{2}/;
    const RATE_RE = /([\d,]+\.?\d*)\s*:\s*1/;
    const LOCK_RE = /([\d,]+\.?\d*)\s*%/;
    for (let i = 0; i < lines.length; i++) {
      if (!DATE_RE.test(lines[i])) continue;
      let name = '';
      for (let b = 1; b <= 4 && i - b >= 0; b++) {
        const p = lines[i - b];
        if (/^[\d,.\-~:]+$/.test(p)) continue;
        if (!(/[가-힣]/.test(p))) continue;
        name = p; break;
      }
      if (!name) continue;
      const after = lines.slice(i + 1, i + 10).join(' ');
      const rm = RATE_RE.exec(after);
      const lm = LOCK_RE.exec(after);
      if (rm || lm) result[name] = {
        inst_comp_rate: rm ? parseFloat(rm[1].replace(/,/g, '')) : null,
        lock_up_pct: lm ? parseFloat(lm[1].replace(/,/g, '')) : null,
      };
    }
  } catch (e) { console.warn('[수요예측] 조회 실패:', e.message); }
  return result;
}

// ── 청약일정 파싱 ────────────────────────────────────────────
function parseSchedule(plain, listingMap, demandMap, today) {
  const DATE_RANGE_RE = /20(\d\d)\.(\d{2}\.\d{2})~(\d{2}\.\d{2})/;
  const BAND_RE = /^([\d,]+)~([\d,]+)$/;
  const RATE_RE = /([\d,]+\.?\d*)\s*:\s*1/;
  const SKIP = new Set(['종목명','공모주일정','확정공모가','희망공모가','청약경쟁률','주간사','분석','수요예측결과']);
  const lines = plain.split(/[\n\r]+/).map(l => l.trim()).filter(Boolean);
  const records = [], seen = new Set();
  const year = new Date().getFullYear();

  for (let i = 0; i < lines.length; i++) {
    const m = DATE_RANGE_RE.exec(lines[i]);
    if (!m) continue;
    const startMmdd = m[2], endMmdd = m[3];
    const dateStart = parseDate(String(year), startMmdd);
    const dateEnd   = parseDate(String(endMmdd < startMmdd ? year + 1 : year), endMmdd);
    let name = '';
    for (let b = 1; b <= 4 && i - b >= 0; b++) {
      const p = lines[i - b];
      if (/^[\d,.\-~:]+$/.test(p) || SKIP.has(p)) continue;
      if (!(/[가-힣]/.test(p))) continue;
      name = p; break;
    }
    if (!name || seen.has(name)) continue;
    seen.add(name);
    let priceIpo = 0, bandLow = 0, bandHigh = 0, instRate = null, broker = '';
    let fi = 0;
    const fields = [];
    for (let f = 1; f <= 7 && i + f < lines.length; f++) {
      if (DATE_RANGE_RE.test(lines[i + f])) break;
      fields.push(lines[i + f]);
    }
    if (fi < fields.length && /^(-|[\d,]+)$/.test(fields[fi])) { priceIpo = parseNum(fields[fi++]); }
    if (fi < fields.length && BAND_RE.test(fields[fi])) {
      const bm = BAND_RE.exec(fields[fi++]);
      bandLow = parseNum(bm[1]); bandHigh = parseNum(bm[2]);
    }
    if (fi < fields.length && (RATE_RE.test(fields[fi]) || fields[fi] === '-')) {
      instRate = parseRate(fields[fi++]);
    }
    if (fi < fields.length && /[가-힣]/.test(fields[fi])) { broker = fields[fi]; }
    const dd = demandMap[name] || {};
    const inst = dd.inst_comp_rate ?? instRate;
    const lock = dd.lock_up_pct ?? null;
    const score = calcScore(name, inst, lock, priceIpo, bandLow, bandHigh);
    records.push({
      name, date_sub_start: dateStart, date_sub_end: dateEnd,
      date_allot: '', date_list: listingMap[name] || '',
      price_ipo: priceIpo, price_band_low: bandLow, price_band_high: bandHigh,
      broker, inst_comp_rate: inst, lock_up_pct: lock,
      band_position: bandHigh > bandLow ? (priceIpo - bandLow) / (bandHigh - bandLow) : 0.5,
      score, recommendation: recommend(name, score),
      status: calcStatus(dateStart, dateEnd),
      subscribed: false, shares_alloc: null,
      note: name.includes('스팩') ? '스팩주: 원금보장형, 수익 낮음' : '',
      fetched_at: today,
    });
  }
  const ORDER = { '청약중': 0, '청약예정': 1, '청약완료': 2, '상장완료': 3 };
  records.sort((a, b) => (ORDER[a.status] ?? 9) - (ORDER[b.status] ?? 9) || a.date_sub_start.localeCompare(b.date_sub_start));
  return records;
}

// ── 병합 ─────────────────────────────────────────────────────
const USER_FIELDS = ['subscribed','shares_alloc','price_open','sell_qty','sell_date','direct_profit','direct_rate','date_list','status','id','note'];
function mergeIpo(fresh, existing) {
  const exMap = Object.fromEntries(existing.filter(r => r?.name).map(r => [r.name, r]));
  const merged = fresh.map(rec => {
    const old = exMap[rec.name] || {};
    for (const f of USER_FIELDS) {
      if (f === 'date_list') { if (old[f]) rec[f] = rec[f] || old[f]; continue; }
      if (f === 'status')    { if (old[f] === '상장완료') rec[f] = '상장완료'; continue; }
      if (f === 'note') {
        const oldNote = old[f] || '';
        if (oldNote && !(rec.note||'').includes(oldNote))
          rec.note = ((rec.note||'') + ' / ' + oldNote).trim().replace(/^\/ /, '');
        continue;
      }
      if (old[f] != null) rec[f] = old[f];
    }
    return rec;
  });
  const freshNames = new Set(fresh.map(r => r.name));
  for (const old of existing) {
    if (!old?.name || freshNames.has(old.name)) continue;
    if (!/[가-힣]/.test(old.name)) continue;
    const hasUserData = old.subscribed || old.shares_alloc != null || (old.price_open > 0) || old.direct_profit != null;
    if (hasUserData) { merged.push(old); continue; }
    if (['상장완료','청약완료'].includes(old.status)) merged.push(old);
  }
  return merged;
}

// ── Gist 읽기/쓰기 ───────────────────────────────────────────
async function readGistIpo() {
  const r = await fetch(`https://api.github.com/gists/${GIST_ID}`, {
    headers: { Authorization: `Bearer ${GH_TOKEN}`, Accept: 'application/vnd.github+json', 'User-Agent': 'ipo-crawl' },
  });
  if (!r.ok) throw new Error(`Gist read error: ${r.status}`);
  const g = await r.json();
  const f = g.files?.['ipo.json'];
  return f ? JSON.parse(f.content || '[]') : [];
}
async function writeGistIpo(data) {
  const r = await fetch(`https://api.github.com/gists/${GIST_ID}`, {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${GH_TOKEN}`, Accept: 'application/vnd.github+json',
               'Content-Type': 'application/json', 'User-Agent': 'ipo-crawl' },
    body: JSON.stringify({ files: { 'ipo.json': { content: JSON.stringify(data, null, 2) } } }),
  });
  if (!r.ok) throw new Error(`Gist write error: ${r.status}`);
}

// ── JSONBin 읽기/쓰기 ────────────────────────────────────────
async function readJsonBin() {
  const r = await fetch(`https://api.jsonbin.io/v3/b/${JSONBIN_ID}/latest`, {
    headers: { 'X-Master-Key': JSONBIN_KEY },
  });
  if (!r.ok) return {};
  const d = await r.json();
  return d.record || {};
}
async function writeJsonBin(data) {
  const r = await fetch(`https://api.jsonbin.io/v3/b/${JSONBIN_ID}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-Master-Key': JSONBIN_KEY },
    body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error(`JSONBin write error: ${r.status}`);
}

// ── 메인 ─────────────────────────────────────────────────────
async function main() {
  const today = TODAY();
  console.log(`[IPO] 크롤링 시작: ${today}`);

  console.log('[IPO] 38.co.kr 데이터 가져오는 중...');
  const [mainHtml, demandMap] = await Promise.all([
    fetchEucKr('https://www.38.co.kr/html/fund/index.htm?o=k'),
    fetchDemandData(),
  ]);
  const plain = stripHtml(mainHtml);
  const listingMap = parseListingDates(plain);
  const fresh = parseSchedule(plain, listingMap, demandMap, today);
  console.log(`[IPO] 크롤링 완료: ${fresh.length}건`);
  if (fresh.length === 0) { console.error('[IPO] 파싱 결과 0건, 종료'); process.exit(1); }

  console.log('[IPO] Gist 기존 데이터 읽는 중...');
  const existingGist = await readGistIpo();
  const merged = mergeIpo(fresh, existingGist);
  console.log(`[IPO] 병합 완료: ${merged.length}건`);

  // 통계
  const statuses = {};
  for (const r of merged) statuses[r.status] = (statuses[r.status] || 0) + 1;
  console.log('[IPO] 상태별:', JSON.stringify(statuses));

  console.log('[IPO] Gist 업데이트 중...');
  await writeGistIpo(merged);
  console.log('[IPO] Gist 업데이트 완료');

  if (JSONBIN_ID && JSONBIN_KEY) {
    console.log('[IPO] JSONBin 업데이트 중...');
    const binData = await readJsonBin();
    const existingJB = binData.ipo || [];
    // JSONBin의 ipo와도 병합 (subscribed 등 보존)
    const mergedJB = mergeIpo(merged, existingJB);
    await writeJsonBin({ ...binData, ipo: mergedJB });
    console.log('[IPO] JSONBin 업데이트 완료');
  }

  const upcoming = merged.filter(r => ['청약예정','청약중','상장예정'].includes(r.status));
  console.log(`[IPO] 완료! 예정 일정: ${upcoming.length}건`);
}

main().catch(e => { console.error('[IPO] 오류:', e.message); process.exit(1); });
