import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { classify, exchangeCoins } from "../src/rules.js";
import { parseBinance, parseOkx } from "../src/sources.js";
import { newsMessage, coinStatus } from "../src/format.js";
import { step } from "../src/index.js";

// the same vectors as tests/test_news.py, so both paths score titles identically
const VECTORS = JSON.parse(readFileSync(new URL("../../../tests/fixtures/news_titles.json", import.meta.url)));

for (const v of VECTORS) {
  test(`classify: ${v.title.slice(0, 50)}`, () => {
    const s = classify(v.title, v.source);
    assert.deepEqual([s.level, s.rule], [v.level, v.rule]);
    if (v.coins !== null) assert.deepEqual(exchangeCoins(v.title), v.coins);
  });
}

test("parse exchange payloads", () => {
  const b = parseBinance({ data: { catalogs: [{ articles: [{ id: 7, code: "c7", title: " Binance Will List X (X) ", releaseDate: 5 }] }] } });
  assert.deepEqual(b, [{ id: "binance:7", source: "binance", kind: "exchange", title: "Binance Will List X (X)",
    url: "https://www.binance.com/en/support/announcement/detail/c7", ts: 5 }]);
  const o = parseOkx({ data: [{ details: [{ title: "OKX to list perpetual futures for QNT crypto", url: "u", pTime: "9" }] }] });
  assert.equal(o[0].id, "okx:u");
  assert.equal(o[0].ts, 9);
});

test("message layout matches the Python one", () => {
  const now = 1791400000000;
  const item = { source: "binance", title: "Binance Will List Hyperliquid (HYPE) with Seed Tag Applied",
    url: "https://www.binance.com/en/support/announcement/detail/c1", ts: now - 2 * 60000 };
  const msg = newsMessage(item, classify(item.title, "binance"), ["HYPE"], { perp_bases: ["HYPE"], coins: {} }, now);
  assert.deepEqual(msg.split("\n"), [
    "🚀 خبر خیلی مثبت | HYPE", "لیستینگ اسپات بایننس",
    "«Binance Will List Hyperliquid (HYPE) with Seed Tag Applied»",
    "منبع: Binance · 2 دقیقه پیش", "وضعیت تکنیکال:", "• HYPE: ستاپ تکنیکال ندارد",
    "https://www.binance.com/en/support/announcement/detail/c1",
    "ℹ️ فقط اطلاع‌رسانی است و سیگنال معامله نیست."]);
  assert.equal(coinStatus("SOL", { perp_bases: ["SOL"], coins: { SOL: { signal: { side: "long", grade: "B" } } } }),
    "SOL: سیگنال باز LONG رده B");
  assert.equal(coinStatus("SOL", null), "SOL: وضعیت تکنیکال در دسترس نیست");
});

class KV {
  constructor() { this.data = new Map(); this.writes = 0; }
  async get(k) { return this.data.get(k) ?? null; }
  async put(k, v) { this.data.set(k, v); this.writes += 1; }
}

test("first run seeds, then only new positive coin news is sent once", async () => {
  const now = 1791400000000;
  const env = { NEWS_KV: new KV() };
  let items = [
    { id: "binance:1", source: "binance", kind: "exchange", title: "Binance Will List Old (OLD)", url: "u1", ts: now - 60000 },
  ];
  const sent = [];
  const deps = { fetchAll: async () => ({ items, failed: [] }), send: async (t) => sent.push(t), context: async () => null };
  assert.equal((await step(env, now, deps)).seeded, true);
  assert.equal(sent.length, 0);
  // nothing new: no KV write
  const w = env.NEWS_KV.writes;
  await step(env, now, deps);
  assert.equal(env.NEWS_KV.writes, w);
  items = [
    { id: "binance:2", source: "binance", kind: "exchange", title: "Binance Will List Hyperliquid (HYPE) with Seed Tag Applied", url: "u2", ts: now - 30000 },
    { id: "binance:3", source: "binance", kind: "exchange", title: "Binance Will Add 4 bStocks Tokenized Securities as Collateral Asset", url: "u3", ts: now },
    { id: "binance:4", source: "binance", kind: "exchange", title: "Binance Will List Ancient (ANC)", url: "u4", ts: now - 10 * 3600000 },
    { id: "binance:5", source: "binance", kind: "exchange", title: "Binance Will Delist ABC (ABC)", url: "u5", ts: now },
    ...items,
  ];
  const r = await step(env, now, deps);
  assert.equal(r.new, 4);
  assert.equal(sent.length, 1);                 // listing yes; bStocks, too old, delist without a signal: no
  assert.ok(sent[0].startsWith("🚀 خبر خیلی مثبت | HYPE"));
  await step(env, now + 60000, deps);           // already seen: not sent again
  assert.equal(sent.length, 1);
});

test("negative news is sent when the coin has an open signal", async () => {
  const now = 1791400000000;
  const env = { NEWS_KV: new KV() };
  await env.NEWS_KV.put("seen", "[]");
  const sent = [];
  await step(env, now, {
    fetchAll: async () => ({ items: [{ id: "okx:x", source: "okx", kind: "exchange", title: "OKX to delist ABC/USDT spot pair", url: "x", ts: now }], failed: [] }),
    send: async (t) => sent.push(t),
    context: async () => ({ perp_bases: ["ABC"], coins: { ABC: { signal: { side: "long", grade: "A" } } } }),
  });
  assert.equal(sent.length, 1);
  assert.ok(sent[0].startsWith("⛔️ خبر خیلی منفی | ABC"));
});
