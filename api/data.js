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
// PAPER_TRADE 환경변수와 실제 KIS_APP_KEY 종류(실전/모의)가 어긋나는 사고가 반복돼서
// ("실전투자 도메인은 모의투자 앱키로 호출하실 수 없습니다" 500 에러로 시스템 트레이딩
// 내역이 통째로 안 나오던 문제, 2026-08 여러 차례) — 도메인-앱키 불일치 에러를 감지하면
// 반대 도메인으로 자동 전환 후 1회 재시도하도록 방어적으로 처리(2026-08-15).
const REAL_BASE  = 'https://openapi.koreainvestment.com:9443';
const PAPER_BASE = 'https://openapivts.koreainvestment.com:29443';
let _kisBase = (process.env.PAPER_TRADE || '').toLowerCase() === 'true' ? PAPER_BASE : REAL_BASE;

function _isKisDomainMismatch(text) {
  return typeof text === 'string' && (text.includes('EGW02004') || text.includes('모의투자 앱키') || text.includes('실전투자 앱키'));
}

function _flipKisBase() {
  _kisBase = _kisBase === REAL_BASE ? PAPER_BASE : REAL_BASE;
  _tokenCache = null; // 도메인이 바뀌면 이전 토큰은 새 도메인에서 무효
}

async function getKisToken(appKey, appSecret) {
  const now = Date.now();
  if (_tokenCache && _tokenCache.base === _kisBase && _tokenCache.expires > now + 60_000) return _tokenCache.token;
  const r = await fetch(`${_kisBase}/oauth2/tokenP`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ grant_type: 'client_credentials', appkey: appKey, appsecret: appSecret }),
  });
  if (!r.ok) {
    const errText = await r.text().catch(() => '');
    throw new Error(`KIS 토큰 발급 실패: ${r.status} ${errText}`);
  }
  const d = await r.json();
  _tokenCache = { token: d.access_token, expires: now + (d.expires_in ? d.expires_in * 1000 : 86_400_000), base: _kisBase };
  return _tokenCache.token;
}

async function getKisPendingOrders(token, appKey, appSecret, cano, acntPrdtCd) {
  const params = new URLSearchParams({
    CANO: cano, ACNT_PRDT_CD: acntPrdtCd,
    CTX_AREA_FK200: '', CTX_AREA_NK200: '',
    INQR_DVSN_1: '0', INQR_DVSN_2: '0',
  });
  const trId = _kisBase === PAPER_BASE ? 'VTTC8036R' : 'TTTC8036R';
  const r = await fetch(`${_kisBase}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl?${params}`, {
    headers: { 'content-type': 'application/json; charset=utf-8', authorization: `Bearer ${token}`, appkey: appKey, appsecret: appSecret, tr_id: trId, custtype: 'P' },
  });
  if (!r.ok) return [];
  const data = await r.json();
  if (data.rt_cd !== '0') return [];
  return (data.output || []).map(o => ({
    ticker: o.pdno, name: o.prdt_name,
    side: o.sll_buy_dvsn_cd === '02' ? 'BUY' : 'SELL',
    qty: Number(o.ord_qty || 0), filled: Number(o.tot_ccld_qty || 0), price: Number(o.ord_unpr || 0),
  }));
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
      const params = new URLSearchParams({
        CANO: cano, ACNT_PRDT_CD: acntPrdtCd,
        AFHR_FLPR_YN: 'N', OFL_YN: '', INQR_DVSN: '02', UNPR_DVSN: '01',
        FUND_STTL_ICLD_YN: 'N', FNCG_AMT_AUTO_RDPT_YN: 'N', PRCS_DVSN: '01',
        CTX_AREA_FK100: '', CTX_AREA_NK100: '',
      });

      // PAPER_TRADE 설정과 실제 앱키 종류가 어긋나 있으면(EGW02004) 반대 도메인으로
      // 전환해서 딱 1번 더 시도 — 환경변수 설정 실수로 대시보드 전체가 안 나오는
      // 사고가 반복돼서 코드 레벨에서 자동 복구되도록 방어.
      let token, r, data;
      for (let attempt = 0; attempt < 2; attempt++) {
        token = await getKisToken(appKey, appSecret);
        const balTrId = _kisBase === PAPER_BASE ? 'VTTC8434R' : 'TTTC8434R';
        r = await fetch(`${_kisBase}/uapi/domestic-stock/v1/trading/inquire-balance?${params}`, {
          headers: { 'content-type': 'application/json; charset=utf-8', authorization: `Bearer ${token}`, appkey: appKey, appsecret: appSecret, tr_id: balTrId, custtype: 'P' },
        });
        if (!r.ok) {
          const err = await r.text();
          if (attempt === 0 && _isKisDomainMismatch(err)) { _flipKisBase(); continue; }
          return res.status(r.status).json({ error: `KIS API 오류: ${r.status}`, detail: err });
        }
        data = await r.json();
        if (data.rt_cd !== '0') {
          if (attempt === 0 && _isKisDomainMismatch(data.msg1)) { _flipKisBase(); continue; }
          return res.status(400).json({ error: data.msg1 || 'KIS 조회 오류', code: data.rt_cd });
        }
        break;
      }
      const summary = data.output2?.[0] || {};
      const now = new Date();
      const updatedAt = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')} ${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
      const holdings = (data.output1 || []).filter(h => Number(h.hldg_qty) > 0).map(h => ({
        ticker: h.pdno, name: h.prdt_name, qty: Number(h.hldg_qty),
        avg_price: Math.round(Number(h.pchs_avg_pric)), eval_price: Number(h.prpr),
        pnl_pct: Number(h.evlu_pfls_rt), eval_amt: Number(h.evlu_amt), buy_amt: Number(h.pchs_amt),
        bfdy_close_diff: Number(h.bfdy_cprs_icdc || 0),
      }));
      // 당일손익 = (보유종목의 전일종가 대비 평가변동) + (오늘 완료된 그리드
      // 매매 실현손익). 총자산평가금액 단순 비교(전일 대비) 방식은 당일
      // 입출금까지 손익으로 잡아버리는 문제가 있어(실측: 10만원 입금이
      // 그대로 손익에 섞임) 폐기 — 가격 변동/체결 기반이라 입출금과 무관.
      const totalEval = Number(summary.tot_evlu_amt || 0);
      const bfdyTotalEval = Number(summary.bfdy_tot_asst_evlu_amt || 0);
      const unrealizedDay = holdings.reduce((sum, h) => sum + h.bfdy_close_diff * h.qty, 0);
      let realizedToday = 0;
      try {
        const todayStr = updatedAt.slice(0, 10);
        const gridGist = await fetch(`https://api.github.com/gists/${gistId}`, { headers: ghHeaders }).then(r => r.json());
        const gridFile = gridGist.files?.['stock_grid_jobs.json'];
        const gridJobs = gridFile ? JSON.parse(gridFile.content || '[]') : [];
        for (const gj of gridJobs) {
          for (const t of (gj.trade_history || [])) {
            if (t.date === todayStr) realizedToday += Number(t.profit || 0);
          }
        }
      } catch (_) { /* 실현손익 집계 실패해도 미실현분은 반환 */ }
      const dayPnl = Math.round(unrealizedDay + realizedToday);
      let pendingOrders = [];
      try { pendingOrders = await getKisPendingOrders(token, appKey, appSecret, cano, acntPrdtCd); } catch (_) {}
      const account_balance = {
        updated_at: updatedAt,
        cash: Number(summary.dnca_tot_amt || 0),
        total_eval: totalEval,
        day_pnl: dayPnl,
        day_ret: bfdyTotalEval ? Number((dayPnl / bfdyTotalEval * 100).toFixed(2)) : 0,
        holdings,
        pending_orders: pendingOrders,
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

  // ── Gist trader_trades만 (대시보드 거래내역 카드 폴링용) ──────
  if (mode === 'trades') {
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
      const file = (gist.files || {})['trader_trades.json'];
      const data = file ? JSON.parse(file.content || '[]') : [];
      res.setHeader('Cache-Control', 'no-store');
      return res.status(200).json({ trader_trades: data });
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
