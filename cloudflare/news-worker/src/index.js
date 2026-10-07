// Fast news path: every minute, read Binance and OKX announcements and send a Telegram alert
// for positive / very positive news about a coin (rules shared with the Python side).
// State: the ids already seen, in Workers KV (written only when something new appears).
import { classify, exchangeCoins } from "./rules.js";
import { fetchAll } from "./sources.js";
import { newsMessage } from "./format.js";

const KEEP_IDS = 600;

export async function step(env, now, deps = {}) {
  const fetchItems = deps.fetchAll || fetchAll;
  const send = deps.send || ((text) => telegram(env, text));
  const getContext = deps.context || (() => context(env));
  const minLevel = Number(env.MIN_ALERT_LEVEL ?? 1);
  const maxAge = Number(env.MAX_AGE_MIN ?? 180) * 60000;

  const { items, failed } = await fetchItems();
  const raw = await env.NEWS_KV.get("seen");
  const seen = raw ? JSON.parse(raw) : null;
  if (!items.length) return { seeded: false, new: 0, alerts: 0, failed };
  if (seen === null) {                          // first run: remember everything, alert nothing
    await env.NEWS_KV.put("seen", JSON.stringify(items.map((i) => i.id).slice(0, KEEP_IDS)));
    return { seeded: true, new: items.length, alerts: 0, failed };
  }
  const known = new Set(seen);
  const fresh = items.filter((i) => !known.has(i.id)).sort((a, b) => (a.ts || 0) - (b.ts || 0));
  if (!fresh.length) return { seeded: false, new: 0, alerts: 0, failed };

  let ctx = null;
  let alerts = 0;
  for (const item of fresh) {
    const sc = classify(item.title, item.source);
    const coins = exchangeCoins(item.title);
    const recent = !item.ts || now - item.ts <= maxAge;
    if (!coins.length || !recent || sc.level === 0) continue;
    if (sc.level < minLevel) {
      // negative news only matters for a coin with an open signal
      ctx = ctx ?? (await getContext());
      if (!coins.some((c) => ctx?.coins?.[c]?.signal)) continue;
    }
    ctx = ctx ?? (await getContext());
    await send(newsMessage(item, sc, coins, ctx, now));
    alerts += 1;
  }
  const ids = [...fresh.map((i) => i.id), ...seen].slice(0, KEEP_IDS);
  await env.NEWS_KV.put("seen", JSON.stringify(ids));
  return { seeded: false, new: fresh.length, alerts, failed };
}

async function context(env) {
  if (!env.CONTEXT_URL) return null;
  try {
    const r = await fetch(env.CONTEXT_URL, { cf: { cacheTtl: 60 } });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

async function telegram(env, text) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, disable_web_page_preview: true }),
  });
  if (!r.ok) console.log(`telegram failed: HTTP ${r.status}`);
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(step(env, Date.now()).then((r) => console.log(JSON.stringify(r))));
  },
  async fetch() {
    return new Response("trade-news worker: runs every minute\n");
  },
};
