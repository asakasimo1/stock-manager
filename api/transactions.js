/**
 * Vercel API — 매수/매도 거래 내역 (JSONBin 연동)
 * GET    /api/transactions         → { records: [...] }
 * GET    /api/transactions?etf_id= → { records: [...] }
 * POST   /api/transactions         → body: { record } → { ok, record }
 * DELETE /api/transactions?id=     → { ok }
 *
 * JSONBin에 transactions 키가 없으면 Gist의 transactions.json에서 자동 이전 (1회)
 */

import { readBin, writeBin } from './_jsonbin.js';

/** Gist에서 transactions.json 읽기 (마이그레이션 전용) */
async function readGistTransactions() {
  const gistId  = process.env.GIST_ID;
  const ghToken = process.env.GH_TOKEN;
  if (!gistId) return null;
  try {
    const headers = {
      Accept: 'application/vnd.github+json',
      'User-Agent': 'stock-analyzer',
      ...(ghToken ? { Authorization: `Bearer ${ghToken}` } : {}),
    };
    const r = await fetch(`https://api.github.com/gists/${gistId}`, { headers });
    if (!r.ok) return null;
    const gist = await r.json();
    const file = gist.files?.['transactions.json'];
    if (!file?.content) return null;
    return JSON.parse(file.content || '[]');
  } catch (_) {
    return null;
  }
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const binId = process.env.JSONBIN_BIN_ID;
  const key   = process.env.JSONBIN_KEY;
  if (!binId || !key) return res.status(500).json({ error: 'JSONBIN 환경변수 미설정' });

  try {
    if (req.method === 'GET') {
      const data = await readBin(binId, key);

      // transactions 키가 없으면 Gist에서 자동 이전 (1회성 마이그레이션)
      if (!Object.prototype.hasOwnProperty.call(data, 'transactions')) {
        let migrated = [];
        const gistRecords = await readGistTransactions();
        if (Array.isArray(gistRecords) && gistRecords.length > 0) migrated = gistRecords;
        await writeBin(binId, key, { ...data, transactions: migrated }).catch(() => {});
        let records = migrated;
        const etfId = req.query.etf_id;
        if (etfId) records = records.filter(r => String(r.etf_id) === String(etfId));
        res.setHeader('Cache-Control', 'no-store');
        return res.status(200).json({ records });
      }

      let records = data.transactions ?? [];
      const etfId = req.query.etf_id;
      if (etfId) records = records.filter(r => String(r.etf_id) === String(etfId));
      res.setHeader('Cache-Control', 'no-store');
      return res.status(200).json({ records });
    }

    if (req.method === 'POST') {
      const { record } = req.body;
      if (!record) return res.status(400).json({ error: 'record 필요' });
      const data = await readBin(binId, key, true); // 쓰기 전 fresh 읽기
      const records = data.transactions ?? [];
      if (!record.id) record.id = Date.now();
      records.push(record);
      await writeBin(binId, key, { ...data, transactions: records });
      return res.status(200).json({ ok: true, record });
    }

    if (req.method === 'DELETE') {
      const id = req.query.id;
      if (!id) return res.status(400).json({ error: 'id 필요' });
      const data = await readBin(binId, key, true); // 쓰기 전 fresh 읽기
      const records = (data.transactions ?? []).filter(r => String(r.id) !== String(id));
      await writeBin(binId, key, { ...data, transactions: records });
      return res.status(200).json({ ok: true });
    }

    return res.status(405).json({ error: 'Method Not Allowed' });
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
