// Same layout as src/agent/news/format.py; keep the two in step.
const HEAD = { 2: "🚀 خبر خیلی مثبت", 1: "📰 خبر مثبت", "-1": "⚠️ خبر منفی", "-2": "⛔️ خبر خیلی منفی" };
const SOURCE_FA = { binance: "Binance", okx: "OKX" };
const GRADE_FA = { A: "رده A", B: "رده B", Watch: "Watch" };
export const FOOTER = "ℹ️ فقط اطلاع‌رسانی است و سیگنال معامله نیست.";

export function ago(ms) {
  const m = Math.max(0, Math.floor(ms / 60000));
  if (m < 1) return "همین الان";
  if (m < 60) return `${m} دقیقه پیش`;
  return `${Math.floor(m / 60)} ساعت پیش`;
}

const side = (s) => (s === 1 || s === "long" ? "LONG" : "SHORT");

export function coinStatus(coin, ctx) {
  const c = ctx?.coins?.[coin] || {};
  if (c.signal) return `${coin}: سیگنال باز ${side(c.signal.side)} ${GRADE_FA[c.signal.grade] || c.signal.grade}`;
  if (c.eval) {
    const score = c.eval.score != null ? ` امتیاز ${Math.round(c.eval.score)}` : "";
    return `${coin}: ${GRADE_FA[c.eval.grade] || c.eval.grade} ${side(c.eval.side)}${score}`;
  }
  if (c.watch) return `${coin}: در لیست بررسی، هنوز ستاپ ندارد`;
  if (ctx && Array.isArray(ctx.perp_bases) && !ctx.perp_bases.includes(coin)) return `${coin}: در OKX فیوچرز نیست`;
  if (!ctx) return `${coin}: وضعیت تکنیکال در دسترس نیست`;
  return `${coin}: ستاپ تکنیکال ندارد`;
}

export function newsMessage(item, score, coins, ctx, now) {
  const when = item.ts ? ` · ${ago(now - item.ts)}` : "";
  const lines = [`${HEAD[score.level] || "📰 خبر"} | ${coins.join(", ")}`];
  if (score.fa) lines.push(score.fa);
  lines.push(`«${item.title}»`, `منبع: ${SOURCE_FA[item.source] || item.source}${when}`, "وضعیت تکنیکال:");
  for (const c of coins.slice(0, 5)) lines.push(`• ${coinStatus(c, ctx)}`);
  if (item.url) lines.push(item.url);
  lines.push(FOOTER);
  return lines.join("\n");
}
