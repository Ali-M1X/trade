"""Persian Telegram messages.

New signals and updates of open signals are instructions (2026-10-30, Ali): the first line
says what to do now, then one line per figure (order, stop, targets, risk, size, leverage,
expiry, reason, score). Updates say what to do with the open position. No score explanation.

The daily report keeps one line per coin with the same fields in the same order:

    <icon> <COIN>USDT | <LONG|SHORT> | امتیاز <score> | ورود <entry> | SL <sl> | TP1 <tp1> | TP2 <tp2> | <reason>

🟢 = signal (all gates passed, grade A/B), 🟡 = Watch (near setup). All text comes from
templates and check results.
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
    "bull": "صعودی",
    "range": "رنج",
    "bear": "نزولی",
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
    "grade_b_off": "فعلاً فقط رده A",
    "funding_crowded": "فاندینگ شلوغ",
    "out_of_cap": "خارج از سقف",
    "not_issued": "صادر نشد",
    # classic filters (technical.classic, experiment)
    "no_vcp": "بدون VCP",
    "adx_low": "ADX پایین",
    "impulse_against_4h": "ایمپالس خلاف 4H", "impulse_against_1w": "ایمپالس خلاف هفتگی",
    "weekly_tide_against": "روند هفتگی مخالف", "stage_against": "مرحله واینستین مخالف",
    "range_no_dryup": "حجم رنج خشک نشده", "dmi_against": "ADX/DMI مخالف", "choppy": "حرکت پرنوسان",          # A/B setup the signal book held back (cap, correlation, ...)
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
    if p["order"] == "confirm_4h":
        # entry after a 4H confirmation: SL/TP shown are from the level price, final at fill
        entry, tp2 = (f"ورود پس از تأیید 4H حوالی {fmt_price(p['entry'])}",
                      f"TP2 {fmt_price(p['tp2'])} (نهایی پس از فعال شدن)")
    else:
        entry, tp2 = f"ورود {fmt_price(p['entry'])}", f"TP2 {fmt_price(p['tp2'])}"
    return " | ".join([
        f"{icon} {pair(ev['base'])}", side_word(ev["side"]), f"امتیاز {shown_score(ev['score'])}",
        entry, f"SL {fmt_price(p['sl'])}", f"TP1 {fmt_price(p['tp1'])}", tp2, reason(ev, out_of_cap),
    ])


# ------------------------------------------------------------------- messages
BUY_SELL = {1: ("خرید", "LONG"), -1: ("فروش", "SHORT")}


def _pct(x: float) -> str:
    return f"{fmt_num(x, 2 if x < 1 else 1)}٪"


def _order_lines(p, cfg: dict) -> list[str]:
    word = BUY_SELL[p.side][0]
    hours = cfg["lifecycle"]["expiry_bars_4h"] * 4
    if p.order == "market":
        # the plan's zone edge: past it the entry is already late
        edge, beyond = (p.entry_high, "بالای") if p.side == 1 else (p.entry_low, "زیر")
        return [f"سفارش: همین الان مارکت {word} (حدود {fmt_price(p.entry)})",
                f"شرط: اگر قیمت {beyond} {fmt_price(edge)} است، وارد نشو"]
    if p.order == "confirm_4h":
        return [f"سفارش: هنوز وارد نشو؛ وقتی کندل 4H حوالی {fmt_price(p.entry)} تأیید داد پیام ورود می‌آید",
                f"اعتبار: {fa(hours)} ساعت"]
    return [f"سفارش: لیمیت {word} در {fmt_price(p.entry)}",
            f"اعتبار: اگر تا {fa(hours)} ساعت پر نشد، پیام لغو می‌آید"]


def signal_message(ev, cfg: dict, out_of_cap: bool = False) -> str:
    """New signal as an instruction: what to do now, then one figure per line."""
    p, t = ev.plan, cfg["trade"]
    word, side = BUY_SELL[ev.side]
    approx = " (تقریبی؛ نهایی در پیام ورود)" if p.order == "confirm_4h" else ""
    tp1_pct, tp2_pct = t["tp1_close_pct"], t["tp2_close_pct"]
    rest = 100 - tp1_pct - tp2_pct
    lines = [f"{SIGNAL_ICON} اقدام: {word} {pair(ev.base)} ({side})",
             *_order_lines(p, cfg),
             f"حد ضرر: {fmt_price(p.sl)} ({_pct(p.sl_pct)} فاصله){approx}",
             f"TP1: {fmt_price(p.tp1)} ← {fa(tp1_pct)}٪ را ببند و حد ضرر را به ورود ببر{approx}",
             f"TP2: {fmt_price(p.tp2)} ← {fa(tp2_pct)}٪ دیگر را ببند"]
    if rest > 0:
        lines.append(f"باقی {fa(rest)}٪: نگه دار تا پیام خروج")
    lines += [f"ریسک: {_pct(p.risk_pct)} موجودی",
              f"حجم پوزیشن: {fmt_num(p.size_pct, 0)}٪ موجودی (مارجین {_pct(p.margin_pct)})",
              f"لوریج: {p.leverage}x ایزوله",
              f"دلیل: {reason(ev.to_dict(), out_of_cap)}",
              f"امتیاز: {shown_score(ev.score)} (رده {ev.grade})"]
    return "\n".join(lines)


def watch_message(ev, cfg: dict) -> str | None:
    """Near-setup alert (off by default: watch_alerts.enabled). None without a trade plan."""
    if ev.plan is None:
        return None
    p = ev.plan
    word, side = BUY_SELL[ev.side]
    return "\n".join([
        f"{WATCH_ICON} زیر نظر: {pair(ev.base)} ({side}) | فعلاً کاری نکن",
        f"اگر سیگنال شد: {word} حوالی {fmt_price(p.entry)}",
        f"حد ضرر: {fmt_price(p.sl)}",
        f"TP1: {fmt_price(p.tp1)}",
        f"منتظر: {reason(ev.to_dict())}",
        f"امتیاز: {shown_score(ev.score)}",
    ])


CANCEL_FA = {"tp1_before_entry": "قیمت قبل از ورود به TP1 رسید",
             "fill_rr": "پس از تأیید 4H ریسک به ریوارد کافی نیست",
             "fill_sl_too_wide": "پس از تأیید 4H حد ضرر بیش از حد دور است",
             "fill_sl_too_tight": "پس از تأیید 4H حد ضرر بیش از حد نزدیک است",
             "fill_stop_wrong_side": "پس از تأیید 4H حد ضرر نامعتبر است"}


def event_message(sig: dict, e, cfg: dict) -> str:
    """An update of an open signal: what happened, then what to do with the position."""
    head = pair(sig["symbol"])
    side = 1 if sig["side"] == "long" else -1
    lc = (sig.get("payload") or {}).get("lifecycle") or {}
    t = cfg["trade"]
    k = e.kind
    if k == "filled":
        if "confirm" in lc:                  # confirm_4h: SL/TP were rebuilt from this fill
            return "\n".join([
                f"✅ {head} | تأیید 4H آمد",
                f"اقدام: همین الان مارکت {BUY_SELL[side][0]} (حدود {fmt_price(e.price)})",
                f"حد ضرر: {fmt_price(lc['sl'])}",
                f"TP1: {fmt_price(lc['tp1'])}",
                f"TP2: {fmt_price(lc['tp2'])}" if lc.get("tp2") else ""]).rstrip("\n")
        lines = [f"✅ {head} | سفارش پر شد در {fmt_price(e.price)}"]
        if lc.get("sl") and lc.get("tp1"):
            lines.append(f"اقدام: مطمئن شو حد ضرر {fmt_price(lc['sl'])} و TP1 {fmt_price(lc['tp1'])} ثبت است")
        return "\n".join(lines)
    if k == "tp1":
        entry = f" ({fmt_price(lc['entry'])})" if lc.get("entry") else ""
        return (f"🎯 {head} | TP1 خورد | {e.r:+.1f}R\n"
                f"اقدام: {fa(t['tp1_close_pct'])}٪ را ببند و حد ضرر را به ورود{entry} ببر")
    if k == "tp2":
        return (f"🎯 {head} | TP2 خورد | {e.r:+.1f}R\n"
                f"اقدام: {fa(t['tp2_close_pct'])}٪ دیگر را ببند؛ باقی را تا پیام خروج نگه دار")
    if k == "tp3":
        why = (f"کلوز 4H {'زیر' if side == 1 else 'بالای'} MA25" if e.reason == "ma25"
               else "شکست ساختار 4H")
        return f"🏁 {head} | خروج نهایی ({why}) | {e.r:+.1f}R\nاقدام: باقی پوزیشن را همین الان ببند"
    if k == "sl":
        return f"🛑 {head} | حد ضرر خورد | {e.r:+.1f}R\nاقدام: اگر پوزیشن هنوز باز است، ببندش"
    if k == "breakeven":
        return (f"⚪️ {head} | قیمت به ورود برگشت | {e.r:+.1f}R\n"
                f"اقدام: اگر باقی پوزیشن هنوز باز است، ببندش")
    if k == "expired":
        hours = cfg["lifecycle"]["expiry_bars_4h"] * 4
        return f"⌛️ {head} | در {fa(hours)} ساعت پر نشد\nاقدام: سفارش را لغو کن"
    if k == "cancelled":
        why = CANCEL_FA.get(e.reason) or (
            f"کلوز 4H {'زیر' if side == 1 else 'بالای'} حد ضرر" if e.reason == "closed_beyond_sl"
            else e.reason)
        return f"❌ {head} | لغو شد: {why}\nاقدام: سفارش را لغو کن و وارد نشو"
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
    return "\n".join(lines)


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
