"""Persian Telegram messages.

Every coin is shown as ONE line with the same fields in the same order, and the only score
ever shown is the final L6 technical score:

    <icon> <COIN>USDT | <LONG|SHORT> | امتیاز <score> | ورود <entry> | SL <sl> | TP1 <tp1> | TP2 <tp2> | <reason>

🟢 = signal (all gates passed, grade A/B), 🟡 = Watch (near setup). Messages that show a
score end with SCORE_EXPLANATION. All text comes from templates and check results.
"""
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

SIGNAL_ICON, WATCH_ICON = "🟢", "🟡"
REASON_MAX = 60                      # characters; parts that don't fit are dropped, never wrapped
TF_FA = {"4h": "4H", "1d": "D", "1w": "W", "1h": "1H", "round": ""}

# The one dictionary of reason phrases. Keys are the evaluation's flags, labels, 1H
# confirmations, notes and the kind of level the entry leans on.
REASON_FA = {
    # level the entry leans on ({lvl} = حمایت / مقاومت, {tf} = its timeframe)
    "level:cluster": "پولبک به {lvl} {tf}",
    "level:flip": "ریتست سطح شکسته {tf}",
    "level:prev_high": "برگشت به سقف قبلی {tf}",
    "level:prev_low": "برگشت به کف قبلی {tf}",
    "level:round": "واکنش به عدد رند",
    # 1H confirmations
    "trigger:engulfing": "انگالف 1H",
    "trigger:pin_bar": "پین‌بار 1H",
    "trigger:strong_close": "کندل قوی 1H",
    "choch": "تغییر ساختار 1H",
    "rsi:1": "RSI بالای 50",
    "rsi:-1": "RSI زیر 50",
    "macd": "چرخش MACD",
    "rvol": "حجم بالا 1H",
    # volume on 4H
    "pullback_volume": "حجم کاهشی",
    "breakout_volume": "شکست با حجم",
    # scanner labels
    "EARLY_TREND": "شکست رنج",
    "RS_LEADER": "قوی‌تر از BTC",
    "PULLBACK": "اصلاح سالم",
    "SQUEEZE": "فشردگی قبل از حرکت",
    "EXHAUSTION": "خستگی حرکت",
    "OI_BUILDUP": "رشد اوپن‌اینترست",
    "FUNDING_EXTREME": "فاندینگ افراطی",
    "VOLUME_ANOMALY": "حجم غیرعادی",
    "HOT_SECTOR": "بخش داغ",
    "BREAKOUT_WATCH": "شکست اخیر",
    # chart patterns (notes "pattern:<name>")
    "pattern:double_bottom": "کف دوقلو", "pattern:double_top": "سقف دوقلو",
    "pattern:inverse_head_shoulders": "سر و شانه معکوس", "pattern:head_shoulders": "سر و شانه",
    "pattern:triangle": "مثلث", "pattern:rising_wedge": "وج صعودی",
    "pattern:falling_wedge": "وج نزولی", "pattern:bull_flag": "فلگ صعودی",
    "pattern:bear_flag": "فلگ نزولی",
    # why a setup is only Watch / special cases
    "not_confirmed": "منتظر تأیید 1H",
    "lower_cycle_correcting": "اصلاح 4H ادامه دارد",
    "chase": "منتظر پولبک",
    "btc_weak": "BTC ضعیف",
    "neutral_regime_needs_A": "رژیم خنثی فقط A",
    "funding_crowded": "فاندینگ شلوغ",
    "out_of_cap": "خارج از سقف",
    "not_issued": "صادر نشد",          # A/B setup the signal book held back (cap, correlation, ...)
}

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


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


def fa(x) -> str:
    return str(x).translate(FA_DIGITS)


def pair(base: str, quote: str = "USDT") -> str:
    return f"{base}{quote}"


def side_word(side: int) -> str:
    return "LONG" if side == 1 else "SHORT"


def shown_score(score: float) -> int:
    """The integer score users see: rounded down, so it never crosses a grade threshold
    the real score is below (74.6 is grade B and is shown as 74, not 75)."""
    return math.floor(score + 1e-9)


# ------------------------------------------------------------ score explanation
def _risk_words(share: float) -> str:
    if share >= 1:
        return "ریسک کامل"
    if share == 0.5:
        return "نصف ریسک"
    return f"{fa(fmt_num(share * 100, 0))}٪ ریسک"


def score_explanation(cfg: dict) -> str:
    """One paragraph explaining the L6 score, built from the scoring config so it always
    matches what the code does."""
    t, g = cfg["technical"], cfg["grades"]
    parts = [
        ("فاز بازار", t["phase"]["points_both"], False),
        ("ساختار سقف و کف", t["dow"]["points_both"], False),
        ("حمایت و مقاومت", t["levels"]["points_strong"], False),
        ("حجم", t["volume"]["max_points"], False),
        ("کندل و پرایس‌اکشن", t["candles"]["max_points"], False),
        ("سایکل‌ها", t["cycles"]["points_all_aligned_fresh"], False),
        ("الگوها و سلامت روند", t["patterns"]["max_points"], True),
        ("تأیید ورود", t["confirmation"]["max_points"], False),
    ]
    sections = "، ".join(f"{name} {'تا ' if upto else ''}{fa(fmt_num(pts, 1))}"
                         for name, pts, upto in parts[:-1])
    last_name, last_pts, _ = parts[-1]
    sections += f" و {last_name} {fa(fmt_num(last_pts, 1))}"
    rr = fa(fmt_num(cfg["trade"]["tp1_min_r"], 1))           # the R:R gate in build_trade
    conf = t["confirmation"]["min_confirmations"]
    conf_word = {1: "یک", 2: "دو", 3: "سه", 4: "چهار", 5: "پنج"}.get(conf, fa(conf))
    a, b, w = g["A"], g["B"], g["watch"]
    share = g["risk_share"]
    return (
        "ℹ️ امتیاز (۰ تا ۱۰۰): هر کوین بعد از عبور از قیف بازار، در تحلیل تکنیکال این "
        f"بخش‌ها را می‌گیرد: {sections}. "
        "امتیاز بالا به‌تنهایی کافی نیست: سیگنال فقط وقتی صادر می‌شود که چهار شرط اجباری "
        f"برقرار باشد (هم‌جهت با رژیم بازار، فاز مجاز، ریسک به ریوارد حداقل ۱ به {rr} و "
        f"حداقل {conf_word} تأیید ورود)؛ اگر شرطی هنوز کامل نیست، ستاپ با هر امتیازی فقط Watch است. "
        f"بازه‌ها: {fa(a)} به بالا رده A با {_risk_words(share['A'])}، "
        f"{fa(b)} تا {fa(a - 1)} رده B با {_risk_words(share['B'])}، "
        f"{fa(w)} تا {fa(b - 1)} فقط Watch (هشدار نزدیک ستاپ، بدون ورود) "
        f"و زیر {fa(w)} چیزی ارسال نمی‌شود."
    )


# ------------------------------------------------------------------ coin line
def reason(ev: dict, out_of_cap: bool = False) -> str:
    """Short reason (<= REASON_MAX characters) from the evaluation's checks, most
    important first. Parts that don't fit are left out."""
    side, plan = ev["side"], ev["plan"]
    lvl_word = "حمایت" if side == 1 else "مقاومت"
    level = REASON_FA[f"level:{plan['support_kind']}"].format(
        lvl=lvl_word, tf=TF_FA.get(plan["support_tf"], plan["support_tf"])).strip()
    labels = [REASON_FA[l] for l in ev.get("labels", []) if l in REASON_FA]
    notes = ev.get("notes", [])
    if ev["grade"] in ("A", "B"):
        conf = set(ev.get("confirmations", []))
        parts = (["out_of_cap"] if out_of_cap else []) + [level]
        if ev.get("trigger_1h"):
            parts.append(f"trigger:{ev['trigger_1h']}")
        if "choch" in conf:
            parts.append("choch")
        if "rsi" in conf:
            parts.append(f"rsi:{side}")
        if "macd" in conf:
            parts.append("macd")
        parts += labels[:1]
        parts += [n for n in notes if n in ("pullback_volume", "breakout_volume")]
        parts += [n for n in notes if n.startswith("pattern:")]
    else:
        flags = [f for f in ev.get("flags", []) if f in REASON_FA]
        lead = [f for f in flags if f == "not_issued"]
        parts = lead + [level] + [f for f in flags if f not in lead] + labels[:1]
    out: list[str] = []
    for key in parts:
        phrase = REASON_FA.get(key, key)
        if phrase in out:
            continue
        if out and len(" + ".join(out + [phrase])) > REASON_MAX:
            continue
        out.append(phrase)
    return " + ".join(out)[:REASON_MAX]


def coin_line(ev: dict, out_of_cap: bool = False) -> str:
    """ev: an Evaluation as a dict (Evaluation.to_dict()), with a trade plan."""
    p = ev["plan"]
    icon = SIGNAL_ICON if ev["grade"] in ("A", "B") else WATCH_ICON
    return " | ".join([
        f"{icon} {pair(ev['base'])}", side_word(ev["side"]), f"امتیاز {shown_score(ev['score'])}",
        f"ورود {fmt_price(p['entry'])}", f"SL {fmt_price(p['sl'])}", f"TP1 {fmt_price(p['tp1'])}",
        f"TP2 {fmt_price(p['tp2'])}", reason(ev, out_of_cap),
    ])


def with_explanation(text: str, cfg: dict) -> str:
    return f"{text}\n\n{score_explanation(cfg)}"


# ------------------------------------------------------------------- messages
def signal_message(ev, cfg: dict, out_of_cap: bool = False) -> str:
    """New signal: the coin line, one risk line, then the score explanation."""
    p = ev.plan
    risk = (f"ریسک {fmt_num(p.risk_pct, 2)}٪ | حجم {round(p.size_pct)}٪ موجودی | "
            f"لوریج {p.leverage}x")
    return with_explanation(f"{coin_line(ev.to_dict(), out_of_cap)}\n{risk}", cfg)


def watch_message(ev, cfg: dict) -> str | None:
    """Near-setup alert. None when there is no trade plan to show."""
    if ev.plan is None:
        return None
    return with_explanation(coin_line(ev.to_dict()), cfg)


def event_message(sig: dict, e, cfg: dict) -> str:
    """One line per update of an existing signal (no score)."""
    head = pair(sig["symbol"])
    side = 1 if sig["side"] == "long" else -1
    k = e.kind
    if k == "filled":
        return f"✅ {head} | ورود فعال شد | {fmt_price(e.price)}"
    if k == "tp1":
        return f"🎯 {head} | TP1 | {e.r:+.1f}R | SL به ورود"
    if k == "tp2":
        return f"🎯 {head} | TP2 | {e.r:+.1f}R | تریل روی MA25 4H"
    if k == "tp3":
        why = "MA25 4H" if e.reason == "ma25" else "شکست ساختار"
        return f"🏁 {head} | خروج نهایی ({why}) | {e.r:+.1f}R"
    if k == "sl":
        return f"🛑 {head} | SL | {e.r:+.1f}R"
    if k == "breakeven":
        return f"⚪️ {head} | SL در ورود | {e.r:+.1f}R"
    if k == "expired":
        hours = cfg["lifecycle"]["expiry_bars_4h"] * 4
        return f"⌛️ {head} | منقضی شد | ورود در {hours} ساعت فعال نشد"
    if k == "cancelled":
        why = {"tp1_before_entry": "TP1 قبل از ورود",
               "closed_beyond_sl": f"کلوز 4H {'زیر' if side == 1 else 'بالای'} SL"}.get(e.reason, e.reason)
        return f"❌ {head} | لغو شد | {why}"
    return f"{head} | {k}"


def pause_message(until_ms: int) -> str:
    until = datetime.fromtimestamp(until_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"⏸ ترمز ضرر: ۳ حد ضرر پیاپی؛ تا کلوز کندل روزانه ({until}) سیگنال جدید صادر نمی‌شود"


def daily_message(funnel: dict, open_signals: list[dict], evaluations: list[dict], now_ms: int,
                  cfg: dict) -> str:
    """Two header lines, then one line per coin with a signal or Watch plan (L6 score,
    highest first), then the shortlisted coins without a setup (names only). 🟢 is kept for
    signals that were actually sent and are open; an A/B setup the signal book held back
    (cap, correlation, cooldown, loss brake) is shown 🟡 with "صادر نشد"."""
    date = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    regime = funnel["regime"]["name"]
    lines = [f"📊 گزارش روزانه {date} | رژیم: {REGIME_FA.get(regime, regime)}",
             f"سیگنال باز: {len(open_signals)} از {cfg['lifecycle']['max_active']}"]
    rows: dict[str, dict] = {}
    for s in open_signals:                       # issued signals: the evaluation they came from
        ev = s["payload"].get("evaluation") or {}
        plan = ev.get("plan") or s["payload"].get("plan")
        if plan:
            rows[s["symbol"]] = {**ev, "base": s["symbol"], "plan": plan, "grade": s["grade"],
                                 "score": s["score"], "side": 1 if s["side"] == "long" else -1}
    for ev in evaluations:                       # latest hourly L6 results
        if ev.get("plan") and ev.get("grade") in ("A", "B", "Watch") and ev["base"] not in rows:
            if ev["grade"] in ("A", "B"):        # passed L6 but no open signal: it wasn't sent
                ev = {**ev, "grade": "Watch", "flags": ["not_issued", *ev.get("flags", [])]}
            rows[ev["base"]] = ev
    ordered = sorted(rows.values(), key=lambda e: (-e["score"], e["base"]))
    lines += [coin_line(ev) for ev in ordered]
    if not ordered:
        lines.append("ستاپ فعالی نیست.")
    rest = [c["base"] for c in funnel.get("shortlist", []) if c["base"] not in rows]
    if rest:
        lines.append("در گلچین بدون ستاپ: " + ", ".join(rest))
    text = "\n".join(lines)
    return with_explanation(text, cfg) if ordered else text


def check(ok: bool) -> str:
    return "✅" if ok else "❌"


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


def performance_stats(signals: list[dict], since_ms: int | None = None) -> dict:
    """Results of signals that reached a fill, from their stored lifecycle. R is the
    signal's own plan (before fees, slippage and funding). With `since_ms`, only trades
    closed at or after it. Expired and cancelled signals never filled and count apart."""
    closed = ("sl", "breakeven", "tp3")
    out = {"n": 0, "wins": 0, "r": 0.0, "by_grade": {}, "unfilled": 0, "open": 0}
    for s in signals:
        lc = s["payload"]["lifecycle"]
        status = s["status"]
        if status in ("expired", "cancelled"):
            if since_ms is None or (lc.get("closed_at") or 0) >= since_ms:
                out["unfilled"] += 1
        elif status in closed:
            if since_ms is not None and (lc.get("closed_at") or 0) < since_ms:
                continue
            r = lc["realized_r"]
            g = out["by_grade"].setdefault(s["grade"], {"n": 0, "wins": 0, "r": 0.0})
            for d in (out, g):
                d["n"] += 1
                d["wins"] += r > 0
                d["r"] += r
        elif since_ms is None:
            out["open"] += 1
    return out


def _perf_line(label: str, d: dict) -> str:
    if not d["n"]:
        return f"{label}: معامله‌ی بسته‌شده‌ای نبود"
    return (f"{label}: {d['n']} معامله، نرخ برد {d['wins'] / d['n'] * 100:.0f}٪، "
            f"مجموع {d['r']:+.2f}R، میانگین {d['r'] / d['n']:+.2f}R")


def performance_message(signals: list[dict], now_ms: int) -> str:
    date = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    week, total = performance_stats(signals, now_ms - 7 * 86_400_000), performance_stats(signals)
    lines = [f"📊 عملکرد واقعی سیگنال‌ها {date}", "", _perf_line("۷ روز اخیر", week),
             _perf_line("از ابتدا", total)]
    for g in sorted(total["by_grade"]):
        lines.append(_perf_line(f"  رده‌ی {g}", total["by_grade"][g]))
    lines.append(f"بدون ورود (منقضی/لغو): {total['unfilled']} | هنوز باز: {total['open']}")
    lines.append("R طبق پلن خود سیگنال است و کارمزد، اسلیپیج و فاندینگ را شامل نمی‌شود "
                 "(در بک‌تست حدود ۰.۱۷R از هر معامله کم می‌کرد).")
    if total["n"] < 30:
        lines.append(f"⚠️ فقط {total['n']} معامله؛ برای قضاوت کم است.")
    return "\n".join(lines)
