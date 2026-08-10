/**
 * 통합 트레이딩 API
 *
 * ── 주식 자동매매 잡 (기존 profit-jobs.js 통합) ──
 * GET/POST/DELETE /api/profit-sell   → profit_sell_jobs.json
 * GET/POST/DELETE /api/profit-buy    → profit_buy_jobs.json
 *
 * ── 코인 자동매매 잡 CRUD ──
 * GET/POST/DELETE /api/coin-buy      → coin_buy_jobs.json
 * GET/POST/DELETE /api/coin-sell     → coin_sell_jobs.json
 * GET             /api/coin-account  → coin_account.json
 *
 * ── 코인 실행 엔진 (Vercel 고정 IP → Upbit API 직접 호출) ──
 * GET /api/coin-runner?mode=all|buy|sell|balance
 *
 * ── 초단타(스캘핑) 잡 CRUD (daemon_scalp.py가 실행 주체) ──
 * GET/POST/PATCH/DELETE /api/scalp-coin   → scalp_coin_jobs.json
 * GET/POST/PATCH/DELETE /api/scalp-stock  → scalp_stock_jobs.json
 * GET/POST(PATCH)       /api/scalp-control → scalp_control.json (전체 정지 킬스위치)
 * GET/POST(PATCH)       /api/scalp-auto-config → scalp_auto_config.json (자동 종목 발굴 설정)
 *
 * ── Vercel 아웃바운드 IP 확인 ──
 * GET /api/coin-ip
 */

import crypto from 'crypto';

// ══════════════════════════════════════════════════════════
// 공통 유틸
// ══════════════════════════════════════════════════════════
const nowKst = () =>
  new Date(Date.now() + 9 * 3600000).toISOString().slice(0, 16).replace('T', ' ');

const COIN_NAMES = {
  'KRW-BTC':'비트코인','KRW-ETH':'이더리움','KRW-XRP':'리플','KRW-SOL':'솔라나',
  'KRW-DOGE':'도지코인','KRW-ADA':'에이다','KRW-AVAX':'아발란체','KRW-DOT':'폴카닷',
  'KRW-LINK':'체인링크','KRW-ATOM':'코스모스','KRW-MATIC':'폴리곤','KRW-TRX':'트론',
  'KRW-SHIB':'시바이누','KRW-LTC':'라이트코인','KRW-BCH':'비트코인캐시',
  'KRW-ETC':'이더리움클래식','KRW-NEAR':'니어프로토콜','KRW-AAVE':'에이브',
  'KRW-UNI':'유니스왑','KRW-SAND':'샌드박스',
};

// ══════════════════════════════════════════════════════════
// Gist 공통 헬퍼
// ══════════════════════════════════════════════════════════
function ghHeaders(ghToken) {
  return {
    Accept: 'application/vnd.github+json',
    'User-Agent': 'stock-analyzer',
    Authorization: `Bearer ${ghToken}`,
  };
}

let _gistCache = null;
let _gistCacheAt = 0;

async function fetchGist(gistId, ghToken, force = false) {
  if (!force && _gistCache && Date.now() - _gistCacheAt < 20000) return _gistCache;
  const r = await fetch(`https://api.github.com/gists/${gistId}`, { headers: ghHeaders(ghToken) });
  if (!r.ok) return null;
  _gistCache = await r.json();
  _gistCacheAt = Date.now();
  return _gistCache;
}

async function readGistFile(gistId, ghToken, filename) {
  const gist = await fetchGist(gistId, ghToken);
  if (!gist) return filename.endsWith('account.json') ? {} : [];
  const file = gist.files?.[filename];
  if (!file) return filename.endsWith('account.json') ? {} : [];
  try { return JSON.parse(file.content); } catch { return filename.endsWith('account.json') ? {} : []; }
}

async function writeGistFile(gistId, ghToken, filename, data) {
  const r = await fetch(`https://api.github.com/gists/${gistId}`, {
    method: 'PATCH',
    headers: { ...ghHeaders(ghToken), 'Content-Type': 'application/json' },
    body: JSON.stringify({ files: { [filename]: { content: JSON.stringify(data, null, 2) } } }),
  });
  _gistCache = null; // 캐시 무효화
  return r.ok;
}

async function writeGistFiles(gistId, ghToken, filesDict) {
  const files = {};
  for (const [name, data] of Object.entries(filesDict))
    files[name] = { content: JSON.stringify(data, null, 2) };
  const r = await fetch(`https://api.github.com/gists/${gistId}`, {
    method: 'PATCH',
    headers: { ...ghHeaders(ghToken), 'Content-Type': 'application/json' },
    body: JSON.stringify({ files }),
  });
  _gistCache = null;
  return r.ok;
}

// ══════════════════════════════════════════════════════════
// 주식 자동매매 잡 CRUD (profit-jobs.js 기능)
// ══════════════════════════════════════════════════════════
async function handleStockJobs(req, res, url, gistId, ghToken) {
  const FILENAME = url.includes('profit-sell')  ? 'profit_sell_jobs.json'
                 :                                'profit_buy_jobs.json';

  if (req.method === 'GET') {
    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json(await readGistFile(gistId, ghToken, FILENAME));
  }

  if (req.method === 'POST') {
    const job = req.body || {};
    if (!job.ticker) return res.status(400).json({ error: 'ticker 필수' });
    const newJob = { ...job, status: 'active', created_at: nowKst() };
    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const idx = Array.isArray(jobs) ? jobs.findIndex(j => j.ticker === job.ticker && j.status === 'active') : -1;
    const list = Array.isArray(jobs) ? jobs : [];
    if (idx >= 0) list[idx] = newJob; else list.unshift(newJob);
    const ok = await writeGistFile(gistId, ghToken, FILENAME, list);
    if (!ok) return res.status(500).json({ error: '저장 실패' });

    const isBuyUrl   = url.includes('profit-buy');
    const isSellUrl  = url.includes('profit-sell');
    const isMarket   = job.condition_type === 'market';

    let triggered = false, triggerError = null;
    if (isSellUrl || (isBuyUrl && isMarket)) {
      const jobName = isSellUrl ? 'profit_sell' : 'profit_buy';
      try {
        triggered = await triggerGHAction(ghToken, jobName);
        if (!triggered) triggerError = 'workflow_dispatch 실패';
      } catch (e) { triggerError = e.message; }
    }
    return res.status(200).json({ ok: true, triggered, triggerError });
  }

  if (req.method === 'DELETE') {
    const { ticker } = req.query;
    if (!ticker) return res.status(400).json({ error: 'ticker 필수' });
    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = Array.isArray(jobs) ? jobs : [];
    const updated = list.map(j =>
      j.ticker === ticker && j.status === 'active'
        ? { ...j, status: 'cancelled', cancelled_at: nowKst() } : j
    );
    const ok = await writeGistFile(gistId, ghToken, FILENAME, updated);
    return res.status(ok ? 200 : 500).json(ok ? { ok: true } : { error: '취소 실패' });
  }
  return res.status(405).json({ error: 'Method not allowed' });
}

async function triggerGHAction(ghToken, jobName) {
  const r = await fetch(
    'https://api.github.com/repos/asakasimo1/stock-trader/actions/workflows/trader.yml/dispatches',
    {
      method: 'POST',
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'stock-analyzer',
                 Authorization: `Bearer ${ghToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref: 'main', inputs: { job: jobName } }),
    }
  );
  return r.ok;
}

// ══════════════════════════════════════════════════════════
// 주식 그리드 잡 CRUD
// ══════════════════════════════════════════════════════════
async function handleStockGridJobs(req, res, gistId, ghToken) {
  const FILENAME = 'stock_grid_jobs.json';
  if (req.method === 'GET') {
    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json(await readGistFile(gistId, ghToken, FILENAME));
  }
  if (req.method === 'POST') {
    const job = req.body || {};
    if (!job.ticker) return res.status(400).json({ error: 'ticker 필수' });
    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = Array.isArray(jobs) ? jobs : [];
    list.unshift({ ...job, status: 'active', created_at: nowKst() });
    const ok = await writeGistFile(gistId, ghToken, FILENAME, list);
    return res.status(ok ? 200 : 500).json(ok ? { ok: true } : { error: '저장 실패' });
  }
  if (req.method === 'DELETE') {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'id 필수' });
    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = (Array.isArray(jobs) ? jobs : []).filter(j => j.id !== id);
    const ok = await writeGistFile(gistId, ghToken, FILENAME, list);
    return res.status(ok ? 200 : 500).json(ok ? { ok: true } : { error: '삭제 실패' });
  }
  return res.status(405).json({ error: 'Method not allowed' });
}

// ══════════════════════════════════════════════════════════
// 코인 자동매매 잡 CRUD
// ══════════════════════════════════════════════════════════
async function handleCoinJobs(req, res, url, gistId, ghToken) {
  const isAccount = url.includes('coin-account');
  const FILENAME  = isAccount             ? 'coin_account.json'
                  : url.includes('coin-sell')  ? 'coin_sell_jobs.json'
                  :                              'coin_buy_jobs.json';

  if (req.method === 'GET') {
    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json(await readGistFile(gistId, ghToken, FILENAME));
  }
  if (isAccount) return res.status(405).json({ error: 'Method not allowed' });

  if (req.method === 'POST') {
    const job = req.body || {};
    if (!job.ticker) return res.status(400).json({ error: 'ticker 필수' });
    const newJob = { ...job, status: 'active', created_at: nowKst() };
    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = Array.isArray(jobs) ? jobs : [];
    const idx  = list.findIndex(j => j.ticker === job.ticker && j.status === 'active');
    if (idx >= 0) list[idx] = newJob; else list.unshift(newJob);
    const ok = await writeGistFile(gistId, ghToken, FILENAME, list);
    if (!ok) return res.status(500).json({ error: '저장 실패' });

    const isBuyUrl   = url.includes('coin-buy');
    const isSellUrl  = url.includes('coin-sell');
    const isMarket   = job.condition_type === 'market_krw' || job.condition_type === 'market';

    // 즉시 coin-runner 실행 (fire-and-forget)
    if (isSellUrl || (isBuyUrl && isMarket)) {
      const mode = isBuyUrl ? 'buy' : 'sell';
      triggerCoinRunner(req, mode);
    }
    return res.status(200).json({ ok: true, triggered: true });
  }

  if (req.method === 'DELETE') {
    const { ticker } = req.query;
    if (!ticker) return res.status(400).json({ error: 'ticker 필수' });
    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = Array.isArray(jobs) ? jobs : [];
    const updated = list.map(j =>
      j.ticker === ticker && j.status === 'active'
        ? { ...j, status: 'cancelled', cancelled_at: nowKst() } : j
    );
    const ok = await writeGistFile(gistId, ghToken, FILENAME, updated);
    return res.status(ok ? 200 : 500).json(ok ? { ok: true } : { error: '취소 실패' });
  }
  return res.status(405).json({ error: 'Method not allowed' });
}

function triggerCoinRunner(req, mode = 'all') {
  const host  = req.headers['x-forwarded-host'] || req.headers.host || '';
  const proto = req.headers['x-forwarded-proto'] || 'https';
  const secret = process.env.COIN_RUNNER_SECRET || '';
  const qs    = `mode=${mode}${secret ? `&secret=${secret}` : ''}`;
  fetch(`${proto}://${host}/api/coin-runner?${qs}`).catch(() => {});
}

// ══════════════════════════════════════════════════════════
// Upbit API 헬퍼 (coin-runner 전용)
// ══════════════════════════════════════════════════════════
const UPBIT_BASE = 'https://api.upbit.com/v1';
const BUY_FEE   = 0.0005;
const SELL_FEE  = 0.0005;
const AUTO_PROFIT = 20.0;
const AUTO_LOSS   = -4.0;

function makeJwt(accessKey, secretKey, params = null) {
  const payload = { access_key: accessKey, nonce: crypto.randomUUID() };
  if (params && Object.keys(params).length > 0) {
    const qs = new URLSearchParams(params).toString();
    payload.query_hash     = crypto.createHash('sha512').update(qs).digest('hex');
    payload.query_hash_alg = 'SHA512';
  }
  const h   = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url');
  const b   = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const sig = crypto.createHmac('sha256', secretKey).update(`${h}.${b}`).digest('base64url');
  return `Bearer ${h}.${b}.${sig}`;
}

async function upbitBalance(accessKey, secretKey) {
  const r = await fetch(`${UPBIT_BASE}/accounts`, { headers: { Authorization: makeJwt(accessKey, secretKey) } });
  if (!r.ok) throw new Error(`잔고 조회 실패 HTTP ${r.status}`);
  return r.json();
}

async function upbitPrices(markets) {
  if (!markets.length) return {};
  const r = await fetch(`${UPBIT_BASE}/ticker?markets=${markets.join(',')}`);
  if (!r.ok) return {};
  const result = {};
  for (const d of await r.json()) result[d.market] = { price: d.trade_price, chgPct: +(d.signed_change_rate * 100).toFixed(2) };
  return result;
}

async function upbitOrder(accessKey, secretKey, { market, side, ordType, volume, price }) {
  const params = { market, side, ord_type: ordType };
  if (volume != null) params.volume = String(volume);
  if (price  != null) params.price  = String(Math.round(price));
  const r = await fetch(`${UPBIT_BASE}/orders`, {
    method: 'POST',
    headers: { Authorization: makeJwt(accessKey, secretKey, params), 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!r.ok) throw new Error(`주문 실패 ${market} HTTP ${r.status} ${await r.text()}`);
  return r.json();
}

function netPnlPct(buyPrice, curPrice) {
  return ((curPrice * (1 - SELL_FEE)) - (buyPrice * (1 + BUY_FEE))) / (buyPrice * (1 + BUY_FEE)) * 100;
}

function sellTarget(buyPrice, takePct) {
  return buyPrice * (1 + BUY_FEE) * (1 + takePct / 100) / (1 - SELL_FEE);
}

// ── 코인 실행 엔진 ────────────────────────────────────────
async function handleCoinRunner(req, res, gistId, ghToken) {
  const secret     = process.env.COIN_RUNNER_SECRET;
  const cronSecret = process.env.CRON_SECRET;
  const authHeader = req.headers['authorization'] || '';
  const isCronCall = cronSecret && authHeader === `Bearer ${cronSecret}`;
  if (!isCronCall && secret && req.query.secret !== secret)
    return res.status(401).json({ error: '인증 실패' });

  const accessKey = process.env.UPBIT_ACCESS_KEY;
  const secretKey = process.env.UPBIT_SECRET_KEY;
  if (!accessKey || !secretKey)
    return res.status(500).json({ error: 'UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 미설정' });

  const logs = [];
  const log  = msg => { logs.push(`[${nowKst()}] ${msg}`); console.log(msg); };
  const mode = req.query.mode || 'all';

  try {
    // ── 잔고 조회 모드 ─────────────────────────────────────
    if (mode === 'balance') {
      const accounts  = await upbitBalance(accessKey, secretKey);
      const coinAccts = accounts.filter(a => a.currency !== 'KRW' && +a.balance > 0);
      const prices    = await upbitPrices(coinAccts.map(a => `KRW-${a.currency}`));
      let krw = 0;
      const holdings = [];
      for (const acc of accounts) {
        if (acc.currency === 'KRW') { krw = +acc.balance; continue; }
        if (+acc.balance <= 0) continue;
        const ticker   = `KRW-${acc.currency}`;
        const qty      = +acc.balance + +acc.locked;
        const avgPrice = +acc.avg_buy_price;
        const curPrice = prices[ticker]?.price ?? avgPrice;
        const cost     = avgPrice * qty * (1 + BUY_FEE);
        const evalAmt  = curPrice * qty;
        const netEval  = evalAmt * (1 - SELL_FEE);  // pnl/pnl_pct는 매도수수료까지 포함한 순손익
        holdings.push({ ticker, symbol: acc.currency, name: COIN_NAMES[ticker] || acc.currency, qty, avg_price: avgPrice,
          cur_price: curPrice, eval_amount: evalAmt,
          pnl: netEval - cost, pnl_pct: +((netEval - cost) / cost * 100).toFixed(2) });
      }
      const account = { krw, holdings, updated_at: nowKst() };
      await writeGistFiles(gistId, ghToken, { 'coin_account.json': account });
      log(`잔고 저장: KRW ${krw.toLocaleString()}원, 코인 ${holdings.length}종`);
      return res.status(200).json({ ok: true, account, logs });
    }

    // ── 잡 로드 ────────────────────────────────────────────
    const [buyJobs, sellJobs] = await Promise.all([
      mode !== 'sell' ? readGistFile(gistId, ghToken, 'coin_buy_jobs.json')   : [],
      mode !== 'buy'  ? readGistFile(gistId, ghToken, 'coin_sell_jobs.json')  : [],
    ]);
    const _buy   = Array.isArray(buyJobs)   ? buyJobs   : [];
    const _sell  = Array.isArray(sellJobs)  ? sellJobs  : [];

    // ── 현재가 + 잔고 ──────────────────────────────────────
    const tickers = new Set([
      ..._buy.filter(j => j.status === 'active').map(j => j.ticker),
      ..._sell.filter(j => ['active','submitted'].includes(j.status)).map(j => j.ticker),
    ]);
    let prices = {}, holdings = [];
    if (mode !== 'buy') {
      try {
        const accounts = await upbitBalance(accessKey, secretKey);
        for (const a of accounts.filter(acc => acc.currency !== 'KRW' && +acc.balance > 0))
          tickers.add(`KRW-${a.currency}`);
        prices = await upbitPrices([...tickers]);
        for (const acc of accounts) {
          if (acc.currency === 'KRW' || +acc.balance <= 0) continue;
          const ticker   = `KRW-${acc.currency}`;
          const qty      = +acc.balance + +acc.locked;
          const avgPrice = +acc.avg_buy_price;
          holdings.push({ ticker, qty, avg_price: avgPrice, cur_price: prices[ticker]?.price ?? avgPrice });
        }
      } catch (e) { log(`잔고 조회 실패: ${e.message}`); }
    } else {
      prices = await upbitPrices([...tickers]);
    }

    let bChg = false, sChg = false;

    // ── 자동매도 규칙 ──────────────────────────────────────
    if (mode !== 'buy') {
      for (const h of holdings) {
        const cur = prices[h.ticker]?.price ?? h.cur_price;
        const pct = netPnlPct(h.avg_price, cur);
        if (pct >= AUTO_PROFIT || pct <= AUTO_LOSS) {
          const reason = pct >= AUTO_PROFIT ? '익절' : '손절';
          log(`★ 자동${reason}: ${h.ticker} ${pct.toFixed(2)}%`);
          try { await upbitOrder(accessKey, secretKey, { market: h.ticker, side: 'ask', ordType: 'market', volume: h.qty }); }
          catch (e) { log(`자동${reason} 실패: ${e.message}`); }
        }
      }
    }

    // ── 매수 잡 ────────────────────────────────────────────
    if (mode !== 'sell') {
      for (const job of _buy) {
        if (job.status !== 'active') continue;
        const cur = prices[job.ticker]?.price;
        if (!cur) continue;
        const cond = job.condition_type;
        if (cond === 'market_krw') {
          const amt = +job.krw_amount || 0;
          if (amt < 5000) continue;
          log(`★ 시장가 매수: ${job.ticker} ${amt.toLocaleString()}원`);
          try {
            const r = await upbitOrder(accessKey, secretKey, { market: job.ticker, side: 'bid', ordType: 'price', price: amt });
            Object.assign(job, { status:'done', executed_at:nowKst(), order_uuid:r.uuid, exec_price:cur, exec_qty:+(amt/cur).toFixed(8) });
            bChg = true;
          } catch (e) { log(`매수 실패: ${e.message}`); }
        } else if (cond === 'limit') {
          const tp = +job.target_price || 0;
          if (!tp || cur > tp) continue;
          const qty = +job.krw_amount > 0 ? +(+job.krw_amount / tp).toFixed(8) : +job.coin_qty || 0;
          if (qty <= 0) continue;
          log(`★ 지정가 매수: ${job.ticker} ${qty} @ ${tp.toLocaleString()}원`);
          try {
            const r = await upbitOrder(accessKey, secretKey, { market: job.ticker, side: 'bid', ordType: 'limit', volume: qty, price: tp });
            Object.assign(job, { status:'done', executed_at:nowKst(), order_uuid:r.uuid, exec_price:tp, exec_qty:qty });
            bChg = true;
          } catch (e) { log(`지정가 매수 실패: ${e.message}`); }
        }
      }
    }

    // ── 매도 잡 ────────────────────────────────────────────
    if (mode !== 'buy') {
      for (const job of _sell) {
        if (!['active','submitted'].includes(job.status)) continue;
        const cur = prices[job.ticker]?.price;
        if (!cur) continue;
        const bp = +job.buy_price || 0, qty = +job.qty || 0;
        const tv = +job.target_value || 0;
        let tp;
        if (job.target_type === 'price') tp = tv;
        else if (job.target_type === 'amount' && bp && qty) tp = (bp * qty * (1 + BUY_FEE) + tv) / qty / (1 - SELL_FEE);
        else tp = bp > 0 ? sellTarget(bp, tv) : 0;
        if (!tp || cur < tp) continue;
        const pct = bp > 0 ? netPnlPct(bp, cur) : 0;
        log(`★ 목표 매도: ${job.ticker} ${pct.toFixed(2)}%`);
        try {
          const r = await upbitOrder(accessKey, secretKey, { market: job.ticker, side: 'ask', ordType: 'market', volume: qty });
          Object.assign(job, { status:'done', executed_at:nowKst(), order_uuid:r.uuid, exec_price:cur, pnl_pct:+pct.toFixed(2) });
          sChg = true;
        } catch (e) { log(`매도 실패: ${e.message}`); }
      }
    }

    // ── 변경분 Gist 저장 ─────────────────────────────────
    const toWrite = {};
    if (bChg) toWrite['coin_buy_jobs.json']   = _buy;
    if (sChg) toWrite['coin_sell_jobs.json']  = _sell;
    if (Object.keys(toWrite).length) {
      const ok = await writeGistFiles(gistId, ghToken, toWrite);
      log(`Gist 저장 ${ok ? '완료' : '실패'}`);
    } else log('변경 없음');

    return res.status(200).json({ ok: true, logs });
  } catch (e) {
    log(`오류: ${e.message}`);
    return res.status(500).json({ ok: false, error: e.message, logs });
  }
}

// ── Vercel 아웃바운드 IP 확인 ─────────────────────────────
// ══════════════════════════════════════════════════════════
// 그리드 트레이딩 잡 CRUD
// ══════════════════════════════════════════════════════════
async function handleCoinGrid(req, res, gistId, ghToken) {
  const FILENAME = 'coin_grid_jobs.json';
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'GET') {
    return res.status(200).json(await readGistFile(gistId, ghToken, FILENAME));
  }

  if (req.method === 'POST') {
    const b = req.body || {};
    if (!b.ticker)       return res.status(400).json({ error: 'ticker 필수' });
    if (!b.lower_price)  return res.status(400).json({ error: 'lower_price 필수' });
    if (!b.upper_price)  return res.status(400).json({ error: 'upper_price 필수' });
    if (!b.krw_per_grid) return res.status(400).json({ error: 'krw_per_grid 필수' });

    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = Array.isArray(jobs) ? jobs : [];
    const newJob = {
      id:                  Date.now().toString(36),
      name:                b.name || `${b.ticker} 그리드`,
      ticker:              b.ticker,
      status:              'init',
      grid_pct:            +b.grid_pct     || 1.5,
      lower_price:         +b.lower_price,
      upper_price:         +b.upper_price,
      krw_per_grid:        +b.krw_per_grid,
      stop_loss_on_escape: b.stop_loss_on_escape !== false,
      grids:               [],
      total_profit_krw:    0,
      trade_count:         0,
      created_at:          nowKst(),
    };
    list.unshift(newJob);
    const ok = await writeGistFile(gistId, ghToken, FILENAME, list);
    return res.status(ok ? 200 : 500).json(ok ? newJob : { error: '저장 실패' });
  }

  if (req.method === 'PATCH') {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'id 필수' });
    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = Array.isArray(jobs) ? jobs : [];
    const idx  = list.findIndex(j => j.id === id);
    if (idx < 0) return res.status(404).json({ error: '잡 없음' });
    list[idx] = { ...list[idx], ...req.body };
    const ok = await writeGistFile(gistId, ghToken, FILENAME, list);
    return res.status(ok ? 200 : 500).json(ok ? list[idx] : { error: '저장 실패' });
  }

  if (req.method === 'DELETE') {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'id 필수' });
    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = Array.isArray(jobs) ? jobs : [];
    const idx  = list.findIndex(j => j.id === id);
    if (idx >= 0) {
      // 주문이 존재할 수 있는 상태면 stopping으로 전환 (daemon이 주문 취소 처리)
      if (['active', 'init', 'reinit'].includes(list[idx].status)) {
        list[idx].status = 'stopping';
      } else {
        list.splice(idx, 1);
      }
    }
    const ok = await writeGistFile(gistId, ghToken, FILENAME, list);
    return res.status(ok ? 200 : 500).json(ok ? { ok: true } : { error: '처리 실패' });
  }

  return res.status(405).json({ error: 'Method not allowed' });
}

// ══════════════════════════════════════════════════════════
// 초단타(스캘핑) 잡 CRUD — 코인/주식 공용
// 안전 기본값: 신규 잡은 항상 status='paused'로 생성 (사용자가 명시적으로 켜야 실거래 시작)
// ══════════════════════════════════════════════════════════
async function handleScalpJobs(req, res, url, gistId, ghToken) {
  const FILENAME = url.includes('scalp-coin') ? 'scalp_coin_jobs.json' : 'scalp_stock_jobs.json';
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'GET') {
    return res.status(200).json(await readGistFile(gistId, ghToken, FILENAME));
  }

  if (req.method === 'POST') {
    const b = req.body || {};
    if (!b.ticker) return res.status(400).json({ error: 'ticker 필수' });

    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = Array.isArray(jobs) ? jobs : [];
    const today = new Date(Date.now() + 9 * 3600000).toISOString().slice(0, 10);
    const newJob = {
      id:                  Date.now().toString(36),
      ticker:              b.ticker,
      name:                b.name || b.ticker,
      status:              'paused',   // 항상 일시정지로 생성 — 사용자가 직접 시작해야 함
      phase:               'watching',
      entry_momentum_pct:  +b.entry_momentum_pct || 0.4,
      lookback_sec:        +b.lookback_sec       || 30,
      max_day_chg_pct:     +b.max_day_chg_pct    || 5.0,
      take_profit_pct:     +b.take_profit_pct    || 0.6,
      stop_loss_pct:       +b.stop_loss_pct      || 0.4,
      time_stop_sec:       +b.time_stop_sec      || 180,
      time_stop_loss_pct:  +b.time_stop_loss_pct || 0.5,
      krw_amount:          +b.krw_amount         || 0,   // 코인용
      qty:                 +b.qty                || 0,   // 주식용 (수량 지정)
      amount:              +b.amount             || 0,   // 주식용 (금액 지정)
      max_daily_loss_krw:  b.max_daily_loss_krw !== undefined ? +b.max_daily_loss_krw : -20000,
      buy_price: 0, buy_qty: 0, entered_at: 0, buy_uuid: '',
      trades_today: 0, realized_pnl_today: 0, stats_date: today,
      created_at: nowKst(),
    };
    list.unshift(newJob);
    const ok = await writeGistFile(gistId, ghToken, FILENAME, list);
    return res.status(ok ? 200 : 500).json(ok ? newJob : { error: '저장 실패' });
  }

  if (req.method === 'PATCH') {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'id 필수' });
    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = Array.isArray(jobs) ? jobs : [];
    const idx  = list.findIndex(j => j.id === id);
    if (idx < 0) return res.status(404).json({ error: '잡 없음' });
    list[idx] = { ...list[idx], ...req.body };
    const ok = await writeGistFile(gistId, ghToken, FILENAME, list);
    return res.status(ok ? 200 : 500).json(ok ? list[idx] : { error: '저장 실패' });
  }

  if (req.method === 'DELETE') {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'id 필수' });
    const jobs = await readGistFile(gistId, ghToken, FILENAME);
    const list = Array.isArray(jobs) ? jobs : [];
    const idx  = list.findIndex(j => j.id === id);
    if (idx < 0) return res.status(404).json({ error: '잡 없음' });
    if (list[idx].phase === 'holding') {
      return res.status(409).json({ error: '보유 중인 포지션이 있습니다 — 전체정지 킬스위치로 청산 후 삭제하세요' });
    }
    list.splice(idx, 1);
    const ok = await writeGistFile(gistId, ghToken, FILENAME, list);
    return res.status(ok ? 200 : 500).json(ok ? { ok: true } : { error: '삭제 실패' });
  }

  return res.status(405).json({ error: 'Method not allowed' });
}

// ══════════════════════════════════════════════════════════
// 초단타 전체 정지 킬스위치 — coin_enabled/stock_enabled=false 시
// daemon_scalp.py가 신규 진입을 멈추고 보유 포지션을 즉시 시장가 청산
// ══════════════════════════════════════════════════════════
async function handleScalpControl(req, res, gistId, ghToken) {
  const FILENAME = 'scalp_control.json';
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'GET') {
    const ctrl = await readGistFile(gistId, ghToken, FILENAME);
    const base = (ctrl && typeof ctrl === 'object' && !Array.isArray(ctrl)) ? ctrl : {};
    return res.status(200).json({ coin_enabled: true, stock_enabled: true, ...base });
  }

  if (req.method === 'POST' || req.method === 'PATCH') {
    const ctrl = await readGistFile(gistId, ghToken, FILENAME);
    const base = (ctrl && typeof ctrl === 'object' && !Array.isArray(ctrl)) ? ctrl : {};
    const merged = { coin_enabled: true, stock_enabled: true, ...base, ...(req.body || {}) };
    const ok = await writeGistFile(gistId, ghToken, FILENAME, merged);
    return res.status(ok ? 200 : 500).json(ok ? merged : { error: '저장 실패' });
  }

  return res.status(405).json({ error: 'Method not allowed' });
}

// ══════════════════════════════════════════════════════════
// 초단타 자동 종목 발굴 설정 — coin/stock 각각 enabled 시
// daemon_scalp.py가 시장 전체를 스캔해 급등 후보를 watching 잡으로 자동 생성
// ══════════════════════════════════════════════════════════
const SCALP_AUTO_DEFAULTS = {
  enabled: false,
  entry_momentum_pct: 0.4,
  lookback_sec: 30,
  max_day_chg_pct: 5.0,
  take_profit_pct: 0.6,
  stop_loss_pct: 0.4,
  time_stop_sec: 180,
  time_stop_loss_pct: 0.5,
  max_concurrent: 2,
  max_daily_loss_krw: -30000,
  watch_timeout_sec: 300,
  discovery_momentum_sec: 60,
  min_discovery_momentum_pct: 0.4,
  min_volume_surge_ratio: 1.3,
  surge_enabled: true,
  reversal_enabled: false,
  decline_lookback_sec: 300,
  min_decline_pct: 2.0,
  rebound_lookback_sec: 30,
  min_rebound_pct: 0.4,
  fast_rise_momentum_pct: 0,       // 0=비활성. 보유 중 lookback_sec 모멘텀이 이 값 이상이면 급등으로 판단
  fast_rise_take_profit_pct: 0,    // 급등 판단 시 take_profit_pct 대신 적용할 상향 목표
  fast_decline_momentum_pct: 0,    // 0=비활성. 보유 중 모멘텀이 이 값의 음수 이하면 급락으로 판단
  fast_decline_stop_loss_pct: 0,   // 급락 판단 시 stop_loss_pct 대신 적용할 축소(타이트) 손절 기준
  retry_cooldown_sec: 0,           // 0=당일 1회만 시도(기존 동작). >0이면 이 시간(초) 지나면 같은 티커 재시도 허용
  max_spread_pct: 0,               // 0=비활성. 매수/매도 1호가 스프레드가 이 값을 넘는 후보는 제외
};

async function handleScalpAutoConfig(req, res, gistId, ghToken) {
  const FILENAME = 'scalp_auto_config.json';
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'GET') {
    const cfg = await readGistFile(gistId, ghToken, FILENAME);
    const base = (cfg && typeof cfg === 'object' && !Array.isArray(cfg)) ? cfg : {};
    return res.status(200).json({
      coin:  { ...SCALP_AUTO_DEFAULTS, krw_amount: 10000, min_liquidity: 50_000_000, ...(base.coin || {}) },
      stock: { ...SCALP_AUTO_DEFAULTS, amount: 500000,   min_liquidity: 100_000_000, ...(base.stock || {}) },
    });
  }

  if (req.method === 'POST' || req.method === 'PATCH') {
    const { market, ...fields } = req.body || {};
    if (market !== 'coin' && market !== 'stock') return res.status(400).json({ error: "market은 'coin' 또는 'stock'이어야 합니다" });
    const cfg = await readGistFile(gistId, ghToken, FILENAME);
    const base = (cfg && typeof cfg === 'object' && !Array.isArray(cfg)) ? cfg : {};
    const merged = { ...base, [market]: { ...(base[market] || {}), ...fields } };
    const ok = await writeGistFile(gistId, ghToken, FILENAME, merged);
    return res.status(ok ? 200 : 500).json(ok ? merged : { error: '저장 실패' });
  }

  return res.status(405).json({ error: 'Method not allowed' });
}

// ══════════════════════════════════════════════════════════
// 일별 코인 거래 수익 집계
// GET /api/coin-today         → 오늘(KST) 거래 집계
// GET /api/coin-date?date=    → 특정 날짜(YYYY-MM-DD) 거래 집계
// ══════════════════════════════════════════════════════════
async function handleCoinDate(req, res, gistId, ghToken) {
  const { date } = req.query;
  const kstNow    = new Date(Date.now() + 9 * 3600000);
  const targetDate = date || kstNow.toISOString().slice(0, 10);

  const FEE = 0.0005; // 업비트 수수료 0.05% (매수·매도 각각)

  const [buyJobs, sellJobs, gridJobs, scalpJobs] = await Promise.all([
    readGistFile(gistId, ghToken, 'coin_buy_jobs.json'),
    readGistFile(gistId, ghToken, 'coin_sell_jobs.json'),
    readGistFile(gistId, ghToken, 'coin_grid_jobs.json'),
    readGistFile(gistId, ghToken, 'scalp_coin_jobs.json'),
  ]);

  // ── 매수잡 당일 체결 (미매도 포지션 — 손익 미확정)
  const buys = (Array.isArray(buyJobs) ? buyJobs : [])
    .filter(j => j.status === 'done' && j.executed_at?.slice(0, 10) === targetDate)
    .map(j => {
      const qty    = j.exec_qty || j.qty || 0;
      const price  = j.exec_price || 0;
      const amount = Math.round(price * qty);
      return { time: j.executed_at.slice(11, 16), name: j.name || j.ticker,
               ticker: j.ticker, qty, buyPrice: price, amount,
               fee: Math.round(amount * FEE), profit: null };
    });

  // ── 매도잡 당일 체결 → buy_price 기반 실현손익
  //    매수가 전일이어도 sell job에 buy_price가 저장되어 있으므로 정확한 손익 계산 가능
  const sells = (Array.isArray(sellJobs) ? sellJobs : [])
    .filter(j => j.status === 'done' && j.executed_at?.slice(0, 10) === targetDate)
    .map(j => {
      const qty       = j.qty || 0;
      const sellPrice = j.exec_price || 0;
      const buyPrice  = j.buy_price  || 0;
      const sellNet   = sellPrice * qty * (1 - FEE);
      const buyCost   = buyPrice  * qty * (1 + FEE);
      // buy_price=0이면 수익 계산 불가 → null로 처리 (netProfit 합산 제외)
      const profit    = buyPrice > 0 ? Math.round(sellNet - buyCost) : null;
      const fee       = Math.round((sellPrice + buyPrice) * qty * FEE);
      return { time: j.executed_at.slice(11, 16), buyTime: null,
               name: j.name || j.ticker,
               ticker: j.ticker, qty, buyPrice, sellPrice,
               amount: Math.round(sellPrice * qty), fee, profit };
    });

  // ── 코인 그리드 당일 체결 내역 (trade_history 배열)
  const gridSells = (Array.isArray(gridJobs) ? gridJobs : []).flatMap(job => {
    const hist = Array.isArray(job.trade_history) ? job.trade_history : [];
    return hist
      .filter(h => h.date === targetDate)
      .map(h => ({
        time:      (h.time || '').slice(0, 5),
        buyTime:   (h.buy_time || '').slice(0, 5) || null,
        name:      job.name || job.ticker,
        ticker:    job.ticker,
        qty:       h.qty,
        buyPrice:  h.buy_price,
        sellPrice: h.sell_price,
        amount:    Math.round(h.sell_price * h.qty),
        fee:       0,
        // buy_price=0이면 Oracle VM이 잘못 계산한 값 → null로 덮어씀
        profit:    h.buy_price ? Math.round(h.profit) : null,
        source:    'grid',
      }));
  });

  // ── 초단타(스캘핑) 당일 체결 내역 (trade_log 배열)
  const scalpSells = (Array.isArray(scalpJobs) ? scalpJobs : []).flatMap(job => {
    const log = Array.isArray(job.trade_log) ? job.trade_log : [];
    return log
      .filter(t => t.date === targetDate)
      .map(t => ({
        time:      (t.time || '').slice(0, 5),
        buyTime:   null,
        name:      job.name || job.ticker,
        ticker:    job.ticker,
        qty:       t.qty,
        buyPrice:  t.buy_price,
        sellPrice: t.sell_price,
        amount:    Math.round((t.sell_price || 0) * (t.qty || 0)),
        fee:       0,
        profit:    t.buy_price ? Math.round(t.pnl) : null,
        source:    'scalp',
      }));
  });

  const allBuys  = [...buys].sort((a, b) => a.time.localeCompare(b.time));
  const allSells = [...sells, ...gridSells, ...scalpSells].sort((a, b) => a.time.localeCompare(b.time));

  // 실현손익 = 당일 체결된 매도의 (매도 - 매수) 손익 합계
  const netProfit = Math.round(allSells.reduce((s, o) => s + (o.profit || 0), 0));
  const totalFee  = Math.round([...allBuys, ...allSells].reduce((s, o) => s + (o.fee || 0), 0));

  res.setHeader('Cache-Control', 'no-store');
  return res.status(200).json({
    date: targetDate,
    netProfit,
    totalFee,
    buys:             allBuys,
    sells:            allSells,
    pendingBuyCnt:    allBuys.length,   // 당일 매수 후 아직 미매도
    carryoverSellCnt: 0,
  });
}

async function handleCoinPrice(req, res) {
  const { markets } = req.query;
  if (!markets) return res.status(400).json({ error: 'markets 파라미터 필요' });
  try {
    const r = await fetch(`https://api.upbit.com/v1/ticker?markets=${encodeURIComponent(markets)}`);
    if (!r.ok) return res.status(r.status).json({ error: 'Upbit API 오류' });
    const data = await r.json();
    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json(data);
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}

async function handleCoinIp(res) {
  try {
    const r = await fetch('https://api.ipify.org?format=json');
    const d = await r.json();
    return res.status(200).json({
      ip: d.ip,
      message: '이 IP를 업비트 API 키에 등록하세요',
      guide: '업비트 → 마이페이지 → Open API 관리 → API 키 생성 → IP 주소',
    });
  } catch (e) {
    return res.status(500).json({ error: 'IP 조회 실패', detail: e.message });
  }
}

// ══════════════════════════════════════════════════════════
// 메인 핸들러 — URL 기반 라우팅
// ══════════════════════════════════════════════════════════
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const url     = req.url || '';
  const gistId  = process.env.GIST_ID;
  const ghToken = process.env.GH_TOKEN;
  if (!gistId || !ghToken) return res.status(500).json({ error: 'GIST_ID / GH_TOKEN 미설정' });

  if (url.includes('coin-runner')) return handleCoinRunner(req, res, gistId, ghToken);
  if (url.includes('coin-ip'))     return handleCoinIp(res);
  if (url.includes('coin-price'))  return handleCoinPrice(req, res);
  if (url.includes('coin-grid'))   return handleCoinGrid(req, res, gistId, ghToken);
  if (url.includes('coin-today') || url.includes('coin-date')) return handleCoinDate(req, res, gistId, ghToken);
  if (url.includes('coin-'))       return handleCoinJobs(req, res, url, gistId, ghToken);
  if (url.includes('scalp-auto-config')) return handleScalpAutoConfig(req, res, gistId, ghToken);
  if (url.includes('scalp-control')) return handleScalpControl(req, res, gistId, ghToken);
  if (url.includes('scalp-coin') || url.includes('scalp-stock')) return handleScalpJobs(req, res, url, gistId, ghToken);
  if (url.includes('profit-'))     return handleStockJobs(req, res, url, gistId, ghToken);
  if (url.includes('stock-grid'))  return handleStockGridJobs(req, res, gistId, ghToken);

  return res.status(404).json({ error: '알 수 없는 경로' });
}
