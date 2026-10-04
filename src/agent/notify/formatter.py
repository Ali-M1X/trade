"""Persian Telegram messages.

Every coin is shown as a CARD: one field per line, the same fields in the same order in every
message (new signal, Watch, daily report). The only score ever shown is the final L6
technical score:

    <icon> <COIN>USDT | <LONG|SHORT> | امتیاز <score> (رده A|رده B|Watch)
    ورود: <entry>
    حد ضرر (SL): <sl>
    هدف ۱ (TP1): <tp1>
    هدف ۲ (TP2): <tp2>
    چرا: <reason>

A new signal adds the risk line and the regime line (which sides the regime allows), a Watch
adds the regime line. Every message with a card ends with LEGEND (only the icons it shows) and
the ℹ️ score explanation. Signal updates, pause, HOLD and performance messages are one line
per item. All text comes from templates and check results.
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
BIAS_FA = {"long": "فقط لانگ", "short": "فقط شورت", "both": "هر دو سمت", "none": "بدون معامله"}

SIGNAL_ICON, WATCH_ICON = "🟢", "🟡"
# The colour legend: one line under every message with a card, listing only the icons shown.
LEGEND = {
    SIGNAL_ICON: "سیگنال: همه شرط‌ها برقرار است و می‌توانی طبق پلن وارد شوی",
    WATCH_ICON: "Watch: نزدیک ستاپ است، هنوز وارد نشو",
}
REASON_MAX = 100                     # characters; parts that don't fit are dropped, never cut
REASON_PARTS = 4                     # at most this many phrases
DAILY_SPLIT = 3800                   # characters; longer daily reports are split between cards
TF_FA = {"4h": "4H", "1d": "D", "1w": "W", "1h": "1H", "round": ""}

# The one dictionary of reason phrases: plain Persian that says WHY. Keys are the
# evaluation's flags, labels, 1H confirmations, notes and the kind of level the entry leans on.
REASON_FA = {
    # level the entry leans on ({lvl} = حمایت / مقاومت, {tf} = its timeframe)
    "level:cluster": "برگشت قیمت به {lvl} {tf}",
    "level:flip": "برگشت به سطح شکسته‌شده {tf}",
    "level:prev_high": "برگشت به سقف قبلی {tf}",
    "level:prev_low": "برگشت به کف قبلی {tf}",
    "level:round": "واکنش به عدد رند",
    # 1H confirmations
    "trigger:engulfing": "کندل برگشتی قوی 1H",
    "trigger:pin_bar": "پین‌بار 1H",
    "trigger:strong_close": "کلوز قوی 1H",
    "choch": "تغییر جهت ساختار 1H",
    "rsi:1": "RSI بالای 50",
    "rsi:-1": "RSI زیر 50",
    "macd": "چرخش مومنتوم MACD",
    "rvol": "حجم بالا در 1H",
    # volume on 4H
    "pullback_volume": "اصلاح با حجم کم",
    "breakout_volume": "شکست با حجم بالا",
    # scanner labels
    "EARLY_TREND": "شکست رنج و شروع روند",
    "RS_LEADER": "قوی‌تر از BTC",
    "PULLBACK": "اصلاح سالم در روند",
    "SQUEEZE": "نوسان فشرده قبل از حرکت",
    "EXHAUSTION": "حرکت قبلی خسته شده",
    "OI_BUILDUP": "رشد قراردادهای باز",
    "FUNDING_EXTREME": "فاندینگ افراطی (بازار یک‌طرفه)",
    "VOLUME_ANOMALY": "حجم غیرعادی",
    "HOT_SECTOR": "بخش داغ بازار",
    "BREAKOUT_WATCH": "شکست تازه، منتظر برگشت",
    # chart patterns (notes "pattern:<name>")
    "pattern:double_bottom": "الگوی کف دوقلو", "pattern:double_top": "الگوی سقف دوقلو",
    "pattern:inverse_head_shoulders": "الگوی سر و شانه معکوس",
    "pattern:head_shoulders": "الگوی سر و شانه",
    "pattern:triangle": "الگوی مثلث", "pattern:rising_wedge": "الگوی کنج صعودی",
    "pattern:falling_wedge": "الگوی کنج نزولی", "pattern:bull_flag": "الگوی پرچم صعودی",
    "pattern:bear_flag": "الگوی پرچم نزولی",
    # why a setup is only Watch / special cases
    "not_confirmed": "هنوز تأیید 1H نیامده",
    "lower_cycle_correcting": "اصلاح 4H هنوز ادامه دارد",
    "chase": "قیمت دور شده، منتظر پولبک",
    "btc_weak": "BTC ضعیف است",
    "neutral_regime_needs_A": "در رژیم خنثی فقط رده A",
    "grade_b_off": "فعلاً فقط رده A سیگنال می‌شود",
    "funding_crowded": "فاندینگ شلوغ در همین سمت",
    "out_of_cap": "خارج از سقف سیگنال‌های باز",
    "not_issued": "سیگنال صادر نشد",   # A/B setup the signal book held back (cap, correlation, ...)
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
        + (f"{fa(b)} تا {fa(a - 1)} رده B که فعلاً خاموش است و فقط Watch می‌آید، "
           if g.get("min_signal", "B") == "A" else
           f"{fa(b)} تا {fa(a - 1)} رده B با {_risk_words(share['B'])}، ") +
        f"{fa(w)} تا {fa(b - 1)} فقط Watch (هشدار نزدیک ستاپ، بدون ورود) "
        f"و زیر {fa(w)} چیزی ارسال نمی‌شود."
    )


# ------------------------------------------------------------------ coin card
def reason_keys(ev: dict, out_of_cap: bool = False) -> list[str]:
    """The REASON_FA keys for an evaluation, in display order and at most REASON_PARTS of
    them: the level the entry leans on, the 1H trigger/confirmation, the scanner label, then
    4H volume or a pattern (for Watch: what is still missing instead of the 1H part). Further
    1H confirmations only fill free places."""
    side = ev["side"]
    labels = [l for l in ev.get("labels", []) if l in REASON_FA][:1]
    notes = ev.get("notes", [])
    extra = [n for n in notes if n in ("pullback_volume", "breakout_volume")] + \
        [n for n in notes if n.startswith("pattern:")]
    level = f"level:{ev['plan']['support_kind']}"
    if ev["grade"] in ("A", "B"):
        conf = set(ev.get("confirmations", []))
        h1 = ([f"trigger:{ev['trigger_1h']}"] if ev.get("trigger_1h") else []) + \
            [k for k in ("choch", "rsi", "macd") if k in conf]
        h1 = [f"rsi:{side}" if k == "rsi" else k for k in h1]
        lead = ["out_of_cap"] if out_of_cap else []
        groups = [lead, [level], h1[:1], labels, extra[:1], h1[1:], extra[1:]]
        order = [lead, [level], h1, labels, extra]           # display order
    else:
        flags = [f for f in ev.get("flags", []) if f in REASON_FA]
        lead = [f for f in flags if f == "not_issued"]
        rest = [f for f in flags if f not in lead]
        groups = [lead, [level], rest[:1], labels, rest[1:]]
        order = [lead, [level], rest, labels]
    picked: list[str] = []
    for k in (k for g in groups for k in g):
        if k in picked or len(picked) >= REASON_PARTS:
            continue
        if picked and len(_join(picked + [k], side, ev["plan"])) > REASON_MAX:
            continue
        picked.append(k)
    return [k for g in order for k in g if k in picked]


def _phrase(key: str, side: int, plan: dict) -> str:
    return REASON_FA[key].format(lvl="حمایت" if side == 1 else "مقاومت",
                                 tf=TF_FA.get(plan["support_tf"], plan["support_tf"])).strip()


def _join(keys: list[str], side: int, plan: dict) -> str:
    out: list[str] = []
    for k in keys:
        p = _phrase(k, side, plan)
        if p not in out:
            out.append(p)
    return " + ".join(out)


def reason(ev: dict, out_of_cap: bool = False) -> str:
    """WHY in a few words (<= REASON_MAX characters, whole phrases only)."""
    return _join(reason_keys(ev, out_of_cap), ev["side"], ev["plan"])


def grade_word(grade: str) -> str:
    return f"رده {grade}" if grade in ("A", "B") else "Watch"


def coin_card(ev: dict, out_of_cap: bool = False) -> str:
    """ev: an Evaluation as a dict (Evaluation.to_dict()), with a trade plan."""
    p = ev["plan"]
    icon = SIGNAL_ICON if ev["grade"] in ("A", "B") else WATCH_ICON
    if p["order"] == "confirm_4h":
        # entry after a 4H confirmation: SL/TP shown are from the level price, final at fill
        entry = f"ورود پس از تأیید 4H حوالی {fmt_price(p['entry'])}"
        tp2 = f"{fmt_price(p['tp2'])} (نهایی پس از فعال شدن)"
    else:
        entry, tp2 = f"ورود: {fmt_price(p['entry'])}", fmt_price(p["tp2"])
    return "\n".join([
        f"{icon} {pair(ev['base'])} | {side_word(ev['side'])} | "
        f"امتیاز {shown_score(ev['score'])} ({grade_word(ev['grade'])})",
        entry,
        f"حد ضرر (SL): {fmt_price(p['sl'])}",
        f"هدف ۱ (TP1): {fmt_price(p['tp1'])}",
        f"هدف ۲ (TP2): {tp2}",
        f"چرا: {reason(ev, out_of_cap)}",
    ])


def regime_line(regime: dict) -> str:
    """Which sides the regime that produced the setup allows (its own bias, risk and
    min_grade; nothing is recomputed)."""
    name, bias = regime["name"], regime.get("bias", "both")
    words = BIAS_FA.get(bias, bias)
    extra = []
    if regime.get("min_grade"):
        extra.append(f"فقط رده {regime['min_grade']}")
    if regime.get("risk") is not None and regime["risk"] < 1:
        extra.append(_risk_words(regime["risk"]))
    if extra:
        words += "، " + " و ".join(extra)
    return f"رژیم بازار: {REGIME_FA.get(name, name)} ({words})"


def legend(icons) -> str:
    """One line explaining the card icons, only those in `icons`, in LEGEND order."""
    return " | ".join(f"{i} {t}" for i, t in LEGEND.items() if i in set(icons))


def with_explanation(text: str, cfg: dict, icons=()) -> str:
    head = f"{legend(icons)}\n" if legend(icons) else ""
    return f"{text}\n\n{head}{score_explanation(cfg)}"


def _icon(ev: dict) -> str:
    return SIGNAL_ICON if ev["grade"] in ("A", "B") else WATCH_ICON


# ------------------------------------------------------------------- messages
def signal_message(ev, regime: dict, cfg: dict, out_of_cap: bool = False) -> str:
    """New signal: the card, the risk line, the regime line, the legend and the score
    explanation. `regime` is the funnel regime the signal came from (Regime.to_dict())."""
    p, d = ev.plan, ev.to_dict()
    risk = (f"ریسک {fmt_num(p.risk_pct, 2)}٪ | حجم {round(p.size_pct)}٪ موجودی | "
            f"لوریج {p.leverage}x")
    return with_explanation(f"{coin_card(d, out_of_cap)}\n{risk}\n{regime_line(regime)}", cfg,
                            [_icon(d)])


def watch_message(ev, regime: dict, cfg: dict) -> str | None:
    """Near-setup alert: the card and the regime line. None when there is no trade plan."""
    if ev.plan is None:
        return None
    d = ev.to_dict()
    return with_explanation(f"{coin_card(d)}\n{regime_line(regime)}", cfg, [_icon(d)])


def event_message(sig: dict, e, cfg: dict) -> str:
    """One line per update of an existing signal (no score)."""
    head = pair(sig["symbol"])
    side = 1 if sig["side"] == "long" else -1
    k = e.kind
    if k == "filled":
        lc = (sig.get("payload") or {}).get("lifecycle") or {}
        if "confirm" in lc:                  # confirm_4h: SL/TP were rebuilt from this fill
            return (f"✅ {head} | ورود فعال شد (تأیید 4H) | {fmt_price(e.price)} | "
                    f"SL {fmt_price(lc['sl'])} | TP1 {fmt_price(lc['tp1'])}")
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
               "closed_beyond_sl": f"کلوز 4H {'زیر' if side == 1 else 'بالای'} SL",
               "fill_rr": "پس از تأیید 4H ریسک به ریوارد کافی نیست",
               "fill_sl_too_wide": "پس از تأیید 4H حد ضرر بیش از حد دور است",
               "fill_sl_too_tight": "پس از تأیید 4H حد ضرر بیش از حد نزدیک است",
               "fill_stop_wrong_side": "پس از تأیید 4H حد ضرر نامعتبر است"}.get(e.reason, e.reason)
        return f"❌ {head} | لغو شد | {why}"
    return f"{head} | {k}"


def pause_message(until_ms: int) -> str:
    until = datetime.fromtimestamp(until_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"⏸ ترمز ضرر: ۳ حد ضرر پیاپی؛ تا کلوز کندل روزانه ({until}) سیگنال جدید صادر نمی‌شود"


def daily_message(funnel: dict, open_signals: list[dict], evaluations: list[dict], now_ms: int,
                  cfg: dict) -> list[str]:
    """The daily report as one or more Telegram messages. Two header lines, then one card per
    coin with a signal or Watch plan (L6 score, highest first) separated by a blank line, then
    the shortlisted coins without a setup (names only). 🟢 is kept for signals that were
    actually sent and are open; an A/B setup the signal book held back (cap, correlation,
    cooldown, loss brake) is shown 🟡 with "سیگنال صادر نشد". A report longer than
    DAILY_SPLIT characters is split between cards (never inside one); the legend and the
    score explanation are only in the last message."""
    date = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    regime = funnel["regime"]["name"]
    header = (f"📊 گزارش روزانه {date} | رژیم: {REGIME_FA.get(regime, regime)}\n"
              f"سیگنال باز: {len(open_signals)} از {cfg['lifecycle']['max_active']}")
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
    blocks = [header] + [coin_card(ev) for ev in ordered]
    if not ordered:
        blocks[0] += "\nستاپ فعالی نیست."
    rest = [c["base"] for c in funnel.get("shortlist", []) if c["base"] not in rows]
    if rest:
        blocks.append("در گلچین بدون ستاپ: " + ", ".join(rest))
    if ordered:
        blocks.append(with_explanation("", cfg, [_icon(ev) for ev in ordered]).lstrip("\n"))
    parts: list[list[str]] = [[]]
    for b in blocks:
        if parts[-1] and len("\n\n".join(parts[-1] + [b])) > DAILY_SPLIT:
            parts.append([])
        parts[-1].append(b)
    return ["\n\n".join(p) for p in parts]


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
