/**
 * JSONBin.io 헬퍼 — 읽기/쓰기 + 인스턴스 내 캐시 (stale-while-revalidate)
 * 환경변수: JSONBIN_KEY, JSONBIN_BIN_ID
 *
 * 2026-09-07: JSONBin.io 자체가 완전히 다운(521, Cloudflare 오리진 응답 없음
 * — jsonbin.io 홈페이지조차 접속 안 됨, 우리 쪽 설정/쿼터 문제 아님)되면서
 * 포트폴리오(개별주/ETF/공모주) 데이터가 통째로 빈 화면이 되는 문제 발견.
 * 이후엔 읽기/쓰기 성공 시마다 같은 데이터를 GitHub Gist(GIST_ID, 기존
 * 코인/브리핑 데이터가 쓰는 것과 같은 Gist)의 jsonbin_mirror.json 파일에도
 * 백업해두고, JSONBin이 응답 불가일 때는 그 미러를 대신 반환한다.
 * ⚠️ 한계: 미러는 "마지막 성공 읽기/쓰기 시점" 스냅샷이라 완전한 대체 저장소가
 * 아니고, 지금처럼 이 기능 배포 전부터 이어진 장애라면 미러에도 데이터가 없어
 * 즉시 복구는 안 됨 — JSONBin이 살아난 뒤 첫 성공 응답부터 미러가 채워짐.
 * 쓰기(writeBin)는 미러로 대신하지 않고 그대로 실패시킨다(장애 중 수정한
 * 내용이 나중에 JSONBin과 어긋나는 걸 방지).
 */
import { fetchGistCached } from './_gist-cache.js';

const BASE = 'https://api.jsonbin.io/v3/b';
const TTL_MS       = 300_000;  // 5분 (fresh 범위)
const STALE_TTL_MS = 600_000;  // 10분 (stale-while-revalidate 허용 범위)
const MIRROR_FILE  = 'jsonbin_mirror.json';

let _cache        = null;
let _cacheAt      = 0;
let _bgRefreshing = false;

function _mirrorToGist(data) {
  const gistId  = process.env.GIST_ID;
  const ghToken = process.env.GH_TOKEN;
  if (!gistId || !ghToken) return;
  fetch(`https://api.github.com/gists/${gistId}`, {
    method: 'PATCH',
    headers: {
      Accept: 'application/vnd.github+json',
      'User-Agent': 'stock-analyzer',
      Authorization: `Bearer ${ghToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ files: { [MIRROR_FILE]: { content: JSON.stringify(data) } } }),
  }).catch(() => {});
}

async function _readMirrorFromGist() {
  const gistId = process.env.GIST_ID;
  if (!gistId) return null;
  try {
    const gist = await fetchGistCached(gistId, process.env.GH_TOKEN);
    const raw = gist?.files?.[MIRROR_FILE]?.content;
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

async function _doFetch(binId, key) {
  let r, networkErr = null;
  try {
    r = await fetch(`${BASE}/${binId}/latest`, { headers: { 'X-Master-Key': key } });
  } catch (e) {
    networkErr = e;
  }

  if (networkErr || !r.ok) {
    const mirror = await _readMirrorFromGist();
    if (mirror) {
      console.warn(`[JSONBin] 원본 응답 불가(${networkErr ? networkErr.message : r.status}) — Gist 미러로 대체`);
      _cache   = mirror;
      _cacheAt = Date.now();
      return _cache;
    }
    throw networkErr || new Error(`JSONBin read ${r.status}`);
  }

  const { record } = await r.json();
  _cache   = record ?? {};
  _cacheAt = Date.now();
  _mirrorToGist(_cache); // 성공 시 백업 갱신 (실패해도 무시, 다음 성공 때 재시도)
  return _cache;
}

/**
 * @param {boolean} fresh - true이면 캐시 무시하고 강제로 JSONBin에서 읽기 (쓰기 직전에 사용)
 */
export async function readBin(binId, key, fresh = false) {
  if (fresh) return _doFetch(binId, key);

  const now = Date.now();
  const age = now - _cacheAt;

  // 캐시 유효 (5분 이내)
  if (_cache && age < TTL_MS) return _cache;

  // stale (5~10분): 즉시 반환 + 백그라운드 갱신
  if (_cache && age < STALE_TTL_MS) {
    if (!_bgRefreshing) {
      _bgRefreshing = true;
      _doFetch(binId, key).catch(() => {}).finally(() => { _bgRefreshing = false; });
    }
    return _cache;
  }

  // 캐시 없거나 만료(10분 초과): 대기 후 반환
  return _doFetch(binId, key);
}

export async function writeBin(binId, key, data) {
  const r = await fetch(`${BASE}/${binId}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      'X-Master-Key': key,
    },
    body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error(`JSONBin write ${r.status}`);
  _cache   = null;
  _cacheAt = 0;
  const record = (await r.json()).record;
  _mirrorToGist(record);
  return record;
}
