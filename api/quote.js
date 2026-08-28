/**
 * Vercel API — ETF 현재가 + 배당 정보 조회 (Naver Mobile 프록시)
 * GET /api/quote?ticker=161510
 * Returns: { ticker, name, price, chg, chgPct, divCycle, divMonths, annualDiv, annualDivRate, recentDiv, recentDivRate }
 */

/** KIS API 토큰 캐시 (Naver API 실패 시 폴백용) */
let _kisTokenCache = null;

// 그리드 매매 분봉 차트용 — 시장(KRX/NXT)별 원본 1분봉 캐시. warm 인스턴스
// 간 재사용(위 _kisTokenCache와 동일 패턴). "ticker:interval:market" → { bars, fetchedAt }.
const _marketBarsCache = {};
// 프론트(tab-autotrade.js)가 30초마다 폴링하는데 10분봉 TTL이 45초라 매
// 폴링의 절반이 캐시 히트로 낭비되며 콜드 상태에서 목표범위(180분)까지
// 점진적 백필이 필요 이상으로 느렸음(2026-08-28 사용자 리포트 — "차트
// 기간이 너무 짧다", 배포 직후 서버 캐시가 리셋된 콜드 상태였음). 폴링
// 주기보다 짧게 낮춰 매 폴링이 항상 백필 1페이지씩 진행하도록 함.
const CANDLE_CACHE_TTL_MS = { '1m': 20_000, '10m': 25_000, '1h': 90_000 };

// 실계좌: openapi.koreainvestment.com:9443
// 모의투자: openapivts.koreainvestment.com:29443 (PAPER_TRADE=true 시)
// 실계좌 우선 시도 → 403 시 모의투자 자동 폴백
function kisBase() {
  return (process.env.PAPER_TRADE || '').toLowerCase() === 'true'
    ? 'https://openapivts.koreainvestment.com:29443'
    : 'https://openapi.koreainvestment.com:9443';
}
function kisBasePaper() { return 'https://openapivts.koreainvestment.com:29443'; }

async function _kisToken(base, appKey, appSecret) {
  const r = await fetch(`${base}/oauth2/tokenP`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ grant_type: 'client_credentials', appkey: appKey, appsecret: appSecret }),
  });
  if (!r.ok) throw new Error(`KIS 토큰 실패: ${r.status}`);
  const d = await r.json();
  return d.access_token;
}

async function getKisPrice(ticker, appKey, appSecret, market = 'J') {
  const now = Date.now();
  // 토큰 유효성 체크 또는 재발급 (실계좌 우선, 403 시 모의투자 폴백)
  if (!_kisTokenCache || _kisTokenCache.expires <= now + 60_000) {
    let base = kisBase();
    let token;
    try {
      token = await _kisToken(base, appKey, appSecret);
    } catch (e) {
      if (e.message.includes('403') && base !== kisBasePaper()) {
        base = kisBasePaper();
        token = await _kisToken(base, appKey, appSecret);
      } else {
        throw e;
      }
    }
    _kisTokenCache = { token, base, expires: now + 86_400_000 };
  }
  const { token, base } = _kisTokenCache;
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
  // KIS "주식당일분봉조회"(FHKST03010200)는 1분봉만 주고 호출당 최대 30건이라
  // (당일 데이터만 제공) FID_INPUT_HOUR_1을 뒤로 이동시키며 페이징해서
  // 1분봉을 모은다. interval에 따라 목표 범위·페이지 수·집계 단위만 다르고
  // 로직은 동일 — 1분봉은 최근 2시간(가벼운 페이지 수), 10분봉은 최근
  // 6시간(기존), 1시간봉은 장중 전체(약 7시간)까지 모은 뒤 bucketMin 단위로
  // 집계(1분봉은 집계 없이 그대로).
  if (req.query.chart === '1' && ['1m', '10m', '1h'].includes(req.query.interval)) {
    const ivl = req.query.interval;
    const bucketMin = ivl === '1m' ? 1 : ivl === '10m' ? 10 : 60;
    // 10분봉은 KRX+NXT 둘 다 콜드 스타트 시 처음부터 백필해야 해서(Redis/KV
    // 없이는 서버리스 인스턴스가 식으면 캐시가 사라짐, 2026-08-25 사용자와
    // 상의) 목표 범위를 6시간→3시간으로 줄여 콜드 로딩 시간을 절반으로
    // 낮춤(17~20초 → 8~10초 예상, 사용자 승인: "3시간으로 축소").
    const targetMin  = ivl === '1m' ? 120 : ivl === '10m' ? 180 : 420;
    const maxPages   = ivl === '1m' ? 5 : ivl === '10m' ? 6 : 14;

    const appKey = process.env.KIS_APP_KEY, appSecret = process.env.KIS_APP_SECRET;
    if (!appKey || !appSecret) return res.status(500).json({ error: 'KIS API 키 미설정' });
    try {
      if (!_kisTokenCache || _kisTokenCache.expires <= Date.now() + 60_000) {
        let base = kisBase(), token;
        try { token = await _kisToken(base, appKey, appSecret); }
        catch (e) {
          if (e.message.includes('403') && base !== kisBasePaper()) { base = kisBasePaper(); token = await _kisToken(base, appKey, appSecret); }
          else throw e;
        }
        _kisTokenCache = { token, base, expires: Date.now() + 86_400_000 };
      }
      const { token, base } = _kisTokenCache;

      // 시장 하나(KRX 'J' 또는 NXT 'NX')분 1분봉을 페이징 수집.
      // KIS 분봉조회(FHKST03010200)는 초당 호출 한도가 낮아서, 지연 없이 연속
      // 페이징하면 3번째 호출부터 HTTP 500이 떨어져 6시간 목표(targetMin)의
      // 극히 일부(약 1시간)만 모으고 조용히 중단됐음(2026-08-25 debug 계측으로
      // 확인: page 0/1은 정상, page 2에서 httpBreak:500). 페이지 사이 텀을 둬서
      // 회피.
      // startHour1을 넘기면 "지금 시각"이 아니라 그 시점부터 과거로 페이징
      // 시작 — 점진적 백필(아래 getMarketBarsCached)이 기존 캐시의 가장 오래된
      // 봉 이전부터 이어서 파고들 때 씀.
      async function fetchMarketBars(market, pages = maxPages, startHour1 = null) {
        let hour1 = startHour1;
        if (!hour1) {
          const kst = new Date(Date.now() + 9 * 3600 * 1000);
          const nowMin = kst.getUTCHours() * 60 + kst.getUTCMinutes();
          // KRX('J')는 15:30 마감 이후 "지금 시각"으로 조회하면 output2가 아예
          // 빈 채로 와서(2026-08-25 실측: NXT 애프터마켓 중 KRX 쪽 통째로 유실,
          // 화면엔 정규장 캔들이 하나도 안 보였음) 정규장 데이터를 못 모음 —
          // hour1을 정규장 마감(15:30) 이내로 고정해 항상 실제 데이터가 있는
          // 시점부터 페이징을 시작한다.
          const anchorMin = (market === 'J' && nowMin > 15 * 60 + 30) ? 15 * 60 + 30 : nowMin;
          hour1 = String(Math.floor(anchorMin / 60)).padStart(2, '0') + String(anchorMin % 60).padStart(2, '0') + '00';
        }
        const bars = [];
        const seenHours = new Set();
        for (let page = 0; page < pages; page++) {
          if (page > 0) await new Promise(r => setTimeout(r, 250));
          const params = new URLSearchParams({
            FID_COND_MRKT_DIV_CODE: market, FID_INPUT_ISCD: ticker,
            FID_INPUT_HOUR_1: hour1, FID_PW_DATA_INCU_YN: 'Y', FID_ETC_CLS_CODE: '',
          });
          let r = await fetch(`${base}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice?${params}`, {
            headers: {
              'content-type': 'application/json; charset=utf-8',
              authorization: `Bearer ${token}`, appkey: appKey, appsecret: appSecret,
              tr_id: 'FHKST03010200', custtype: 'P',
            },
          });
          if (!r.ok) {
            // 순간적인 초당 한도 초과일 수 있으니 한 번은 더 쉬고 재시도.
            await new Promise(res => setTimeout(res, 400));
            r = await fetch(`${base}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice?${params}`, {
              headers: {
                'content-type': 'application/json; charset=utf-8',
                authorization: `Bearer ${token}`, appkey: appKey, appsecret: appSecret,
                tr_id: 'FHKST03010200', custtype: 'P',
              },
            });
            if (!r.ok) break;
          }
          const d = await r.json();
          if (d.rt_cd !== '0' || !Array.isArray(d.output2) || !d.output2.length) break;
          let earliest = null;
          for (const row of d.output2) {
            const t = row.stck_cntg_hour; // HHMMSS
            if (seenHours.has(t)) continue;
            seenHours.add(t);
            bars.push({
              date: row.stck_bsop_date, time: t,
              open: Number(row.stck_oprc), high: Number(row.stck_hgpr),
              low: Number(row.stck_lwpr), close: Number(row.stck_prpr),
            });
            if (earliest === null || t < earliest) earliest = t;
          }
          if (bars.length >= targetMin || !earliest || earliest === hour1) break;
          // 다음 페이지는 이번 페이지의 가장 이른 시각 1분 전부터
          const h = Number(earliest.slice(0, 2)), m = Number(earliest.slice(2, 4));
          const totalMin = h * 60 + m - 1;
          if (totalMin < 0) break; // 자정 이전 — 더 갈 데 없음
          hour1 = String(Math.floor(totalMin / 60)).padStart(2, '0') + String(totalMin % 60).padStart(2, '0') + '00';
        }
        return bars;
      }

      // KRX 정규장 + NXT(프리/애프터마켓) 통합 차트 — 그리드 매매가 NXT
      // 시간대에도 계속 도는데(job_stock_grid.py의 market_open 판정 참고)
      // 차트는 KRX('J')만 조회해서 15:30 이후 캔들이 아예 안 그려지던 문제
      // (2026-08-25 사용자 리포트: NXT장에서 등록한 그리드의 체결 흐름이 차트에
      // 하나도 안 보임) — NX 마켓도 같이 모아서 시간순으로 합친다. 두 세션은
      // 거래시간이 거의 겹치지 않아(KRX 09:00~15:30, NXT 08:00~08:50/15:30~20:00)
      // 같은 분에 양쪽 다 봉이 있는 경우는 드물지만, 겹치면 KRX를 우선한다
      // (더 유동성이 큰 기준가로 간주).
      // 코인 차트는 Upbit가 캔들을 한 번에 통째로 주는 반면, 이쪽은 매 요청마다
      // KRX+NXT 각각 최대 12페이지(0.25~0.65초 텀 포함)를 처음부터 다시 훑어서
      // 10초 넘게 걸림(2026-08-25 사용자 리포트) — 대부분의 과거 구간은 직전
      // 요청과 달라질 게 없는데도 매번 통째로 재수집하고 있던 게 원인. 모듈
      // 스코프 캐시(warm 인스턴스 간 재사용, _kisTokenCache와 동일 패턴)에
      // 시장별 원본 1분봉을 보관해뒀다가, TTL 이내면 KIS 호출 없이 즉시
      // 반환한다.
      //
      // 콜드 상태(캐시 자체가 없음 — 서버 재시작 직후 등)일 땐 목표 범위
      // 전체(targetMin, 최대 12페이지)를 한 번에 채우려면 여전히 몇 초~십몇
      // 초가 걸려서(2026-08-26 사용자 리포트: "3시간으로 줄여도 여전히 느림")
      // 최초 응답은 1페이지(최근 ~30분)만 빠르게 받아 즉시 내려주고, 목표
      // 범위에 못 미치는 동안은 매 갱신(TTL 만료)마다 (a) 최신 구간 1페이지
      // 갱신 + (b) 캐시에 있는 가장 오래된 봉 이전부터 1페이지를 추가로 받아
      // 과거 방향으로 넓혀간다 — 화면은 "최근 30분 → 점점 과거로 늘어나는"
      // 모양으로 몇 번의 자동 새로고침(수십 초~수 분) 안에 목표 범위까지
      // 자연스럽게 채워진다. 목표 범위에 도달한 뒤엔 최신 구간 1페이지만
      // 갱신하는 기존 방식으로 돌아간다.
      async function getMarketBarsCached(market) {
        const cacheKey = `${ticker}:${ivl}:${market}`;
        const ttlMs = CANDLE_CACHE_TTL_MS[ivl] || 30_000;
        const cached = _marketBarsCache[cacheKey];
        if (cached && (Date.now() - cached.fetchedAt) < ttlMs) return cached.bars;

        if (!cached) {
          const bars = await fetchMarketBars(market, 1);
          _marketBarsCache[cacheKey] = { bars, fetchedAt: Date.now() };
          return bars;
        }

        const fresh = await fetchMarketBars(market, 1);
        const merged = [...cached.bars];
        const seen = new Set(merged.map(b => b.date + b.time));
        for (const bar of fresh) {
          const key = bar.date + bar.time;
          if (seen.has(key)) continue;
          seen.add(key);
          merged.push(bar);
        }

        if (merged.length < targetMin && merged.length) {
          merged.sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
          const oldest = merged[0];
          const h = Number(oldest.time.slice(0, 2)), m = Number(oldest.time.slice(2, 4));
          const totalMin = h * 60 + m - 1;
          if (totalMin >= 0) {
            const startHour1 = String(Math.floor(totalMin / 60)).padStart(2, '0') + String(totalMin % 60).padStart(2, '0') + '00';
            const older = await fetchMarketBars(market, 1, startHour1);
            for (const bar of older) {
              const key = bar.date + bar.time;
              if (seen.has(key)) continue;
              seen.add(key);
              merged.push(bar);
            }
          }
        }

        merged.sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
        const bars = merged.length > targetMin * 2 ? merged.slice(-targetMin * 2) : merged;
        _marketBarsCache[cacheKey] = { bars, fetchedAt: Date.now() };
        return bars;
      }

      // 순차 호출 — KIS 초당 호출 한도 때문에(위 fetchMarketBars 주석 참고) 두
      // 시장을 동시에 페이징하면 합산 호출 빈도가 배로 늘어 같은 500 문제가
      // 재발할 수 있어 병렬(Promise.all) 대신 순차로 처리한다. 캐시 적중 시엔
      // 어차피 KIS 호출이 아예 없어 순차 여부가 무의미해짐.
      const krxBars = await getMarketBarsCached('J');
      const nxtBars = await getMarketBarsCached('NX');
      const oneMin = [...krxBars];
      const seenKey = new Set(krxBars.map(b => b.date + b.time));
      for (const bar of nxtBars) {
        const key = bar.date + bar.time;
        if (seenKey.has(key)) continue;
        seenKey.add(key);
        oneMin.push(bar);
      }

      // 시간 오름차순 정렬 후 bucketMin 단위로 집계(1분봉은 사실상 그대로 통과)
      oneMin.sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
      const buckets = new Map(); // "YYYYMMDDHHMM0"(버킷 키) → candle
      for (const bar of oneMin) {
        const h = bar.time.slice(0, 2), m = bar.time.slice(2, 4);
        const bucketMinStr = String(Math.floor(Number(m) / bucketMin) * bucketMin).padStart(2, '0');
        const key = `${bar.date}${h}${bucketMinStr}`;
        if (!buckets.has(key)) {
          buckets.set(key, { key, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
        } else {
          const b = buckets.get(key);
          b.high = Math.max(b.high, bar.high);
          b.low  = Math.min(b.low, bar.low);
          b.close = bar.close; // 1분봉이 시간 오름차순이므로 마지막 값이 종가
        }
      }
      const candles = [...buckets.values()]
        .sort((a, b) => a.key.localeCompare(b.key))
        .map(b => {
          const y = b.key.slice(0, 4), mo = b.key.slice(4, 6), d = b.key.slice(6, 8), h = b.key.slice(8, 10), mi = b.key.slice(10, 12);
          const utcMs = Date.parse(`${y}-${mo}-${d}T${h}:${mi}:00+09:00`);
          return { time: Math.floor(utcMs / 1000), open: b.open, high: b.high, low: b.low, close: b.close };
        });

      res.setHeader('Cache-Control', 'no-store');
      return res.status(200).json({ candles });
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
