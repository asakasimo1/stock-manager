/**
 * Vercel API — ETF 현재가 + 배당 정보 조회 (Naver Mobile 프록시)
 * GET /api/quote?ticker=161510
 * Returns: { ticker, name, price, chg, chgPct, divCycle, divMonths, annualDiv, annualDivRate, recentDiv, recentDivRate }
 */

import { getSharedKisToken } from './_kis-token.js';

async function getKisPrice(ticker, appKey, appSecret, market = 'J') {
  // 2026-09-03: 이 함수 자체 발급 대신 Oracle(coin-runner)의 공유 토큰
  // 캐시를 우선 재사용(1분당 1회 발급 제한 충돌 회피) — 배경은
  // api/_kis-token.js 참고
  const { token, base } = await getSharedKisToken(appKey, appSecret);
  const params = new URLSearchParams({ FID_COND_MRKT_DIV_CODE: market, FID_INPUT_ISCD: ticker });
  const r = await fetch(`${base}/uapi/domestic-stock/v1/quotations/inquire-price?${params}`, {
    headers: {
      'content-type': 'application/json; charset=utf-8',
      authorization: `Bearer ${token}`,
      appkey: appKey,
      appsecret: appSecret,
      tr_id: 'FHKST01010100',
      custtype: 'P',
    },
  });
  if (!r.ok) throw new Error(`KIS API ${r.status}`);
  const d = await r.json();
  if (d.rt_cd !== '0') throw new Error(d.msg1 || 'KIS 조회 오류');
  const o = d.output;
  const price = Number(o.stck_prpr) || Number(o.stck_clpr) || Number(o.prdy_clpr);
  if (!price) throw new Error('KIS 가격 없음');
  return {
    price,
    chg: Number(o.prdy_vrss),
    chgPct: Number(o.prdy_ctrt),
    name: o.hts_kor_isnm || '',
  };
}

// NXT 시간대 판단 (프리마켓 08:05~08:50, 애프터마켓 15:35~20:00 KST, 평일만)
function isNxtTime() {
  const kst = new Date(Date.now() + 9 * 3600 * 1000);
  const day = kst.getUTCDay();
  if (day === 0 || day === 6) return false;
  const t = kst.getUTCHours() * 60 + kst.getUTCMinutes();
  return (t >= 8 * 60 + 5 && t < 8 * 60 + 50) || (t >= 15 * 60 + 35 && t < 20 * 60);
}

/** 알려진 ETF 배당주기 (Naver API가 단일월만 반환할 때 fallback) */
const KNOWN_DIV_CYCLES = {
  // 월배당
  '099140': '분기배당', // ARIRANG 고배당주
  '102110': '분기배당', // TIGER 200
  '130680': '무배당',   // TIGER 금속선물(H)
  '130690': '무배당',   // TIGER 원유선물Enhanced(H)
  '133690': '분기배당', // TIGER 나스닥100
  '137610': '분기배당', // KODEX 국채10년
  '148020': '분기배당', // KOSEF 국고채10년
  '152100': '월배당',   // ARIRANG 단기채권액티브
  '161510': '월배당',   // PLUS 고배당주
  '168580': '분기배당', // TIGER 글로벌리츠(합성H)
  '182480': '월배당',   // TIGER 단기통안채
  '210780': '분기배당', // TIGER 코스피고배당
  '229200': '분기배당', // KODEX 코스닥150
  '237350': '분기배당', // KODEX 배당성장
  '245340': '월배당',   // TIGER 리츠부동산인프라
  '253080': '무배당',   // ARIRANG 코스피TR
  '253150': '무배당',   // KODEX 200선물인버스2X
  '261140': '무배당',   // KBSTAR 200TR
  '261270': '무배당',   // KODEX MSCI Korea TR
  '069500': '분기배당', // KODEX 200
  '069660': '분기배당', // KODEX 코스피100
  '114800': '무배당',   // KODEX 인버스
  '117680': '분기배당', // KODEX 배당성장
  '122630': '무배당',   // KODEX 레버리지
  '243880': '무배당',   // KODEX 코스피TR
  '273130': '분기배당', // KBSTAR 코스피고배당
  '278530': '무배당',   // KODEX 200TR
  '279530': '월배당',   // KODEX 고배당
  '289250': '분기배당', // ARIRANG 미국S&P500
  '290080': '무배당',   // KODEX WTI원유선물(H)
  '292050': '분기배당', // TIGER 국채10년
  '294600': '무배당',   // HANARO 코스피TR
  '304660': '무배당',   // KODEX 200IT레버리지
  '305080': '무배당',   // KODEX 미국달러선물
  '305540': '무배당',   // TIGER 차이나CSI300레버리지(합성)
  '337140': '월배당',   // TIMEFOLIO Korea플러스배당액티브
  '352550': '분기배당', // TIGER S&P500
  '360750': '분기배당', // TIGER 미국S&P500
  '364980': '분기배당', // KODEX 미국빅테크10
  '379800': '무배당',   // KODEX 미국S&P500TR
  '379810': '무배당',   // KODEX 미국나스닥100TR
  '381170': '분기배당', // TIGER 미국나스닥100
  '395160': '분기배당', // TIGER 부동산인프라고배당
  '402970': '월배당',   // ACE 미국배당다우존스
  '407440': '분기배당', // HANARO 미국나스닥100
  '411400': '분기배당', // KBSTAR 미국나스닥100
  '411410': '분기배당', // KBSTAR 미국S&P500
  '416750': '월배당',   // HANARO 미국배당액티브
  '418660': '분기배당', // TIMEFOLIO 미국나스닥100액티브
  '422160': '월배당',   // PLUS 미국배당귀족
  '438100': '월배당',   // TIGER 미국배당+7%프리미엄다우존스
  '438600': '월배당',   // ARIRANG 미국배당다우존스
  '438880': '분기배당', // MASTER 미국나스닥100
  '438890': '분기배당', // MASTER 미국S&P500
  '438900': '월배당',   // KBSTAR 미국배당+커버드콜
  '440080': '월배당',   // ACE 미국30년국채액티브(H)
  '441640': '월배당',   // KODEX 미국배당커버드콜액티브
  '441680': '무배당',   // ACE 미국빅테크TOP7Plus레버리지(합성)
  '446720': '월배당',   // SOL 미국배당다우존스
  '453810': '분기배당', // SOL 미국S&P500
  '453820': '분기배당', // SOL 미국나스닥100
  '456600': '월배당',   // KODEX 미국빅테크TOP10타겟데일리커버드콜
  '458710': '분기배당', // TIGER 미국배당귀족
  '458730': '월배당',   // TIGER 미국배당다우존스
  '461070': '월배당',   // KODEX 미국S&P500타겟데일리커버드콜
  '466070': '월배당',   // KBSTAR 미국30년국채커버드콜액티브(H)
  '466090': '월배당',   // KODEX 미국장기국채+
  '466430': '분기배당', // PLUS 미국S&P500
  '466920': '분기배당', // ACE 미국나스닥100
  '468330': '분기배당', // ACE 미국S&P500
  '469990': '월배당',   // TIGER 미국S&P500+7%프리미엄다우존스
  '473330': '월배당',   // SOL 미국30년국채커버드콜(합성)
  '272560': '월배당',   // TIGER 국채3년
  '475560': '분기배당', // PLUS 미국나스닥100
  '476480': '월배당',   // KODEX 미국배당프리미엄액티브
  '476590': '월배당',   // PLUS 미국배당다우존스
  '479320': '월배당',   // SOL 미국배당다우존스타겟커버드콜(합성)
  '480040': '월배당',   // ACE 미국배당다우존스타겟커버드콜2(합성)
  '481730': '월배당',   // TIGER 미국30년국채커버드콜액티브(H)
  '489190': '월배당',   // KBSTAR 미국S&P500커버드콜액티브
  '489570': '월배당',   // SOL 미국배당다우존스(H)
  '494390': '월배당',   // TIGER 미국나스닥100+15%프리미엄
  '494960': '월배당',   // ACE 미국빅테크커버드콜액티브
};

/**
 * dividendMonthsThisYear 패턴으로 배당주기 추론
 * 핵심 규칙:
 *  - 연속된 2개월 이상 (1,2 / 1,2,3 등) → 월배당  (반기·분기배당은 인접월에 지급하지 않음)
 *  - 2개월, 간격 3개월 (1,4 / 3,6 등) → 분기배당
 *  - 2개월, 간격 6개월 (1,7 / 6,12 등) → 반기배당
 *  - 3개월 이상, 3개월 등간격 → 분기배당
 *  - 11개월 이상 → 월배당
 *  - 단일 월 → '' (1월만 있으면 연/분기/월 모두 가능 → 판단 불가)
 *  - 비어있음 → ''
 */
function inferDivCycle(monthsStr) {
  if (!monthsStr) return '';
  const months = monthsStr.split(',').filter(Boolean).map(Number).sort((a, b) => a - b);
  const cnt = months.length;
  if (cnt === 0) return '';
  if (cnt >= 11) return '월배당';
  if (cnt === 1)  return ''; // 단일월: 판단 불가

  // 연속 여부 확인
  const isConsecutive = months.every((m, i) => i === 0 || m === months[i - 1] + 1);
  if (isConsecutive) return '월배당';  // 2개 이상 연속 → 월배당

  // 2개 비연속: 간격으로 판단
  if (cnt === 2) {
    const gap = months[1] - months[0];
    if (gap === 3) return '분기배당';
    if (gap === 6) return '반기배당';
  }

  // 3개 이상 비연속: 등간격이면 분기배당
  if (cnt >= 3) {
    const gaps = months.slice(1).map((m, i) => m - months[i]);
    const allSame = gaps.every(g => g === gaps[0]);
    if (allSame && gaps[0] === 3) return '분기배당';
    if (allSame && gaps[0] === 4) return '분기배당'; // Jan,May,Sep 패턴
  }

  // 4개 이상이면 분기배당 가능성 높음
  if (cnt >= 4) return '분기배당';

  return '';
}

/** 배당주기 → 연간 배당 횟수 */
function cycleToCount(cycle) {
  if (cycle === '월배당')   return 12;
  if (cycle === '분기배당') return 4;
  if (cycle === '반기배당') return 2;
  if (cycle === '연배당')   return 1;
  return 0;
}

/** "1,2,3" → "1월,2월,3월" */
function formatDivMonths(monthsStr) {
  if (!monthsStr) return '';
  return monthsStr.split(',').filter(Boolean).map(m => m.trim() + '월').join(',');
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'GET') return res.status(405).json({ error: 'GET only' });

  const ticker = (req.query.ticker || '').trim().toUpperCase().replace(/^A/, '');
  if (!/^[A-Z0-9]{6}$/.test(ticker)) return res.status(400).json({ error: '유효한 티커(6자리)를 입력하세요' });

  // ── 일봉 차트 데이터 (?chart=1, interval 없음) — ETF 탭 등 기존 호출 ──
  if (req.query.chart === '1' && !req.query.interval) {
    const count = Math.min(Number(req.query.count) || 60, 200);
    try {
      const url = `https://fchart.stock.naver.com/sise.nhn?symbol=${ticker}&timeframe=day&count=${count}&requestType=0`;
      const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://m.stock.naver.com/' } });
      if (!r.ok) throw new Error(`Naver fchart ${r.status}`);
      const text = await r.text();
      const candles = [...text.matchAll(/data="([^"]+)"/g)].map(m => {
        const [date, open, high, low, close, volume] = m[1].split('|');
        return { time: `${date.slice(0,4)}-${date.slice(4,6)}-${date.slice(6,8)}`, open: Number(open), high: Number(high), low: Number(low), close: Number(close), volume: Number(volume) };
      }).filter(d => d.close > 0);
      res.setHeader('Cache-Control', 'max-age=300');
      return res.status(200).json({ candles });
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }

  // ── 분봉 차트 데이터 (?chart=1&interval=1m|10m|1h) — 그리드 매매 현황용 ──
  // 2026-09-01: KIS 페이징 수집 로직을 Oracle VM(coin-runner)으로 이전하고
  // 여기선 프록시만 함. 사유 두 가지:
  //  (1) Vercel 서버리스는 트래픽이 뜸하면 인스턴스가 죽어 인메모리 캐시가
  //      사라지고, 그때마다 KIS를 처음부터 재수집하다 함수 기본 타임아웃
  //      (10초)에 걸려 차트가 빈 채로 내려오는 문제가 있었음.
  //  (2) 더 심각한 버그: 장 시작 직후처럼 당일 데이터가 페이지(30건)를 못
  //      채우면 KIS가 전일 데이터로 패딩해서 주는데, 그 전일 시:분을 다음
  //      페이지 앵커로 재사용하면 "오늘 그 시각"으로 재해석되어 미래 시각을
  //      조회하게 됨 — KIS가 그 미래 슬롯을 마지막 종가로 채운 평평한 가짜
  //      봉으로 채워 응답해서, 실제 데이터가 차트 좌측 끝 좁은 폭으로 밀려나
  //      사실상 안 보이는 증상이 매일 장 시작 직후 100% 재현됐음(두산에너빌리티
  //      034020 그리드 차트 미표시로 발견). coin-runner(PM2 상시구동, 콜드스타트
  //      없음)에 날짜 경계를 인지하는 버전으로 옮기며 같이 수정함
  //      (자세한 원인 설명은 coin-runner/stock_candles.js 상단 주석 참고).
  if (req.query.chart === '1' && ['1m', '10m', '1h'].includes(req.query.interval)) {
    const oracleUrl = process.env.ORACLE_CANDLES_URL, oracleKey = process.env.ORACLE_DATA_KEY;
    if (!oracleUrl || !oracleKey) return res.status(500).json({ error: 'ORACLE_CANDLES_URL/ORACLE_DATA_KEY 미설정' });
    try {
      const r = await fetch(`${oracleUrl}?ticker=${encodeURIComponent(ticker)}&interval=${encodeURIComponent(req.query.interval)}`, {
        headers: { 'x-api-key': oracleKey },
      });
      const d = await r.json();
      res.setHeader('Cache-Control', 'no-store');
      return res.status(r.ok ? 200 : 500).json(d);
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }

  // ── 일/주/월봉 차트 데이터 (?chart=1&interval=1d|1w|1M) — 그리드 매매
  // 현황용. KIS 대신 이미 ETF 탭에서 쓰던 네이버 fchart(인증 불필요, 단일
  // 콜)를 재사용 — 기간을 아무리 늘려도 호출 1번이라 KIS 쿼터(실거래
  // 데몬과 공유)에 전혀 영향 없음.
  if (req.query.chart === '1' && ['1d', '1w', '1M'].includes(req.query.interval)) {
    const tfMap    = { '1d': 'day', '1w': 'week', '1M': 'month' };
    const countMap = { '1d': 100, '1w': 100, '1M': 60 }; // 대략 일봉 5개월치/주봉 2년치/월봉 5년치
    const timeframe = tfMap[req.query.interval];
    const count = countMap[req.query.interval];
    try {
      const url = `https://fchart.stock.naver.com/sise.nhn?symbol=${ticker}&timeframe=${timeframe}&count=${count}&requestType=0`;
      const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://m.stock.naver.com/' } });
      if (!r.ok) throw new Error(`Naver fchart ${r.status}`);
      const text = await r.text();
      const candles = [...text.matchAll(/data="([^"]+)"/g)].map(m => {
        const [date, open, high, low, close] = m[1].split('|');
        return { time: `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`, open: Number(open), high: Number(high), low: Number(low), close: Number(close) };
      }).filter(d => d.close > 0);
      res.setHeader('Cache-Control', 'no-store');
      return res.status(200).json({ candles });
    } catch (e) {
      return res.status(500).json({ error: e.message });
    }
  }

  try {
    const url = `https://m.stock.naver.com/api/etf/${ticker}/basic`;
    const r = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://m.stock.naver.com/',
        'Accept': 'application/json, */*',
      },
    });

    if (!r.ok) throw new Error(`Naver API ${r.status}`);
    const d = await r.json();

    let price  = Number(String(d.closePrice  ?? '0').replace(/,/g, ''));
    let chg    = Number(String(d.compareToPreviousClosePrice ?? '0').replace(/,/g, ''));
    let chgPct = Number(String(d.fluctuationsRatio ?? '0').replace(/,/g, ''));
    const name   = d.stockName ?? '';

    if (!price) throw new Error('가격 정보 없음');

    // NXT 시간대: NXT 거래 종목이면 실시간가로 교체 (ETF는 대부분 해당 없음)
    let usedNxt = false;
    if (isNxtTime()) {
      try {
        const nxtKey = process.env.KIS_APP_KEY, nxtSec = process.env.KIS_APP_SECRET;
        if (nxtKey && nxtSec) {
          const nxt = await getKisPrice(ticker, nxtKey, nxtSec, 'NX');
          if (nxt.price) { price = nxt.price; chg = nxt.chg; chgPct = nxt.chgPct; usedNxt = true; }
        }
      } catch (_) {}
    }

    // ── 배당 정보 ──────────────────────────────────────────────────
    const monthsStr = d.dividendMonthsThisYear ?? '';   // 올해 지급된 월 "1,2,3"
    const annualDiv = Number(d.dividendPerShareTtm ?? 0); // TTM 연간 배당금 합계(원)

    // 1차: 월 패턴으로 배당주기 추론 (연속 2개월 이상 → 월배당, 단일월 → '')
    // 2차: 판단 불가일 때 hardcoded 룩업 테이블 fallback
    const divCycle  = inferDivCycle(monthsStr) || KNOWN_DIV_CYCLES[ticker] || '';

    // 배당월: 월배당이면 "매월", 그 외는 Naver 월 목록 사용
    const divMonths = divCycle === '월배당' ? '매월' : formatDivMonths(monthsStr);

    // 배당주기가 확인된 경우에만 recentDiv 계산
    const divCount  = cycleToCount(divCycle);
    const recentDiv = (annualDiv && divCount) ? Math.round(annualDiv / divCount) : 0;

    // 최근 배당률: 최근 1회 배당금 / 현재가 × 100
    const recentDivRate = (recentDiv && price)
      ? Number((recentDiv / price * 100).toFixed(2)) : 0;

    // 연간 배당률: TTM 배당금 합계 / 현재가 × 100 (현재가 기준)
    const annualDivRate = (annualDiv && price)
      ? Number((annualDiv / price * 100).toFixed(2))
      : Number(d.dividendYieldTtm ?? 0);

    // NXT 실시간가로 교체된 경우 Vercel 엣지 캐시(60~180초)에 오래된 값이
    // 남아있으면 안 되므로 캐시하지 않음 — 정규장 종가는 그대로 캐시 유지
    res.setHeader('Cache-Control', usedNxt ? 'no-store' : 's-maxage=60, stale-while-revalidate=120');
    return res.status(200).json({
      ticker, name, price, chg, chgPct,
      divCycle, divMonths, annualDiv, annualDivRate, recentDiv, recentDivRate,
    });
  } catch (naverErr) {
    // Naver API 실패 시 KIS API로 폴백 (일부 합성 ETF가 Naver에서 StockConflict 반환)
    const appKey    = process.env.KIS_APP_KEY;
    const appSecret = process.env.KIS_APP_SECRET;
    if (appKey && appSecret) {
      try {
        const kis = await getKisPrice(ticker, appKey, appSecret);
        if (!kis.price) throw new Error('KIS 가격 없음');
        const divCycle  = KNOWN_DIV_CYCLES[ticker] || '';
        const divMonths = divCycle === '월배당' ? '매월' : '';
        res.setHeader('Cache-Control', 's-maxage=60, stale-while-revalidate=120');
        return res.status(200).json({
          ticker, name: kis.name, price: kis.price, chg: kis.chg, chgPct: kis.chgPct,
          divCycle, divMonths, annualDiv: 0, annualDivRate: 0, recentDiv: 0, recentDivRate: 0,
        });
      } catch (_) { /* KIS도 실패 시 원래 에러 반환 */ }
    }
    return res.status(500).json({ error: naverErr.message, ticker });
  }
}
