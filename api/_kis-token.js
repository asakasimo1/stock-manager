/**
 * KIS 접근토큰 공유 조회 — Vercel 서버리스 콜드스타트마다 각 함수가 따로
 * KIS에 토큰을 발급받으려 시도하는 문제를 완화한다.
 *
 * 배경(2026-09-03, 한미반도체 NXT가 미반영·포트폴리오 "변동없음" 표시로
 * 발견): KIS는 앱키당 토큰 발급을 "1분당 1회"로 제한한다(오류 EGW00133).
 * api/quote.js와 api/stock.js는 서로 다른 서버리스 함수라 인메모리 토큰
 * 캐시를 공유하지 않고, 콜드스타트마다(트래픽이 뜸하면 자주 발생) 각자
 * 새 토큰을 발급받으려 한다. 포트폴리오 탭은 이 둘을 동시에 호출하고,
 * 여기에 Oracle VM(coin-runner)의 캔들 수집·거래 데몬의 토큰 발급까지
 * 겹치면 여러 프로세스가 동시에 발급을 시도해 충돌 → 403 → 해당 요청은
 * catch에서 조용히 삼켜져 NXT가 대신 Naver의 장전(PREOPEN, 변동 0%) 값이
 * 표시됨.
 *
 * coin-runner는 PM2로 상시구동되는 유일한 프로세스라 토큰 캐시가 콜드되지
 * 않으므로, 여기서 그 캐시를 우선 재사용하고(GET /api/kis-token) 실패
 * 시에만 이 함수가 직접 KIS에 발급을 요청하는 것으로 폴백한다.
 */

let _mem = null; // { token, base, expires }

function kisBase() {
  return (process.env.PAPER_TRADE || '').toLowerCase() === 'true'
    ? 'https://openapivts.koreainvestment.com:29443'
    : 'https://openapi.koreainvestment.com:9443';
}
function kisBasePaper() { return 'https://openapivts.koreainvestment.com:29443'; }

async function _fetchFromOracle() {
  const dataUrl = process.env.ORACLE_DATA_URL, key = process.env.ORACLE_DATA_KEY;
  if (!dataUrl) return null;
  try {
    const base = dataUrl.trim().replace(/\/api\/stock-data.*$/, '');
    const r = await fetch(`${base}/api/kis-token`, { headers: { 'x-api-key': (key || '').trim() } });
    if (!r.ok) return null;
    const d = await r.json();
    if (!d.token) return null;
    return { token: d.token, base: d.base, expires: d.expires };
  } catch {
    return null;
  }
}

async function _issueDirect(appKey, appSecret) {
  let base = kisBase(), token;
  const issue = async b => {
    const r = await fetch(`${b}/oauth2/tokenP`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ grant_type: 'client_credentials', appkey: appKey, appsecret: appSecret }),
    });
    if (!r.ok) throw new Error(`KIS 토큰 실패: ${r.status}`);
    const d = await r.json();
    return d.access_token;
  };
  try {
    token = await issue(base);
  } catch (e) {
    if (String(e.message).includes('403') && base !== kisBasePaper()) {
      base = kisBasePaper();
      token = await issue(base);
    } else {
      throw e;
    }
  }
  return { token, base, expires: Date.now() + 86_400_000 };
}

/** { token, base } 반환. 이 함수 인스턴스(콜드/웜) 내에서는 재사용됨 */
export async function getSharedKisToken(appKey, appSecret) {
  const now = Date.now();
  if (_mem && _mem.expires > now + 60_000) return _mem;

  const shared = await _fetchFromOracle();
  if (shared) { _mem = shared; return _mem; }

  const direct = await _issueDirect(appKey, appSecret);
  _mem = direct;
  return _mem;
}
