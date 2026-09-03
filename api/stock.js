/**
 * Vercel API — 개별주 검색 + 현재가 통합 엔드포인트
 * GET /api/stock?q={name_or_ticker}      → 종목명 검색 { items: [{name, ticker}] }
 * GET /api/stock?ticker={ticker}          → 현재가 조회 { ticker, name, price, chg, chgPct }
 */

import { getSharedKisToken } from './_kis-token.js';

// NXT 시간대 판단 (프리마켓 08:05~08:50, 애프터마켓 15:35~20:00 KST, 평일만)
function isNxtTime() {
  const kst = new Date(Date.now() + 9 * 3600 * 1000);
  const day = kst.getUTCDay();
  if (day === 0 || day === 6) return false;
  const t = kst.getUTCHours() * 60 + kst.getUTCMinutes();
  return (t >= 8 * 60 + 5 && t < 8 * 60 + 50) || (t >= 15 * 60 + 35 && t < 20 * 60);
}

// NXT 실시간 현재가 조회 — NXT 비대상 종목이면 null 반환
async function getNxtPrice(ticker) {
  const appKey    = process.env.KIS_APP_KEY;
  const appSecret = process.env.KIS_APP_SECRET;
  if (!appKey || !appSecret) return null;
  try {
    // 2026-09-03: 이 함수 자체 발급 대신 Oracle(coin-runner)의 공유 토큰
    // 캐시를 우선 재사용 — 배경은 _kis-token.js 참고
    const { token, base } = await getSharedKisToken(appKey, appSecret);
    const params = new URLSearchParams({ FID_COND_MRKT_DIV_CODE: 'NX', FID_INPUT_ISCD: ticker });
    const doFetch = () => fetch(
      `${base}/uapi/domestic-stock/v1/quotations/inquire-price?${params}`,
      {
        headers: {
          'content-type': 'application/json; charset=utf-8',
          authorization: `Bearer ${token}`,
          appkey: appKey,
          appsecret: appSecret,
          tr_id: 'FHKST01010100',
          custtype: 'P',
        },
      }
    );
    let r = await doFetch();
    if (!r.ok) {
      // KIS 초당 호출 한도에 걸린 순간적 실패일 수 있음(api/quote.js 분봉
      // 조회에서 이미 확인된 패턴) — 한 번 더 쉬고 재시도. 여기서 포기하면
      // 이 함수가 null을 반환해 그리드 카드가 NXT 시세 대신 Naver의 정규장
      // 종가로 조용히 폴백해버려(2026-08-25 사용자 리포트: 계좌 카드는
      // 79,900원인데 그리드 카드는 정규장 종가 80,700원으로 다르게 표시)
      // 재시도 없이는 산발적으로 재발할 수 있음.
      await new Promise(res => setTimeout(res, 400));
      r = await doFetch();
      if (!r.ok) return null;
    }
    const d = await r.json();
    if (d.rt_cd !== '0') return null;
    const o = d.output;
    const price = Number(o.stck_prpr) || 0;
    if (!price) return null;
    return { price, chg: Number(o.prdy_vrss), chgPct: Number(o.prdy_ctrt), name: o.hts_kor_isnm || '' };
  } catch {
    return null;
  }
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'GET') return res.status(405).json({ error: 'GET only' });

  const ua = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Referer': 'https://m.stock.naver.com/',
    'Accept': 'application/json, */*',
  };

  // ── 종목명 검색 ─────────────────────────────────────────
  const q = (req.query.q || '').trim();
  if (q) {
    try {
      const url = `https://ac.stock.naver.com/ac?q=${encodeURIComponent(q)}&target=stock&lang=ko`;
      const r = await fetch(url, { headers: ua });
      if (!r.ok) throw new Error(`Naver AC ${r.status}`);
      const data = await r.json();

      // 응답: { items: [{ code, name, typeCode, ... }, ...] }
      const items = (data.items || []).slice(0, 10).map(it => ({
        name:   it.name   || '',
        ticker: it.code   || '',
        market: it.typeName || '',
      })).filter(it => it.name && it.ticker && /^\d{6}$/.test(it.ticker));

      res.setHeader('Cache-Control', 's-maxage=60');
      return res.status(200).json({ items });
    } catch (e) {
      return res.status(500).json({ error: e.message, items: [] });
    }
  }

  // ── 현재가 조회 ─────────────────────────────────────────
  const ticker = (req.query.ticker || '').trim().replace(/^A/, '');
  if (ticker) {
    if (!/^\d{6}$/.test(ticker)) {
      return res.status(400).json({ error: '6자리 숫자 종목코드를 입력하세요' });
    }

    // NXT 시간대: KIS NXT 현재가 우선 조회 (NXT 거래 종목이면 실시간가 반환)
    if (isNxtTime()) {
      const nxt = await getNxtPrice(ticker);
      if (nxt) {
        res.setHeader('Cache-Control', 'no-store');
        return res.status(200).json({ ticker, name: nxt.name, price: nxt.price, chg: nxt.chg, chgPct: nxt.chgPct, nxt: true });
      }
    }

    try {
      const url = `https://m.stock.naver.com/api/stock/${ticker}/basic`;
      const r = await fetch(url, { headers: ua });
      if (!r.ok) throw new Error(`Naver API ${r.status}`);
      const d = await r.json();

      const price  = Number(String(d.closePrice                  ?? '0').replace(/,/g, ''));
      const chg    = Number(String(d.compareToPreviousClosePrice ?? '0').replace(/,/g, ''));
      const chgPct = Number(String(d.fluctuationsRatio           ?? '0').replace(/,/g, ''));
      const name   = d.stockName ?? d.corporateName ?? '';

      if (!price) throw new Error('가격 정보 없음');

      res.setHeader('Cache-Control', 'no-store');
      return res.status(200).json({ ticker, name, price, chg, chgPct });
    } catch (e) {
      return res.status(500).json({ error: e.message, ticker });
    }
  }

  return res.status(400).json({ error: 'q 또는 ticker 파라미터 필요' });
}
