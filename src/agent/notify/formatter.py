"""Persian Telegram messages. The signal message follows the template in STRATEGY.md.
All text is built from templates and check results; no paid API is involved."""
from __future__ import annotations

import math
from datetime import datetime, timezone

REGIME_FA = {
    "alt_season": "آلت‌سیزن",
    "btc_led": "رشد به رهبری BTC",
    "risk_off": "Risk-off",
    "capitulation": "ریزش همه‌جانبه",
    "neutral": "خنثی/رنج",
}
DIR_FA = {1: "صعودی", 0: "رنج", -1: "نزولی"}
ARROW = {1: "↑", 0: "↔", -1: "↓"}
PHASE_FA = {"TREND_UP": "TREND↑", "TREND_DOWN": "TREND↓", "ACC": "ACC", "DIST": "DIST",
            "RANGE": "RANGE"}
CYCLE_4H_FA = {
    "fresh_turn": "برگشت از اصلاح ✅",
    "lower_correcting": "در حال اصلاح ⏳",
    "late": "موج حرکتی دیرهنگام ⚠️",
    "lower_impulse": "در موج حرکتی",
    "higher_ranging": "هم‌جهت با D (W رنج)",
    "counter_trend": "خلاف روند اصلی ⚠️",
    "not_aligned": "ناهم‌جهت ❌",
}
ORDER_FA = {"limit": "لیمیت", "market": "مارکت"}
TRIGGER_FA = {"engulfing": "انگالف", "pin_bar": "پین‌بار", "strong_close": "کندل قدرتمند"}
PATTERN_FA = {
    "double_bottom": "کف دوقلو", "double_top": "سقف دوقلو",
    "inverse_head_shoulders": "سر و شانه‌ی معکوس", "head_shoulders": "سر و شانه",
    "triangle": "مثلث", "rising_wedge": "وج صعودی", "falling_wedge": "وج نزولی",
    "bull_flag": "فلگ صعودی", "bear_flag": "فلگ نزولی",
}


def fmt_price(p: float) -> str:
    """Two decimals for prices >= 100, more for smaller prices (4 significant digits)."""
    if p is None or p == 0:
        return "0"
    decimals = min(8, max(2, 3 - math.floor(math.log10(abs(p)))))
    return f"{p:.{decimals}f}"


def fmt_level(p: float) -> str:
    s = fmt_price(p)
    return s.rstrip("0").rstrip(".") if "." in s else s


def fmt_num(x: float, decimals: int = 1) -> str:
    """1.0 -> '1', 0.5 -> '0.5', 29.41 -> '29'."""
    s = f"{x:.{decimals}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def pair(base: str, quote: str = "USDT") -> str:
    return f"{base}{quote}"


def side_word(side: int) -> str:
    return "LONG" if side == 1 else "SHORT"


def phase_text(ev) -> str:
    d = ev.phase_d.split(":")[0]
    h4 = ev.phase_4h.split(":")[0]
    same = PHASE_FA.get(d) == PHASE_FA.get(h4)
    return f"{PHASE_FA.get(d, d)} ({'D/4H' if same else 'D'})"


def reason_text(ev) -> str:
    """Short explanation built from the checks that passed."""
    p = ev.plan
    side = ev.side
    parts = []
    lvl = fmt_level(p.support)
    word = "حمایت" if side == 1 else "مقاومت"
    entry = {"flip": f"پیولبک به فلیپ {lvl}",
             "prev_high": f"برگشت به سقف قبلی {lvl}",
             "prev_low": f"برگشت به کف قبلی {lvl}",
             "round": f"واکنش به عدد رند {lvl}"}.get(p.support_kind, f"پیولبک به {word} {lvl}")
    if "pullback_volume" in ev.notes:
        entry += " با حجم کاهشی"
    elif "breakout_volume" in ev.notes:
        entry += " بعد از شکست با حجم بالا"
    parts.append(entry)
    dir_word = "صعودی" if side == 1 else "نزولی"
    conf = set(ev.confirmations)
    if "choch" in conf:
        parts.append(f"CHoCH {dir_word} در 1H")
    if "rsi" in conf:
        parts.append("RSI بالای 50" if side == 1 else "RSI زیر 50")
    if "macd" in conf:
        parts.append("تغییر رنگ هیستوگرام MACD")
    if "trigger_candle" in conf:
        parts.append("کندل تریگر در 1H")
    if "rvol" in conf:
        parts.append("حجم بالا در کندل تریگر")
    pats = [PATTERN_FA.get(n.split(":", 1)[1], n) for n in ev.notes if n.startswith("pattern:")]
    if pats:
        parts.append("الگوی " + " و ".join(pats))
    return "، ".join(parts)


def check(ok: bool) -> str:
    return "✅" if ok else "❌"


def signal_message(ev, regime: dict, majors: dict, active_count: int, cfg: dict,
                   out_of_cap: bool = False) -> str:
    p = ev.plan
    side = ev.side
    grade = "A+" if ev.a_plus else ev.grade
    head = "🟢" if side == 1 else "🔴"
    lines = [f"{head} {side_word(side)} | {pair(ev.base)} (فیوچرز) | رده {grade} (امتیاز {round(ev.score)})"]
    if out_of_cap:
        lines.append("⚠️ خارج از سقف: سیگنال‌های فعال پر است؛ تصمیم با خودتان")
    lines += [
        f"رژیم: {REGIME_FA.get(regime['name'], regime['name'])} | BTC: {majors['btc']:+d} | "
        f"ETHBTC: {DIR_FA[majors.get('ethbtc_d', 0)]}",
        f"سایکل‌ها: W{ARROW[ev.cycle_w]} | D{ARROW[ev.cycle_d]} | 4H {CYCLE_4H_FA.get(ev.cycle, ev.cycle)}",
        f"ستاپ: {' + '.join(ev.labels) or '-'} | فاز: {phase_text(ev)}",
        "",
        "تایم‌فریم: ستاپ 4H / ورود 1H",
        f"ورود: {fmt_price(p.entry_low)} – {fmt_price(p.entry_high)} ({ORDER_FA[p.order]})",
        f"حد ضرر: {fmt_price(p.sl)} ({'-' if side == 1 else '+'}{p.sl_pct:.1f}%)",
        f"TP1: {fmt_price(p.tp1)} ({p.tp1_r:.1f}R) – بستن {cfg['trade']['tp1_close_pct']}% و حد ضرر به ورود",
        f"TP2: {fmt_price(p.tp2)} ({p.tp2_r:.1f}R) – بستن {cfg['trade']['tp2_close_pct']}%",
        "TP3: تریل روی MA25 در 4H",
        f"ریسک به ریوارد تا TP2: 1 به {p.tp2_r:.1f}",
        f"ریسک: {fmt_num(p.risk_pct, 2)}% موجودی ← حجم پوزیشن ≈ {round(p.size_pct)}% موجودی",
        f"لوریج پیشنهادی: {p.leverage}x ایزوله (مارجین ≈ {p.margin_pct:.1f}% موجودی)",
        f"فاندینگ: {'-' if ev.funding_pct is None else f'{ev.funding_pct:.2f}%'} | "
        f"سیگنال‌های فعال: {active_count} از {cfg['lifecycle']['max_active']}",
        "",
    ]
    s = ev.sections
    n_conf = len(ev.confirmations)
    lines.append(
        f"چک‌لیست: فاز {check(ev.gates.get('phase', False))} داو {check(s.get('dow', 0) > 0)} "
        f"S/R {check(s.get('levels', 0) > 0)} حجم {check(s.get('volume', 0) > 0)} "
        f"کندل {check(s.get('candles', 0) > 0)} سایکل {check(s.get('cycles', 0) > 0)} "
        f"اندیکاتورها {check(s.get('patterns', 0) > 0)} "
        f"تایید ({n_conf}/5) {check(ev.gates.get('confirmation', False))}")
    lines.append(f"دلیل: {reason_text(ev)}")
    beyond = "زیر" if side == 1 else "بالای"
    hours = cfg["lifecycle"]["expiry_bars_4h"] * 4
    lines.append(f"انقضا: {hours} ساعت | ابطال: کلوز 4H {beyond} {fmt_price(p.sl)}")
    return "\n".join(lines)


WATCH_REASON_FA = {
    "not_confirmed": "منتظر تایید ورود در 1H",
    "lower_correcting": "اصلاح 4H هنوز تمام نشده",
    "lower_cycle_correcting": "اصلاح 4H هنوز تمام نشده",
    "chase": "کندل خیلی بزرگ؛ منتظر پیولبک",
    "btc_weak": "BTC ضعیف؛ لانگ آلت فقط با امتیاز 80+",
    "neutral_regime_needs_A": "رژیم خنثی؛ فقط رده A",
    "funding_crowded": "فاندینگ شلوغ در جهت معامله",
}


def watch_message(ev) -> str:
    why = [WATCH_REASON_FA.get(f, f) for f in ev.flags] or ["امتیاز در محدوده‌ی Watch"]
    lines = [f"👀 نزدیک ستاپ (Watch) | {pair(ev.base)} {side_word(ev.side)} | امتیاز {round(ev.score)}",
             f"فاز: {phase_text(ev)} | ستاپ: {' + '.join(ev.labels) or '-'}"]
    if ev.plan:
        lines.append(f"سطح: {fmt_price(ev.plan.support)} | منطقه‌ی ورود: "
                     f"{fmt_price(ev.plan.entry_low)} – {fmt_price(ev.plan.entry_high)}")
    lines.append("منتظر: " + "، ".join(why))
    return "\n".join(lines)


def event_message(sig: dict, e, cfg: dict) -> str:
    """Updates for an existing signal: fill, targets, stop, expiry, cancellation."""
    base, side = sig["symbol"], 1 if sig["side"] == "long" else -1
    head = f"{pair(base)} {side_word(side)}"
    t = cfg["trade"]
    k = e.kind
    if k == "filled":
        return f"✅ ورود فعال شد | {head} | قیمت ورود: {fmt_price(e.price)}"
    if k == "tp1":
        return (f"🎯 TP1 خورد | {head} | {fmt_price(e.price)} ({e.r:+.1f}R) – "
                f"{t['tp1_close_pct']}% بسته شد و حد ضرر به نقطه‌ی ورود منتقل شد")
    if k == "tp2":
        return (f"🎯 TP2 خورد | {head} | {fmt_price(e.price)} ({e.r:+.1f}R) – "
                f"{t['tp2_close_pct']}% بسته شد؛ باقی‌مانده با تریل روی MA25 در 4H")
    if k == "tp3":
        why = "کلوز 4H آن طرف MA25" if e.reason == "ma25" else "شکست ساختار مخالف"
        return f"🏁 خروج نهایی ({why}) | {head} | {fmt_price(e.price)} | نتیجه‌ی کل: {e.r:+.2f}R"
    if k == "sl":
        return f"🛑 حد ضرر خورد | {head} | {fmt_price(e.price)} | نتیجه: {e.r:+.2f}R"
    if k == "breakeven":
        return f"⚪️ حد ضرر در نقطه‌ی ورود خورد | {head} | نتیجه‌ی کل: {e.r:+.2f}R"
    if k == "expired":
        hours = cfg["lifecycle"]["expiry_bars_4h"] * 4
        return f"⌛️ سیگنال منقضی شد | {head} | ورود در {hours} ساعت فعال نشد"
    if k == "cancelled":
        why = {"tp1_before_entry": "قیمت قبل از ورود به TP1 رسید",
               "closed_beyond_sl": f"کلوز 4H {'زیر' if side == 1 else 'بالای'} حد ضرر"}.get(e.reason, e.reason)
        return f"❌ سیگنال لغو شد | {head} | دلیل: {why}"
    return f"{head}: {k}"


def pause_message(until_ms: int) -> str:
    until = datetime.fromtimestamp(until_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"⏸ ترمز ضرر: ۳ حد ضرر پیاپی؛ تا کلوز کندل روزانه ({until}) سیگنال جدید صادر نمی‌شود"


def btc_cycle_text(now_ms: int, cfg: dict) -> str:
    """Days since the last halving, for context only."""
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).date()
    past = [datetime.strptime(d, "%Y-%m-%d").date() for d in cfg["daily"]["btc_halvings"]]
    past = [d for d in past if d <= now]
    if not past:
        return "-"
    last = max(past)
    return f"روز {(now - last).days} پس از هاوینگ {last.isoformat()}"


def daily_message(funnel: dict, open_signals: list[dict], now_ms: int, cfg: dict) -> str:
    r, m = funnel["regime"], funnel["majors"]
    date = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    lines = [f"📊 گزارش روزانه {date}",
             f"رژیم: {REGIME_FA.get(r['name'], r['name'])} (USDT.D {DIR_FA[r['usdt_d']]}، "
             f"BTC.D {DIR_FA[r['btc_d']]}، TOTAL2 {DIR_FA[r['total2']]})",
             f"BTC: {m['btc']:+d} | ETH: {m['eth']:+d} | ETHBTC: {m['ethbtc']:+d} ({DIR_FA[m.get('ethbtc_d', 0)]})"]
    warn = []
    if m.get("btc_weak"):
        warn.append("BTC ضعیف (لانگ آلت فقط با امتیاز 80+)")
    if m.get("divergence"):
        warn.append("واگرایی BTC و TOTAL2 (ریسک نصف)")
    if warn:
        lines.append("هشدار: " + "، ".join(warn))
    lines.append(f"سایکل ۴ ساله‌ی BTC: {btc_cycle_text(now_ms, cfg)}")
    if funnel.get("hot_categories"):
        lines.append("دسته‌های داغ: " + "، ".join(funnel["hot_categories"]))
    lines.append("")
    lines.append("واچ‌لیست گلچین:")
    for c in funnel.get("shortlist", []):
        lines.append(f"• {pair(c['base'])} {side_word(c['side'])} – امتیاز {round(c['score'])} – "
                     f"{' + '.join(c['labels'])}")
    if not funnel.get("shortlist"):
        lines.append("• -")
    lines.append("")
    lines.append(f"سیگنال‌های باز: {len(open_signals)} از {cfg['lifecycle']['max_active']}")
    for s in open_signals:
        lines.append(f"• {pair(s['symbol'])} {s['side'].upper()} – {s['status']} "
                     f"(رده {s['grade']}، امتیاز {round(s['score'])})")
    return "\n".join(lines)


HOLD_FA = {
    "w_cycle": "W صعودی/شکست انباشت",
    "btc_pair_hl": "کف بالاتر در جفت BTC (W)",
    "ma99_d": "MA99 روزانه صعودی",
    "rs_90d": "قدرت نسبی ۹۰ روزه",
    "hot_category": "دسته‌ی داغ ۳۰ روزه",
    "ath_acc": "۶۰٪+ زیر سقف تاریخی و انباشت W",
}


def hold_message(ideas, now_ms: int, new_only: bool = False) -> str:
    date = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    title = "💎 کوین جدید در فهرست HOLD (اسپات)" if new_only else f"💎 گزارش هفتگی HOLD (اسپات) {date}"
    lines = [title]
    if not ideas:
        lines.append("امروز کوینی حداقل ۴ از ۶ شرط را ندارد.")
    for i in ideas:
        lines.append("")
        lines.append(f"• {i.base} — {i.count} از 6 شرط")
        lines.append("  " + " | ".join(f"{HOLD_FA[k]} {check(v)}" for k, v in i.met.items()))
        if i.buy_steps:
            lines.append("  خرید پله‌ای: " + " / ".join(fmt_price(p) for p in i.buy_steps))
        if i.invalidation:
            lines.append(f"  ابطال: کلوز هفتگی زیر {fmt_price(i.invalidation)}")
        if i.targets:
            lines.append("  اهداف: " + " / ".join(fmt_price(p) for p in i.targets))
    return "\n".join(lines)
