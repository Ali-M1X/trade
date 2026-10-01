import io
import math
import re
from pathlib import Path

import pytest

from agent.config import load_secrets
from agent.notify import formatter as fmt
from agent.notify.telegram import Notifier, split
from agent.signals.lifecycle import Event
from message_examples import NOW, funnel, open_signal, short_eval, signal_eval, watch_eval

STRATEGY = Path(__file__).resolve().parents[1] / "docs" / "STRATEGY.md"
FIELDS = ["side", "score", "entry", "sl", "tp1", "tp2", "reason"]
LINE = re.compile(r"^(🟢|🟡) ([A-Z0-9]+USDT) \| (LONG|SHORT) \| امتیاز (\d+) \| ورود ([\d.]+) \| "
                  r"SL ([\d.]+) \| TP1 ([\d.]+) \| TP2 ([\d.]+) \| ([^|\n]+)$")


def strategy_signal_example() -> str:
    text = STRATEGY.read_text(encoding="utf-8")
    section = text[text.index("## لایه ۷"):]
    return re.findall(r"```\n(.*?)\n```", section, re.S)[1]


def parse(line: str) -> dict:
    m = LINE.match(line)
    assert m, f"not a coin line: {line!r}"
    icon, coin, side, score, entry, sl, tp1, tp2, why = m.groups()
    return {"icon": icon, "coin": coin, "side": side, "score": int(score), "entry": entry,
            "sl": sl, "tp1": tp1, "tp2": tp2, "reason": why}


# ---------------------------------------------------------------- coin line
def test_signal_message_matches_strategy_md(cfg):
    assert fmt.signal_message(signal_eval(), cfg) == strategy_signal_example()


def test_signal_message_layout(cfg):
    lines = fmt.signal_message(signal_eval(), cfg).split("\n")
    row = parse(lines[0])
    assert row == {"icon": "🟢", "coin": "SOLUSDT", "side": "LONG", "score": 82, "entry": "152.30",
                   "sl": "148.90", "tp1": "158.10", "tp2": "163.20",
                   "reason": "پولبک به حمایت 4H + انگالف 1H"}
    assert lines[1] == "ریسک 1٪ | حجم 45٪ موجودی | لوریج 10x"
    assert lines[2] == "" and lines[3] == fmt.score_explanation(cfg) and len(lines) == 4


def test_every_coin_line_has_the_same_fields_in_order(cfg):
    for ev in (signal_eval(), watch_eval(), short_eval()):
        line = fmt.coin_line(ev.to_dict())
        assert "\n" not in line
        row = parse(line)
        assert row["score"] == math.floor(ev.score)
        assert row["entry"] == fmt.fmt_price(ev.plan.entry)
        assert row["sl"] == fmt.fmt_price(ev.plan.sl)
        assert line.count(" | ") == len(FIELDS)


def test_icons_by_grade(cfg):
    assert fmt.coin_line(signal_eval().to_dict()).startswith("🟢 ")
    assert fmt.coin_line(short_eval().to_dict()).startswith("🟢 APTUSDT | SHORT")
    assert fmt.coin_line(watch_eval().to_dict()).startswith("🟡 ")


def test_score_is_rounded_down_so_it_never_crosses_a_grade(cfg):
    ev = signal_eval()
    ev.score, ev.grade = 74.9, "B"
    assert parse(fmt.coin_line(ev.to_dict()))["score"] == 74
    ev.score = 75.0
    assert fmt.shown_score(ev.score) == 75


def test_reason_is_short_persian_and_without_codes(cfg):
    ev = short_eval()
    ev.labels = ["EARLY_TREND", "RS_LEADER", "HOT_SECTOR", "BREAKOUT_WATCH"]
    ev.notes = ["pullback_volume", "pattern:double_top", "pattern:bear_flag"]
    ev.confirmations = ["choch", "rsi", "macd", "trigger_candle", "rvol"]
    ev.trigger_1h = "pin_bar"
    for out_of_cap in (False, True):
        why = parse(fmt.coin_line(ev.to_dict(), out_of_cap))["reason"]
        assert len(why) <= fmt.REASON_MAX
        assert not re.search(r"[A-Z]{2,}_[A-Z]+|pattern:|pullback_volume", why)
    assert parse(fmt.coin_line(ev.to_dict(), True))["reason"].startswith("خارج از سقف")
    watch = parse(fmt.coin_line(watch_eval().to_dict()))["reason"]
    assert "منتظر تأیید 1H" in watch


def test_every_flag_and_label_has_a_phrase(cfg):
    labels = set(cfg["scanners"]["label_quality"])
    flags = {"not_confirmed", "lower_cycle_correcting", "chase", "btc_weak",
             "neutral_regime_needs_A", "funding_crowded"}
    kinds = {f"level:{k}" for k in ("cluster", "flip", "prev_high", "prev_low", "round")}
    assert labels | flags | kinds <= set(fmt.REASON_FA)


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
    msg = fmt.watch_message(watch_eval(), cfg)
    first, blank, expl = msg.split("\n")
    assert parse(first)["icon"] == "🟡" and blank == "" and expl == fmt.score_explanation(cfg)
    no_plan = watch_eval()
    no_plan.plan = None
    assert fmt.watch_message(no_plan, cfg) is None


def test_daily_table_uses_l6_scores_only(cfg):
    evals = [watch_eval().to_dict(), short_eval().to_dict()]
    msg = fmt.daily_message(funnel(), [open_signal(signal_eval())], evals, NOW, cfg)
    lines = msg.split("\n")
    assert lines[0] == "📊 گزارش روزانه 2025-10-01 | رژیم: خنثی/رنج"
    assert lines[1] == "سیگنال باز: 1 از 5"
    rows = [parse(l) for l in lines[2:5]]
    assert [r["coin"] for r in rows] == ["SOLUSDT", "APTUSDT", "ARBUSDT"]   # L6 score, high first
    assert [r["score"] for r in rows] == [82, 71, 66]
    assert [r["icon"] for r in rows] == ["🟢", "🟡", "🟡"]       # APT: B but not sent
    # shortlisted without an L6 plan: names only, no score (their funnel scores were 96, 91, 74)
    assert lines[5] == "در گلچین بدون ستاپ: SOON, GRASS, PUMP"
    assert not any(s in msg for s in ("96", "91", " 80", "78"))
    assert lines[6] == "" and lines[7] == fmt.score_explanation(cfg) and len(lines) == 8


def test_daily_marks_ab_setups_that_were_not_sent(cfg):
    # APT is grade B in the latest L6 run but has no open signal (e.g. the cap held it back)
    msg = fmt.daily_message(funnel(), [], [short_eval().to_dict()], NOW, cfg)
    row = parse(msg.split("\n")[2])
    assert row["icon"] == "🟡" and row["score"] == 71 and row["reason"].startswith("صادر نشد")


def test_daily_without_setups_shows_no_score(cfg):
    msg = fmt.daily_message(funnel(), [], [], NOW, cfg)
    assert msg.split("\n")[2] == "ستاپ فعالی نیست."
    assert "امتیاز" not in msg and "SOON, GRASS, ARB, SOL, PUMP" in msg


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


def test_dry_run_prints_exactly_what_is_sent(cfg):
    out = io.StringIO()
    n = Notifier(cfg, load_secrets({}), out=out)
    msg = fmt.signal_message(signal_eval(), cfg)
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
