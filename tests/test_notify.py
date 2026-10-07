import io
import math
import re
from pathlib import Path

import pytest

from agent.config import load_secrets
from agent.notify import formatter as fmt
from agent.notify.telegram import Notifier, split
from agent.signals.lifecycle import Event
from message_examples import (ALT_SEASON, NEUTRAL, NOW, RISK_OFF, confirm_eval, funnel,
                              open_signal, short_eval, signal_eval, watch_eval)

STRATEGY = Path(__file__).resolve().parents[1] / "docs" / "STRATEGY.md"
TECHNICAL = Path(__file__).resolve().parents[1] / "src" / "agent" / "layers" / "technical.py"
CARD = re.compile(r"^(🟢|🟡) ([A-Z0-9]+USDT) \| (LONG|SHORT) \| امتیاز (\d+) \((رده A|رده B|Watch)\)\n"
                  r"ورود: ([\d.]+)\nحد ضرر \(SL\): ([\d.]+)\nهدف ۱ \(TP1\): ([\d.]+)\n"
                  r"هدف ۲ \(TP2\): ([\d.]+)\nچرا: ([^\n]+)$")


def strategy_signal_example() -> str:
    text = STRATEGY.read_text(encoding="utf-8")
    section = text[text.index("## لایه ۷"):]
    return re.findall(r"```\n(.*?)\n```", section, re.S)[1]


def parse(card: str) -> dict:
    m = CARD.match(card)
    assert m, f"not a coin card: {card!r}"
    icon, coin, side, score, grade, entry, sl, tp1, tp2, why = m.groups()
    return {"icon": icon, "coin": coin, "side": side, "score": int(score), "grade": grade,
            "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "reason": why}


def card_of(msg: str) -> str:
    return "\n".join(msg.split("\n")[:6])


# ---------------------------------------------------------------- coin card
def test_signal_message_matches_strategy_md(cfg):
    assert fmt.signal_message(signal_eval(), ALT_SEASON, cfg) == strategy_signal_example()


def test_signal_message_layout(cfg):
    msg = fmt.signal_message(signal_eval(), ALT_SEASON, cfg)
    assert parse(card_of(msg)) == {
        "icon": "🟢", "coin": "SOLUSDT", "side": "LONG", "score": 82, "grade": "رده A",
        "entry": "152.30", "sl": "148.90", "tp1": "158.10", "tp2": "163.20",
        "reason": "برگشت قیمت به حمایت 4H + کندل برگشتی قوی 1H"}
    lines = msg.split("\n")
    assert lines[6] == "ریسک 1٪ | حجم 45٪ موجودی | لوریج 10x"
    assert lines[7] == "رژیم بازار: آلت‌سیزن (فقط لانگ)"
    assert lines[8] == "" and lines[9] == f"🟢 {fmt.LEGEND['🟢']}"
    assert lines[10] == fmt.score_explanation(cfg) and len(lines) == 11


def test_short_signal_card(cfg):
    msg = fmt.signal_message(short_eval(), RISK_OFF, cfg)
    row = parse(card_of(msg))
    assert (row["icon"], row["coin"], row["side"], row["grade"]) == ("🟢", "APTUSDT", "SHORT", "رده B")
    assert (row["entry"], row["sl"], row["tp1"], row["tp2"]) == ("2.415", "2.488", "2.268", "2.195")
    assert float(row["sl"]) > float(row["entry"]) > float(row["tp1"]) > float(row["tp2"])
    assert row["reason"] == ("برگشت به سقف قبلی D + تغییر جهت ساختار 1H + حرکت قبلی خسته شده + "
                             "الگوی سقف دوقلو")
    assert msg.split("\n")[7] == "رژیم بازار: Risk-off (فقط شورت)"
    # short-side words: resistance, RSI below 50, a 1H pin bar
    ev = short_eval()
    ev.plan.support_kind, ev.plan.support_tf, ev.trigger_1h = "cluster", "4h", "pin_bar"
    ev.confirmations, ev.labels, ev.notes = ["rsi", "rvol"], [], []
    why = parse(fmt.coin_card(ev.to_dict()))["reason"]
    assert why == "برگشت قیمت به مقاومت 4H + پین‌بار 1H + RSI زیر 50"
    assert "حمایت" not in why and "بالای" not in why


def test_neutral_regime_signal_card(cfg):
    ev = signal_eval()
    ev.plan.risk_pct, ev.plan.size_pct = 0.5, 22.4
    msg = fmt.signal_message(ev, NEUTRAL, cfg)
    lines = msg.split("\n")
    assert parse(card_of(msg))["grade"] == "رده A"
    assert lines[6] == "ریسک 0.5٪ | حجم 22٪ موجودی | لوریج 10x"
    assert lines[7] == "رژیم بازار: خنثی/رنج (هر دو سمت، فقط رده A و نصف ریسک)"


def test_every_card_has_the_same_fields_in_order(cfg):
    for ev in (signal_eval(), watch_eval(), short_eval()):
        card = fmt.coin_card(ev.to_dict())
        assert card.count("\n") == 5
        row = parse(card)
        assert row["score"] == math.floor(ev.score)
        assert row["entry"] == fmt.fmt_price(ev.plan.entry)
        assert row["sl"] == fmt.fmt_price(ev.plan.sl)
        assert row["grade"] == (f"رده {ev.grade}" if ev.grade in "AB" else "Watch")


def test_icons_by_grade(cfg):
    assert fmt.coin_card(signal_eval().to_dict()).startswith("🟢 ")
    assert fmt.coin_card(short_eval().to_dict()).startswith("🟢 APTUSDT | SHORT")
    assert fmt.coin_card(watch_eval().to_dict()).startswith("🟡 ARBUSDT | LONG | امتیاز 66 (Watch)")


def test_score_is_rounded_down_so_it_never_crosses_a_grade(cfg):
    ev = signal_eval()
    ev.score, ev.grade = 74.9, "B"
    assert parse(fmt.coin_card(ev.to_dict()))["score"] == 74
    ev.score = 75.0
    assert fmt.shown_score(ev.score) == 75


def test_reason_is_plain_persian_whole_phrases_and_short(cfg):
    ev = short_eval()
    ev.labels = ["EARLY_TREND", "RS_LEADER", "HOT_SECTOR", "BREAKOUT_WATCH"]
    ev.notes = ["pullback_volume", "pattern:double_top", "pattern:bear_flag"]
    ev.confirmations = ["choch", "rsi", "macd", "trigger_candle", "rvol"]
    ev.trigger_1h = "pin_bar"
    phrases = {fmt._phrase(k, ev.side, ev.plan.to_dict()) for k in fmt.REASON_FA}
    for out_of_cap in (False, True):
        why = parse(fmt.coin_card(ev.to_dict(), out_of_cap))["reason"]
        assert len(why) <= fmt.REASON_MAX
        parts = why.split(" + ")
        assert len(parts) <= fmt.REASON_PARTS and set(parts) <= phrases   # never cut mid-phrase
        assert not re.search(r"[A-Z]{2,}_[A-Z]+|pattern:|pullback_volume|[a-z]{4,}", why)
    full = parse(fmt.coin_card(ev.to_dict()))["reason"].split(" + ")
    # order of importance: level, 1H trigger, scanner label, volume/pattern
    assert full == ["برگشت به سقف قبلی D", "پین‌بار 1H", "شکست رنج و شروع روند", "اصلاح با حجم کم"]
    assert parse(fmt.coin_card(ev.to_dict(), True))["reason"].startswith("خارج از سقف")
    watch = parse(fmt.coin_card(watch_eval().to_dict()))["reason"]
    assert watch == "برگشت به سطح شکسته‌شده D + هنوز تأیید 1H نیامده + شکست تازه، منتظر برگشت"


def _all_keys_reason_can_use(cfg) -> set[str]:
    flags = set(re.findall(r'flags\.append\("(\w+)"\)', TECHNICAL.read_text(encoding="utf-8")))
    labels = set(cfg["scanners"]["label_quality"])
    kinds = {f"level:{k}" for k in ("cluster", "flip", "prev_high", "prev_low", "round")}
    triggers = {f"trigger:{t}" for t in ("engulfing", "pin_bar", "strong_close")}
    patterns = {f"pattern:{p}" for p in ("double_bottom", "double_top", "inverse_head_shoulders",
                                         "head_shoulders", "triangle", "rising_wedge",
                                         "falling_wedge", "bull_flag", "bear_flag")}
    return (flags | labels | kinds | triggers | patterns |
            {"choch", "rsi:1", "rsi:-1", "macd", "pullback_volume", "breakout_volume",
             "out_of_cap", "not_issued"})


def test_every_key_reason_uses_has_a_persian_phrase(cfg):
    assert len(re.findall(r'flags\.append\("(\w+)"\)', TECHNICAL.read_text(encoding="utf-8"))) >= 7
    keys = _all_keys_reason_can_use(cfg)
    assert keys <= set(fmt.REASON_FA), keys - set(fmt.REASON_FA)
    for k in keys:
        assert not re.search(r"[A-Z]{2,}_|_[a-z]", fmt.REASON_FA[k])       # no codes


def test_no_reason_is_longer_than_reason_max(cfg):
    import itertools
    keys = _all_keys_reason_can_use(cfg)
    flags = [k for k in keys if k in set(re.findall(r'flags\.append\("(\w+)"\)',
                                                    TECHNICAL.read_text(encoding="utf-8")))]
    labels = list(cfg["scanners"]["label_quality"])
    notes = ["pullback_volume", "breakout_volume"] + sorted(k for k in keys if k.startswith("pattern:"))
    for side, kind, tf in itertools.product((1, -1), ("cluster", "flip", "prev_high", "round"),
                                            ("4h", "1w")):
        for grade, out_of_cap in (("A", False), ("B", True), ("Watch", False)):
            ev = short_eval() if side == -1 else signal_eval()
            ev.grade, ev.plan.support_kind, ev.plan.support_tf = grade, kind, tf
            ev.trigger_1h, ev.confirmations = "strong_close", ["choch", "rsi", "macd", "rvol"]
            ev.labels, ev.notes, ev.flags = labels, notes, ["not_issued", *flags]
            d = ev.to_dict()
            used = fmt.reason_keys(d, out_of_cap)
            assert set(used) <= set(fmt.REASON_FA) and len(used) <= fmt.REASON_PARTS
            assert len(fmt.reason(d, out_of_cap)) <= fmt.REASON_MAX


# -------------------------------------------------------- score explanation
def test_score_explanation_thresholds_come_from_config(cfg):
    text = fmt.score_explanation(cfg)
    g = cfg["grades"]
    to_fa = fmt.fa
    assert f"{to_fa(g['A'])} به بالا رده A" in text
    assert f"{to_fa(g['B'])} تا {to_fa(g['A'] - 1)} رده B" in text
    assert f"{to_fa(g['watch'])} تا {to_fa(g['B'] - 1)} فقط Watch" in text
    assert f"زیر {to_fa(g['watch'])} چیزی ارسال نمی‌شود" in text


def test_score_explanation_follows_config_changes(cfg):
    import copy
    c = copy.deepcopy(cfg)
    c["grades"].update(A=80, B=70, watch=60)
    c["technical"]["phase"]["points_both"] = 12
    text = fmt.score_explanation(c)
    assert "۸۰ به بالا رده A" in text and "۷۰ تا ۷۹ رده B" in text and "۶۰ تا ۶۹ فقط Watch" in text
    assert "فاز بازار ۱۲" in text


def test_score_explanation_when_grade_b_is_off(cfg):
    import copy
    c = copy.deepcopy(cfg)
    c["grades"]["min_signal"] = "A"
    text = fmt.score_explanation(c)
    assert "رده B که فعلاً خاموش است" in text and "با نصف ریسک" not in text


def test_score_explanation_weights_add_up_to_the_l6_maximum(cfg):
    t = cfg["technical"]
    weights = [t["phase"]["points_both"], t["dow"]["points_both"], t["levels"]["points_strong"],
               t["volume"]["max_points"], t["candles"]["max_points"],
               t["cycles"]["points_all_aligned_fresh"], t["patterns"]["max_points"],
               t["confirmation"]["max_points"]]
    assert sum(weights) == 100
    text = fmt.score_explanation(cfg)
    for w in weights:
        assert fmt.fa(w) in text


@pytest.mark.parametrize("p, s", [(142.3, "142.30"), (83712.5, "83712.50"), (0.53124, "0.5312"),
                                  (0.0000123456, "0.00001235"), (1.5, "1.500")])
def test_fmt_price(p, s):
    assert fmt.fmt_price(p) == s


# ---------------------------------------------------------------- messages
def test_watch_message(cfg):
    msg = fmt.watch_message(watch_eval(), NEUTRAL, cfg)
    lines = msg.split("\n")
    assert parse(card_of(msg))["icon"] == "🟡"
    assert lines[6] == "رژیم بازار: خنثی/رنج (هر دو سمت، فقط رده A و نصف ریسک)"
    assert "ریسک 1٪" not in msg and "لوریج" not in msg                    # no risk line
    assert lines[7] == "" and lines[8] == f"🟡 {fmt.LEGEND['🟡']}"
    assert lines[9] == fmt.score_explanation(cfg) and len(lines) == 10
    no_plan = watch_eval()
    no_plan.plan = None
    assert fmt.watch_message(no_plan, NEUTRAL, cfg) is None


def test_legend_lists_only_the_icons_present(cfg):
    sig = fmt.signal_message(signal_eval(), ALT_SEASON, cfg)
    assert fmt.LEGEND["🟢"] in sig and fmt.LEGEND["🟡"] not in sig
    watch = fmt.watch_message(watch_eval(), NEUTRAL, cfg)
    assert fmt.LEGEND["🟡"] in watch and fmt.LEGEND["🟢"] not in watch
    [both] = fmt.daily_message(funnel(), [open_signal(signal_eval())], [watch_eval().to_dict()],
                               NOW, cfg)
    assert f"🟢 {fmt.LEGEND['🟢']} | 🟡 {fmt.LEGEND['🟡']}" in both
    [only_watch] = fmt.daily_message(funnel(), [], [watch_eval().to_dict()], NOW, cfg)
    assert fmt.LEGEND["🟡"] in only_watch and fmt.LEGEND["🟢"] not in only_watch
    [none] = fmt.daily_message(funnel(), [], [], NOW, cfg)
    assert not any(t in none for t in fmt.LEGEND.values())
    # the legend sits right above the score paragraph, and only the score paragraph follows it
    for msg in (sig, watch, both):
        tail = msg.split("\n")[-2:]
        assert tail[1] == fmt.score_explanation(cfg) and tail[0].startswith(("🟢 سیگنال", "🟡 Watch"))


def test_daily_cards_use_l6_scores_only(cfg):
    evals = [watch_eval().to_dict(), short_eval().to_dict()]
    [msg] = fmt.daily_message(funnel(), [open_signal(signal_eval())], evals, NOW, cfg)
    blocks = msg.split("\n\n")
    assert blocks[0] == "📊 گزارش روزانه 2025-10-01 | رژیم: خنثی/رنج\nسیگنال باز: 1 از 5"
    rows = [parse(b) for b in blocks[1:4]]                        # one blank line between cards
    assert [r["coin"] for r in rows] == ["SOLUSDT", "APTUSDT", "ARBUSDT"]   # L6 score, high first
    assert [r["score"] for r in rows] == [82, 71, 66]
    assert [r["icon"] for r in rows] == ["🟢", "🟡", "🟡"]       # APT: B but not sent
    assert [r["grade"] for r in rows] == ["رده A", "Watch", "Watch"]
    # shortlisted without an L6 plan: names only, no score (their funnel scores were 96, 91, 74)
    assert blocks[4] == "در گلچین بدون ستاپ: SOON, GRASS, PUMP"
    assert not any(s in msg for s in ("96", "91", " 80", "78"))
    assert blocks[5].split("\n")[1] == fmt.score_explanation(cfg) and len(blocks) == 6


def test_daily_marks_ab_setups_that_were_not_sent(cfg):
    # APT is grade B in the latest L6 run but has no open signal (e.g. the cap held it back)
    [msg] = fmt.daily_message(funnel(), [], [short_eval().to_dict()], NOW, cfg)
    row = parse(msg.split("\n\n")[1])
    assert row["icon"] == "🟡" and row["score"] == 71 and row["reason"].startswith("سیگنال صادر نشد")


def test_daily_without_setups_shows_no_score(cfg):
    [msg] = fmt.daily_message(funnel(), [], [], NOW, cfg)
    assert msg.split("\n")[2] == "ستاپ فعالی نیست."
    assert "امتیاز" not in msg and "SOON, GRASS, ARB, SOL, PUMP" in msg


def _many_setups(n: int) -> list[dict]:
    out = []
    for i in range(n):
        ev = watch_eval()
        ev.base, ev.score = f"COIN{i:02d}", 64 - i * 0.1
        out.append(ev.to_dict())
    return out


def test_long_daily_report_is_split_between_cards(cfg):
    evals = _many_setups(30)
    parts = fmt.daily_message(funnel(), [open_signal(signal_eval())], evals, NOW, cfg)
    assert len(parts) >= 2
    assert all(len(p) <= fmt.DAILY_SPLIT for p in parts)
    assert parts[0].startswith("📊 گزارش روزانه 2025-10-01")
    cards = []
    for p in parts:
        for block in p.split("\n\n"):
            if block.startswith(("🟢 ", "🟡 ")) and "USDT |" in block:
                cards.append(parse(block))                        # every card is whole
    assert [c["coin"] for c in cards] == ["SOLUSDT"] + [f"COIN{i:02d}USDT" for i in range(30)]
    expl = fmt.score_explanation(cfg)
    assert parts[-1].endswith(expl) and all(expl not in p for p in parts[:-1])
    assert all(fmt.LEGEND["🟡"] not in p for p in parts[:-1]) and fmt.LEGEND["🟢"] in parts[-1]
    # nothing is lost or added by the split
    one = "\n\n".join(parts)
    assert one.count("\n\nچرا") == 0 and one.count("چرا: ") == 31


def test_short_daily_report_stays_one_message(cfg):
    assert len(fmt.daily_message(funnel(), [], _many_setups(5), NOW, cfg)) == 1


def test_updates_are_one_line_without_score(cfg):
    sig = {"symbol": "SOL", "side": "long"}
    cases = {
        Event("filled", 0, 152.30): "✅ SOLUSDT | ورود فعال شد | 152.30",
        Event("tp1", 0, 158.10, 2.1): "🎯 SOLUSDT | TP1 | +2.1R | SL به ورود",
        Event("tp2", 0, 163.20, 3.2): "🎯 SOLUSDT | TP2 | +3.2R | تریل روی MA25 4H",
        Event("sl", 0, 148.90, -1.0): "🛑 SOLUSDT | SL | -1.0R",
        Event("breakeven", 0, 152.3, 1.05): "⚪️ SOLUSDT | SL در ورود | +1.1R",
        Event("tp3", 0, 160.0, 2.3, "ma25"): "🏁 SOLUSDT | خروج نهایی (MA25 4H) | +2.3R",
        Event("expired", 0): "⌛️ SOLUSDT | منقضی شد | ورود در 24 ساعت فعال نشد",
        Event("cancelled", 0, 158.1, 0, "tp1_before_entry"): "❌ SOLUSDT | لغو شد | TP1 قبل از ورود",
    }
    for e, expected in cases.items():
        msg = fmt.event_message(sig, e, cfg)
        assert msg == expected and "\n" not in msg and "امتیاز" not in msg
    short = {"symbol": "APT", "side": "short"}
    assert fmt.event_message(short, Event("cancelled", 0, 2.5, 0, "closed_beyond_sl"), cfg) == \
        "❌ APTUSDT | لغو شد | کلوز 4H بالای SL"


def test_confirm_4h_signal_card_keeps_its_wording(cfg):
    msg = fmt.signal_message(confirm_eval(), ALT_SEASON, cfg)
    assert msg.split("\n")[:6] == [
        "🟢 SOLUSDT | LONG | امتیاز 82 (رده A)",
        "ورود پس از تأیید 4H حوالی 152.30",
        "حد ضرر (SL): 148.90",
        "هدف ۱ (TP1): 158.10",
        "هدف ۲ (TP2): 163.20 (نهایی پس از فعال شدن)",
        "چرا: برگشت قیمت به حمایت 4H + کندل برگشتی قوی 1H",
    ]


def test_confirm_4h_fill_shows_the_actual_entry_sl_and_tp1(cfg):
    sig = {"symbol": "SOL", "side": "long",
           "payload": {"lifecycle": {"confirm": {}, "entry": 153.1, "sl": 150.1, "tp1": 159.1}}}
    assert fmt.event_message(sig, Event("filled", 0, 153.1), cfg) == \
        "✅ SOLUSDT | ورود فعال شد (تأیید 4H) | 153.10 | SL 150.10 | TP1 159.10"
    # signals of the default entry mode keep the short line
    plain = {"symbol": "SOL", "side": "long", "payload": {"lifecycle": {"entry": 152.3}}}
    assert fmt.event_message(plain, Event("filled", 0, 152.30), cfg) == "✅ SOLUSDT | ورود فعال شد | 152.30"
    for reason in ("fill_rr", "fill_sl_too_wide", "fill_sl_too_tight", "fill_stop_wrong_side"):
        msg = fmt.event_message(sig, Event("cancelled", 0, 153.1, 0, reason), cfg)
        assert msg.startswith("❌ SOLUSDT | لغو شد | پس از تأیید 4H") and "fill_" not in msg


def test_dry_run_prints_exactly_what_is_sent(cfg):
    out = io.StringIO()
    n = Notifier(cfg, load_secrets({}), out=out)
    msg = fmt.signal_message(signal_eval(), ALT_SEASON, cfg)
    n.send(msg)
    assert out.getvalue() == f"----- telegram (dry-run) -----\n{msg}\n\n" and n.sent == [msg]


def test_split_respects_limit():
    text = "\n".join("x" * 100 for _ in range(100))
    parts = split(text, 1000)
    assert all(len(p) <= 1000 for p in parts) and "\n".join(parts) == text
    assert split("a" * 2500, 1000) == ["a" * 1000, "a" * 1000, "a" * 500]


def test_notifier_dry_run_prints(cfg):
    out = io.StringIO()
    n = Notifier(cfg, load_secrets({}), out=out)
    assert n.dry_run and n.send("سلام")
    assert "dry-run" in out.getvalue() and "سلام" in out.getvalue()


class FakeResp:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text = status, body or {}, ""

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return self.responses.pop(0)


def test_notifier_posts_and_retries_429(cfg):
    session = FakeSession([FakeResp(429, {"parameters": {"retry_after": 3}}), FakeResp(200)])
    waits = []
    n = Notifier(cfg, load_secrets({"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "42"}),
                 session=session, sleep=waits.append)
    assert n.send("hi") and waits == [3.0]
    url, body = session.calls[-1]
    assert url.endswith("/botT/sendMessage") and body["chat_id"] == "42" and body["text"] == "hi"
    assert not Notifier(cfg, load_secrets({"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "42"}),
                        session=FakeSession([FakeResp(400)])).send("x")


def _sig(grade, status, r, closed_at):
    return {"grade": grade, "status": status,
            "payload": {"lifecycle": {"realized_r": r, "closed_at": closed_at}}}


def test_performance_message_counts_only_filled_trades():
    from agent.notify.formatter import performance_message, performance_stats
    now = 100 * 86_400_000
    sigs = [_sig("A", "tp3", 2.0, now - 86_400_000), _sig("A", "sl", -1.0, now - 30 * 86_400_000),
            _sig("B", "breakeven", 0.3, now - 2 * 86_400_000), _sig("B", "expired", 0.0, now - 1),
            _sig("B", "tp1", 0.5, None)]
    total = performance_stats(sigs)
    assert (total["n"], total["wins"], total["unfilled"], total["open"]) == (3, 2, 1, 1)
    assert round(total["r"], 2) == 1.3 and total["by_grade"]["A"]["n"] == 2
    week = performance_stats(sigs, now - 7 * 86_400_000)
    assert (week["n"], week["unfilled"], week["open"]) == (2, 1, 0)
    text = performance_message(sigs, now)
    assert "از ابتدا: 3 معامله" in text and "فقط 3 معامله" in text
    assert performance_message([], now).count("معامله‌ی بسته‌شده‌ای نبود") == 2
