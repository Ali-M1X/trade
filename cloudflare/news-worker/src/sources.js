// Exchange announcements (the fast path). Same endpoints as src/agent/news/sources.py.
const UA = { "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) trade-signal-agent" };
const BINANCE =
  "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20";
const OKX = "https://www.okx.com/api/v5/support/announcements?annType=";
const OKX_TYPES = ["announcements-new-listings", "announcements-delistings"];

async function getJson(url) {
  const r = await fetch(url, { headers: UA });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

export function parseBinance(body) {
  const out = [];
  for (const cat of body?.data?.catalogs || []) {
    for (const a of cat.articles || []) {
      out.push({
        id: `binance:${a.id}`, source: "binance", kind: "exchange", title: a.title.trim(),
        url: `https://www.binance.com/en/support/announcement/detail/${a.code}`, ts: Number(a.releaseDate),
      });
    }
  }
  return out;
}

export function parseOkx(body) {
  const out = [];
  for (const page of body?.data || []) {
    for (const a of page.details || []) {
      out.push({ id: `okx:${a.url}`, source: "okx", kind: "exchange", title: a.title.trim(), url: a.url, ts: Number(a.pTime) });
    }
  }
  return out;
}

export async function fetchAll() {
  const jobs = [["binance", async () => parseBinance(await getJson(BINANCE))]];
  for (const t of OKX_TYPES) jobs.push([`okx ${t}`, async () => parseOkx(await getJson(OKX + t))]);
  const items = [];
  const failed = [];
  for (const [name, job] of jobs) {
    try {
      items.push(...(await job()));
    } catch (e) {
      failed.push(`${name}: ${e.message}`);
    }
  }
  return { items, failed };
}
