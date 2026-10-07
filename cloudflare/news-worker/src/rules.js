// Same scoring as src/agent/news/classify.py, from the same rules.json.
import RAW from "../../../src/agent/news/rules.json" with { type: "json" };

const IGNORE = RAW.ignore.map((p) => new RegExp(p, "i"));
const RULES = RAW.rules.map((r) => ({ ...r, rx: new RegExp(r.pattern, "i") }));
const STOP = new Set(RAW.coin_stopwords.map((w) => w.toUpperCase()));

export const LEVEL_FA = { 2: "خیلی مثبت", 1: "مثبت", 0: "خنثی", "-1": "منفی", "-2": "خیلی منفی" };

export function classify(title, source) {
  if (IGNORE.some((rx) => rx.test(title))) return { level: 0, rule: "ignored", fa: "" };
  const kind = source === "binance" || source === "okx" ? source : "media";
  for (const r of RULES) {
    if ((r.source || [kind]).includes(kind) && r.rx.test(title)) {
      return { level: r.level, rule: r.id, fa: r.fa };
    }
  }
  return { level: 0, rule: null, fa: "" };
}

const COIN_RX = [
  /\(([A-Z0-9]{2,12})\)/g,
  /\b([A-Z0-9]{2,15})USDT\b/g,
  /\b([A-Z0-9]{2,12})\/(?:USDT|USDC|USD)\b/g,
  /\bfor ([A-Z0-9]{2,12})(?:,| and| crypto|$)/g,
];

export function exchangeCoins(title) {
  const out = [];
  for (const rx of COIN_RX) {
    for (const m of title.matchAll(rx)) {
      const t = m[1].toUpperCase();
      if (!STOP.has(t) && !out.includes(t)) out.push(t);
    }
  }
  return out;
}
