/**
 * Vercel API — GitHub Gist에서 분석 결과 읽기 / portfolio_meta 저장
 * GET  /api/data             → { briefing, picks, signals, ipo, portfolio_meta, account_balance }
 * GET  /api/data?mode=account      → Gist account_balance만 반환
 * GET  /api/data?mode=kisbalance   → KIS 실시간 잔고 조회 후 Gist 업데이트
 * POST /api/data  body: { portfolio_meta: { cash: 1000000 } }  → JSONBin 저장
 */

import { readBin, writeBin } from './_jsonbin.js';

// ── Gist 전체 읽기 캐시 (warm 인스턴스 간 재사용, 30초 TTL) ──
let _gistCache   = null;
let _gistCacheAt = 0;
const GIST_TTL   = 300_000; // 5분 (프론트 _fetchGistData TTL과 동일)

// ── KIS 토큰 캐시 (warm 인스턴스 간 재사용) ─────────────────
let _tokenCache = null;

// 실계좌: openapi.koreainvestment.com:9443
// 모의투자: openapivts.koreainvestment.com:29443 (PAPER_TRADE=true 시)
const KIS_BASE = (process.env.PAPER_TRADE || '').toLowerCase() === 'true'
  ? 'https://openapivts.koreainvestment.com:29443'
  : 'https://openapi.koreainvestment.com:9443';

async function getKisToken(appKey, appSecret) {
  const now = Date.now();
  if (_tokenCache && _tokenCache.expires > now + 60_000) return _tokenCache.token;
  const r = await fetch(`${KIS_BASE}/oauth2/tokenP`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ grant_type: 'client_credentials', appkey: appKey, appsecret: appSecret }),
  });
  if (!r.ok) throw new Error(`KIS 토큰 발급 실패: ${r.status}`);
  const d = await r.json();
  _tokenCache = { token: d.access_token, expires: now + (d.expires_in ? d.expires_in * 1000 : 86_400_000) };
  return _tokenCache.token;
}

async function updateGist(gistId, ghToken, ghHeaders, data) {
  try {
    await fetch(`https://api.github.com/gists/${gistId}`, {
      method: 'PATCH',
      headers: { ...ghHeaders, 'Content-Type': 'application/json' },
      body: JSON.stringify({ files: { 'account_balance.json': { content: JSON.stringify(data, null, 2) } } }),
    });
  } catch (_) {}
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const gistId  = process.env.GIST_ID;
  const ghToken = process.env.GH_TOKEN;
  const ghHeaders = {
    Accept: 'application/vnd.github+json',
    'User-Agent': 'stock-analyzer',
    ...(ghToken ? { Authorization: `Bearer ${ghToken}` } : {}),
  };

  if (!gistId) return res.status(500).json({ error: 'GIST_ID not configured' });

  const mode = req.query.mode || '';

  // ── 관심종목 (watchlist) GET / POST ──────────────────────
  if (mode === 'watchlist') {
    if (req.method === 'GET') {
      try {
        let gist;
        const now = Date.now();
        if (_gistCache && now - _gistCacheAt < GIST_TTL) {
          gist = _gistCache;
        } else {
          const r = await fetch(`https://api.github.com/gists/${gistId}`, { headers: ghHeaders });
          if (!r.ok) throw new Error(`GitHub API ${r.status}`);
          gist = await r.json();
          _gistCache = gist; _gistCacheAt = now;
        }
        const file   = gist.files?.['watchlist.json'];
        const stocks = file ? JSON.parse(file.content) : [];
        res.setHeader('Cache-Control', 's-maxage=30, stale-while-revalidate=60');
        return res.status(200).json({ stocks: Array.isArray(stocks) ? stocks : [] });
      } catch (e) {
        return res.status(500).json({ error: e.message });
      }
    }
    if (req.method === 'POST') {
      const { stocks } = req.body || {};
      if (!Array.isArray(stocks)) return res.status(400).json({ error: 'stocks 배열 필요' });
      const clean = stocks
        .filter(s => s?.ticker && s?.name)
        .map(s => ({ ticker: String(s.ticker).trim(), name: String(s.name).trim() }));
      try {
        await fetch(`https://api.github.com/gists/${gistId}`, {
          method: 'PATCH',
          headers: { ...ghHeaders, 'Content-Type': 'application/json' },
          body: JSON.stringify({ files: { 'watchlist.json': { content: JSON.stringify(clean, null, 2) } } }),
        });
        _gistCache = null; _gistCacheAt = 0;
        return res.status(200).json({ ok: true, count: clean.length });
      } catch (e) {
        return res.status(500).json({ error: e.message });
      }
    }
  }

  // ── 코인 runner 트리거 (coin-runner 서버사이드 호출) ─────────
  if (mode === 'trigger_coin_runner' || mode === 'trigger_coin_balance') {
    const host       = req.headers['x-forwarded-host'] || req.headers.host || '';
    const proto      = req.headers['x-forwarded-proto'] || 'https';
    const coinSecret = process.env.COIN_RUNNER_SECRET || '';
    const runnerMode = req.query.runner_mode || (mode === 'trigger_coin_balance' ? 'balance' : 'all');
    const qs         = `mode=${runnerMode}${coinSecret ? `&secret=${encodeURIComponent(coinSecret)}` : ''}`;
    fetch(`${proto}://${host}/api/coin-runner?${qs}`).catch(() => {});
    return res.status(200).json({ ok: true, triggered: runnerMode });
  }

  // ── GitHub Actions balance job 트리거 ────────────────────
  if (mode === 'trigger_balance') {
    if (!ghToken) return res.status(500).json({ error: 'GH_TOKEN 미설정' });
    try {
      const r = await fetch(
        'https://api.github.com/repos/asakasimo1/stock-trader/actions/workflows/trader.yml/dispatches',
        {
          method: 'POST',
          headers: {
            Accept: 'application/vnd.github+json',
            'User-Agent': 'stock-analyzer',
            Authorization: `Bearer ${ghToken}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ ref: 'main', inputs: { job: 'balance' } }),
        }
      );
      if (!r.ok) {
        const err = await r.text();
        return res.status(r.status).json({ error: `GitHub API 오류: ${r.status}`, detail: err });
      }
      return res.status(200).json({ ok: true });
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }

  // ── KIS 실시간 잔고 ────────────────────────────────────────
  if (mode === 'kisbalance') {
    const appKey     = process.env.KIS_APP_KEY;
    const appSecret  = process.env.KIS_APP_SECRET;
    const cano       = process.env.KIS_CANO;
    const acntPrdtCd = process.env.KIS_ACNT_PRDT_CD;
    if (!appKey || !appSecret || !cano || !acntPrdtCd)
      return res.status(500).json({ error: 'KIS 환경변수 미설정' });
    try {
      const token = await getKisToken(appKey, appSecret);
      const params = new URLSearchParams({
        CANO: cano, ACNT_PRDT_CD: acntPrdtCd,
        AFHR_FLPR_YN: 'N', OFL_YN: '', INQR_DVSN: '02', UNPR_DVSN: '01',
        FUND_STTL_ICLD_YN: 'N', FNCG_AMT_AUTO_RDPT_YN: 'N', PRCS_DVSN: '01',
        CTX_AREA_FK100: '', CTX_AREA_NK100: '',
      });
      const balTrId = KIS_BASE.includes('vts') ? 'VTTC8434R' : 'TTTC8434R';
      const r = await fetch(`${KIS_BASE}/uapi/domestic-stock/v1/trading/inquire-balance?${params}`, {
        headers: { 'content-type': 'application/json; charset=utf-8', authorization: `Bearer ${token}`, appkey: appKey, appsecret: appSecret, tr_id: balTrId, custtype: 'P' },
      });
      if (!r.ok) { const err = await r.text(); return res.status(r.status).json({ error: `KIS API 오류: ${r.status}`, detail: err }); }
      const data = await r.json();
      if (data.rt_cd !== '0') return res.status(400).json({ error: data.msg1 || 'KIS 조회 오류', code: data.rt_cd });
      const summary = data.output2?.[0] || {};
      const now = new Date();
      const updatedAt = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')} ${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
      const holdings = (data.output1 || []).filter(h => Number(h.hldg_qty) > 0).map(h => ({
        ticker: h.pdno, name: h.prdt_name, qty: Number(h.hldg_qty),
        avg_price: Math.round(Number(h.pchs_avg_pric)), eval_price: Number(h.prpr),
        pnl_pct: Number(h.evlu_pfls_rt), eval_amt: Number(h.evlu_amt), buy_amt: Number(h.pchs_amt),
        bfdy_close_diff: Number(h.bfdy_cprs_icdc || 0),
      }));
      // 당일손익 = 보유종목의 전일종가 대비 증감(원) × 수량 합계.
      // evlu_pfls_smtl_amt(총평가손익)는 매수 시점부터의 누적치라 "당일"과 다름 — 착오 수정.
      const dayPnl = holdings.reduce((sum, h) => sum + h.bfdy_close_diff * h.qty, 0);
      const bfdyTotalEval = Number(summary.bfdy_tot_asst_evlu_amt || 0);
      const account_balance = {
        updated_at: updatedAt,
        cash: Number(summary.dnca_tot_amt || 0),
        total_eval: Number(summary.tot_evlu_amt || 0),
        day_pnl: dayPnl,
        day_ret: bfdyTotalEval ? Number((dayPnl / bfdyTotalEval * 100).toFixed(2)) : 0,
        holdings,
      };
      if (gistId && ghToken) updateGist(gistId, ghToken, ghHeaders, account_balance);
      res.setHeader('Cache-Control', 'no-store');
      return res.status(200).json({ account_balance });
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }

  // ── Gist account_balance만 ────────────────────────────────
  if (mode === 'account') {
    try {
      let gist;
      const now = Date.now();
      if (_gistCache && now - _gistCacheAt < GIST_TTL) {
        gist = _gistCache;
      } else {
        const r = await fetch(`https://api.github.com/gists/${gistId}`, { headers: ghHeaders });
        if (!r.ok) return res.status(r.status).json({ error: `GitHub API error: ${r.status}` });
        gist = await r.json();
        _gistCache = gist; _gistCacheAt = now;
      }
      const file = (gist.files || {})['account_balance.json'];
      const data = file ? JSON.parse(file.content || 'null') : null;
      res.setHeader('Cache-Control', 'no-store');
      return res.status(200).json({ account_balance: data });
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }

  // ── POST: portfolio_meta 저장 (JSONBin) ──────────────────
  if (req.method === 'POST') {
    const jsonbinKey   = process.env.JSONBIN_KEY;
    const jsonbinBinId = process.env.JSONBIN_BIN_ID;
    if (!jsonbinKey || !jsonbinBinId) return res.status(500).json({ error: 'JSONBIN 환경변수 미설정' });
    try {
      const meta    = (req.body || {}).portfolio_meta || req.body || {};
      const binData = await readBin(jsonbinBinId, jsonbinKey, true);
      const merged  = { ...binData, portfolio_meta: { ...(binData.portfolio_meta || {}), ...meta } };
      await writeBin(jsonbinBinId, jsonbinKey, merged);
      _gistCache = null; _gistCacheAt = 0;
      return res.status(200).json({ ok: true });
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }

  // ── GET: 전체 데이터 읽기 ─────────────────────────────────
  try {
    let gist;
    const now = Date.now();
    if (_gistCache && now - _gistCacheAt < GIST_TTL) {
      gist = _gistCache;
    } else {
      const r = await fetch(`https://api.github.com/gists/${gistId}`, { headers: ghHeaders });
      if (!r.ok) return res.status(r.status).json({ error: `GitHub API error: ${r.status}` });
      gist = await r.json();
      _gistCache   = gist;
      _gistCacheAt = now;
    }
    const files = gist.files || {};
    const result = { briefing: [], picks: [], signals: [], ipo: [], portfolio_meta: {}, trader_trades: [], account_balance: null };
    for (const [key, fileObj] of Object.entries(files)) {
      try {
        const data = JSON.parse(fileObj.content || 'null');
        if (key === 'briefing.json')         result.briefing         = data || [];
        if (key === 'picks.json')            result.picks            = data || [];
        if (key === 'signals.json')          result.signals          = data || [];
        if (key === 'ipo.json')              result.ipo              = data || [];
        if (key === 'portfolio_meta.json')   result.portfolio_meta   = data || {};
        if (key === 'trader_trades.json')    result.trader_trades    = data || [];
        if (key === 'account_balance.json')  result.account_balance  = data || null;
      } catch (_) {}
    }
    // JSONBin portfolio_meta 오버레이 (Gist 쓰기 권한 없는 경우 대비)
    try {
      const jbKey   = process.env.JSONBIN_KEY;
      const jbBinId = process.env.JSONBIN_BIN_ID;
      if (jbKey && jbBinId) {
        const binData = await readBin(jbBinId, jbKey);
        if (binData.portfolio_meta) {
          result.portfolio_meta = { ...result.portfolio_meta, ...binData.portfolio_meta };
        }
      }
    } catch (_) {}
    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json(result);
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
