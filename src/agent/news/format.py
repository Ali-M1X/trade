"""Persian Telegram text for a news alert. The Cloudflare worker builds the same layout
(cloudflare/news-worker/src/format.js); keep the two in step."""
from __future__ import annotations

HEAD = {2: "🚀 خبر خیلی مثبت", 1: "📰 خبر مثبت", -1: "⚠️ خبر منفی", -2: "⛔️ خبر خیلی منفی"}
SOURCE_FA = {"binance": "Binance", "okx": "OKX", "coindesk": "CoinDesk", "cointelegraph": "Cointelegraph",
             "theblock": "The Block", "decrypt": "Decrypt", "blockworks": "Blockworks",
             "bitcoinmagazine": "Bitcoin Magazine", "thedefiant": "The Defiant"}
GRADE_FA = {"A": "رده A", "B": "رده B", "Watch": "Watch"}
FOOTER = "ℹ️ فقط اطلاع‌رسانی است و سیگنال معامله نیست."


def ago(ms: int) -> str:
    m = max(0, ms // 60_000)
    if m < 1:
        return "همین الان"
    if m < 60:
        return f"{m} دقیقه پیش"
    return f"{m // 60} ساعت پیش"


def coin_status(coin: str, ctx: dict) -> str:
    """One line: what the signal system says about the coin right now."""
    c = (ctx.get("coins") or {}).get(coin, {})
    side = lambda s: "LONG" if s in (1, "long") else "SHORT"   # noqa: E731
    if "signal" in c:
        s = c["signal"]
        return f"{coin}: سیگنال باز {side(s['side'])} {GRADE_FA.get(s['grade'], s['grade'])}"
    if "eval" in c:
        e = c["eval"]
        score = f" امتیاز {round(e['score'])}" if e.get("score") is not None else ""
        return f"{coin}: {GRADE_FA.get(e['grade'], e['grade'])} {side(e['side'])}{score}"
    if c.get("watch"):
        return f"{coin}: در لیست بررسی، هنوز ستاپ ندارد"
    if coin not in set(ctx.get("perp_bases") or []):
        return f"{coin}: در OKX فیوچرز نیست"
    return f"{coin}: ستاپ تکنیکال ندارد"


def news_message(item: dict, score, coins: list[str], ctx: dict, now: int) -> str:
    when = f" · {ago(now - item['ts'])}" if item.get("ts") else ""
    src = SOURCE_FA.get(item["source"], item["source"])
    lines = [f"{HEAD.get(score.level, '📰 خبر')} | {', '.join(coins)}"]
    if score.fa:
        lines.append(score.fa)
    lines += [f"«{item['title']}»", f"منبع: {src}{when}", "وضعیت تکنیکال:"]
    lines += [f"• {coin_status(c, ctx)}" for c in coins[:5]]
    if item.get("url"):
        lines.append(item["url"])
    lines.append(FOOTER)
    return "\n".join(lines)
