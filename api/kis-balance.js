/**
 * Vercel API — KIS 실시간 잔고 조회
 * GET /api/kis-balance → { account_balance }
 * - 총평가금액, 예수금, 보유종목 실시간 반환
 * - 조회 후 Gist account_balance.json 자동 업데이트
 */

const BASE = 'https://openapi.kis.or.kr';

// Vercel warm 인스턴스 간 토큰 재사용 (cold start 시 재발급)
let _tokenCache = null; // { token, expires }

async function getToken(appKey, appSecret) {
  const now = Date.now();
  if (_tokenCache && _tokenCache.expires > now + 60_000) return _tokenCache.token;

  const r = await fetch(`${BASE}/oauth2/tokenP`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      grant_type: 'client_credentials',
      appkey: appKey,
      appsecret: appSecret,
    }),
  });
  if (!r.ok) throw new Error(`KIS 토큰 발급 실패: ${r.status}`);
  const d = await r.json();
  _tokenCache = {
    token: d.access_token,
    expires: now + (d.expires_in ? d.expires_in * 1000 : 86_400_000),
  };
  return _tokenCache.token;
}

async function getPendingOrders(token, appKey, appSecret, cano, acntPrdtCd) {
  const params = new URLSearchParams({
    CANO: cano,
    ACNT_PRDT_CD: acntPrdtCd,
    CTX_AREA_FK200: '',
    CTX_AREA_NK200: '',
    INQR_DVSN_1: '0',
    INQR_DVSN_2: '0',
  });
  const r = await fetch(
    `${BASE}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl?${params}`,
    {
      headers: {
        'content-type': 'application/json; charset=utf-8',
        authorization: `Bearer ${token}`,
        appkey: appKey,
        appsecret: appSecret,
        tr_id: 'TTTC8036R',
        custtype: 'P',
      },
    }
  );
  if (!r.ok) return [];
  const data = await r.json();
  if (data.rt_cd !== '0') return [];
  return (data.output || []).map(o => ({
    ticker: o.pdno,
    name:   o.prdt_name,
    side:   o.sll_buy_dvsn_cd === '02' ? 'BUY' : 'SELL',
    qty:    Number(o.ord_qty || 0),
    filled: Number(o.tot_ccld_qty || 0),
    price:  Number(o.ord_unpr || 0),
  }));
}

async function updateGist(gistId, ghToken, accountBalance) {
  try {
    await fetch(`https://api.github.com/gists/${gistId}`, {
      method: 'PATCH',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${ghToken}`,
        'Content-Type': 'application/json',
        'User-Agent': 'stock-analyzer',
      },
      body: JSON.stringify({
        files: {
          'account_balance.json': {
            content: JSON.stringify(accountBalance, null, 2),
          },
        },
      }),
    });
  } catch (_) {/* Gist 업데이트 실패해도 응답은 정상 반환 */}
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const appKey    = process.env.KIS_APP_KEY;
  const appSecret = process.env.KIS_APP_SECRET;
  const cano      = process.env.KIS_CANO;
  const acntPrdtCd = process.env.KIS_ACNT_PRDT_CD;
  const gistId    = process.env.GIST_ID;
  const ghToken   = process.env.GH_TOKEN;

  if (!appKey || !appSecret || !cano || !acntPrdtCd) {
    return res.status(500).json({ error: 'KIS 환경변수 미설정' });
  }

  try {
    const token = await getToken(appKey, appSecret);

    const params = new URLSearchParams({
      CANO: cano,
      ACNT_PRDT_CD: acntPrdtCd,
      AFHR_FLPR_YN: 'N',
      OFL_YN: '',
      INQR_DVSN: '02',
      UNPR_DVSN: '01',
      FUND_STTL_ICLD_YN: 'N',
      FNCG_AMT_AUTO_RDPT_YN: 'N',
      PRCS_DVSN: '01',
      CTX_AREA_FK100: '',
      CTX_AREA_NK100: '',
    });

    const r = await fetch(
      `${BASE}/uapi/domestic-stock/v1/trading/inquire-balance?${params}`,
      {
        headers: {
          'content-type': 'application/json; charset=utf-8',
          authorization: `Bearer ${token}`,
          appkey: appKey,
          appsecret: appSecret,
          tr_id: 'TTTC8434R',
          custtype: 'P',
        },
      }
    );

    if (!r.ok) {
      const err = await r.text();
      return res.status(r.status).json({ error: `KIS API 오류: ${r.status}`, detail: err });
    }

    const data = await r.json();

    if (data.rt_cd !== '0') {
      return res.status(400).json({ error: data.msg1 || 'KIS 조회 오류', code: data.rt_cd });
    }

    const summary = data.output2?.[0] || {};
    const now = new Date();
    const updatedAt = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')} ${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;

    const holdings = (data.output1 || [])
      .filter(h => Number(h.hldg_qty) > 0)
      .map(h => ({
        ticker:      h.pdno,
        name:        h.prdt_name,
        qty:         Number(h.hldg_qty),
        avg_price:   Math.round(Number(h.pchs_avg_pric)),
        eval_price:  Number(h.prpr),
        pnl_pct:     Number(h.evlu_pfls_rt),
        eval_amt:    Number(h.evlu_amt),
        buy_amt:     Number(h.pchs_amt),
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
    if (gistId && ghToken) {
      try {
        const todayStr = updatedAt.slice(0, 10);
        const gh = { Accept: 'application/vnd.github+json', 'User-Agent': 'stock-analyzer', Authorization: `Bearer ${ghToken}` };
        const gridGist = await fetch(`https://api.github.com/gists/${gistId}`, { headers: gh }).then(r => r.json());
        const gridFile = gridGist.files?.['stock_grid_jobs.json'];
        const gridJobs = gridFile ? JSON.parse(gridFile.content || '[]') : [];
        for (const gj of gridJobs) {
          for (const t of (gj.trade_history || [])) {
            if (t.date === todayStr) realizedToday += Number(t.profit || 0);
          }
        }
      } catch (_) { /* 실현손익 집계 실패해도 미실현분은 반환 */ }
    }
    const dayPnl = Math.round(unrealizedDay + realizedToday);

    let pendingOrders = [];
    try {
      pendingOrders = await getPendingOrders(token, appKey, appSecret, cano, acntPrdtCd);
    } catch (_) { /* 미체결 조회 실패해도 잔고는 정상 반환 */ }

    const account_balance = {
      updated_at:  updatedAt,
      cash:        Number(summary.dnca_tot_amt || 0),
      total_eval:  totalEval,
      day_pnl:     dayPnl,
      day_ret:     bfdyTotalEval ? Number((dayPnl / bfdyTotalEval * 100).toFixed(2)) : 0,
      holdings,
      pending_orders: pendingOrders,
    };

    // Gist 비동기 업데이트 (응답 지연 없이)
    if (gistId && ghToken) {
      updateGist(gistId, ghToken, account_balance);
    }

    res.setHeader('Cache-Control', 'no-store');
    return res.status(200).json({ account_balance });

  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
