import io
import math
import re
from pathlib import Path

import pytest

from agent.config import load_secrets
from agent.notify import formatter as fmt
from agent.notify.telegram import Notifier, split
from agent.signals.lifecycle import Event
from message_examples import (NOW, confirm_eval, funnel, open_signal, short_eval, signal_eval,
                              watch_eval)

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


def test_signal_message_is_an_instruction_one_figure_per_line(cfg):
    lines = fmt.signal_message(signal_eval(), cfg).split("\n")
    assert lines == [
        "🟢 اقدام: خرید SOLUSDT (LONG)",
        "سفارش: لیمیت خرید در 152.30",
        "اعتبار: اگر تا ۲۴ ساعت پر نشد، پیام لغو می‌آید",
        "حد ضرر: 148.90 (2.2٪ فاصله)",
        "TP1: 158.10 ← ۵۰٪ را ببند و حد ضرر را به ورود ببر",
        "TP2: 163.20 ← ۳۰٪ دیگر را ببند",
        "باقی ۲۰٪: نگه دار تا پیام خروج",
        "ریسک: 1٪ موجودی",
        "حجم پوزیشن: 45٪ موجودی (مارجین 4.5٪)",
        "لوریج: 10x ایزوله",
        "دلیل: پولبک به حمایت 4H + انگالف 1H",
        "امتیاز: 82 (رده A)",
    ]
    assert "ℹ️" not in "\n".join(lines)


def test_market_signal_says_enter_now_and_when_not_to(cfg):
    ev = short_eval()
    lines = fmt.signal_message(ev, cfg).split("\n")
    assert lines[0] == "🟢 اقدام: فروش APTUSDT (SHORT)"
    assert lines[1] == "سفارش: همین الان مارکت فروش (حدود 2.415)"
    assert lines[2] == f"شرط: اگر قیمت زیر {fmt.fmt_price(ev.plan.entry_low)} است، وارد نشو"
    assert lines[-1] == "امتیاز: 71 (رده B)"


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
             "neutral_regime_needs_A", "counter_trend_needs_A", "funding_crowded", "grade_b_off"}
    kinds = {f"level:{k}" for k in ("cluster", "flip", "prev_high", "prev_low", "round")}
    assert labels | flags | kinds <= set(fmt.REASON_FA)


@pytest.mark.parametrize("p, s", [(142.3, "142.30"), (83712.5, "83712.50"), (0.53124, "0.5312"),
                                  (0.0000123456, "0.00001235"), (1.5, "1.500")])
def test_fmt_price(p, s):
    assert fmt.fmt_price(p) == s


# ---------------------------------------------------------------- messages
def test_watch_message(cfg):
    lines = fmt.watch_message(watch_eval(), cfg).split("\n")
    assert lines[0] == "🟡 زیر نظر: ARBUSDT (LONG) | فعلاً کاری نکن"
    assert lines[1] == "اگر سیگنال شد: خرید حوالی 0.6810" and lines[-1] == "امتیاز: 66"
    no_plan = watch_eval()
    no_plan.plan = None
    assert fmt.watch_message(no_plan, cfg) is None


def test_watch_alerts_are_off(cfg):
    assert cfg["watch_alerts"]["enabled"] is False


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
    assert len(lines) == 6 and "ℹ️" not in msg


def test_daily_marks_ab_setups_that_were_not_sent(cfg):
    # APT is grade B in the latest L6 run but has no open signal (e.g. the cap held it back)
    msg = fmt.daily_message(funnel(), [], [short_eval().to_dict()], NOW, cfg)
    row = parse(msg.split("\n")[2])
    assert row["icon"] == "🟡" and row["score"] == 71 and row["reason"].startswith("صادر نشد")


def test_daily_without_setups_shows_no_score(cfg):
    msg = fmt.daily_message(funnel(), [], [], NOW, cfg)
    assert msg.split("\n")[2] == "ستاپ فعالی نیست."
    assert "امتیاز" not in msg and "SOON, GRASS, ARB, SOL, PUMP" in msg


def test_updates_say_what_to_do(cfg):
    sig = {"symbol": "SOL", "side": "long",
           "payload": {"lifecycle": {"entry": 152.3, "sl": 148.9, "tp1": 158.1}}}
    cases = {
        Event("filled", 0, 152.30): "✅ SOLUSDT | سفارش پر شد در 152.30\n"
                                     "اقدام: مطمئن شو حد ضرر 148.90 و TP1 158.10 ثبت است",
        Event("tp1", 0, 158.10, 2.1): "🎯 SOLUSDT | TP1 خورد | +2.1R\n"
                                       "اقدام: ۵۰٪ را ببند و حد ضرر را به ورود (152.30) ببر",
        Event("tp2", 0, 163.20, 3.2): "🎯 SOLUSDT | TP2 خورد | +3.2R\n"
                                       "اقدام: ۳۰٪ دیگر را ببند؛ باقی را تا پیام خروج نگه دار",
        Event("sl", 0, 148.90, -1.0): "🛑 SOLUSDT | حد ضرر خورد | -1.0R\n"
                                       "اقدام: اگر پوزیشن هنوز باز است، ببندش",
        Event("breakeven", 0, 152.3, 1.05): "⚪️ SOLUSDT | قیمت به ورود برگشت | +1.1R\n"
                                             "اقدام: اگر باقی پوزیشن هنوز باز است، ببندش",
        Event("tp3", 0, 160.0, 2.3, "ma25"): "🏁 SOLUSDT | خروج نهایی (کلوز 4H زیر MA25) | +2.3R\n"
                                              "اقدام: باقی پوزیشن را همین الان ببند",
        Event("expired", 0): "⌛️ SOLUSDT | در ۲۴ ساعت پر نشد\nاقدام: سفارش را لغو کن",
        Event("cancelled", 0, 158.1, 0, "tp1_before_entry"):
            "❌ SOLUSDT | لغو شد: قیمت قبل از ورود به TP1 رسید\nاقدام: سفارش را لغو کن و وارد نشو",
    }
    for e, expected in cases.items():
        msg = fmt.event_message(sig, e, cfg)
        assert msg == expected and "امتیاز" not in msg
    short = {"symbol": "APT", "side": "short"}
    assert fmt.event_message(short, Event("cancelled", 0, 2.5, 0, "closed_beyond_sl"), cfg) == \
        "❌ APTUSDT | لغو شد: کلوز 4H بالای حد ضرر\nاقدام: سفارش را لغو کن و وارد نشو"


def test_confirm_4h_signal_says_wait(cfg):
    lines = fmt.signal_message(confirm_eval(), cfg).split("\n")
    assert lines[1] == "سفارش: هنوز وارد نشو؛ وقتی کندل 4H حوالی 152.30 تأیید داد پیام ورود می‌آید"
    assert lines[3].endswith("(تقریبی؛ نهایی در پیام ورود)")


def test_confirm_4h_fill_says_enter_now_with_the_actual_sl_and_tps(cfg):
    sig = {"symbol": "SOL", "side": "long",
           "payload": {"lifecycle": {"confirm": {}, "entry": 153.1, "sl": 150.1, "tp1": 159.1,
                                     "tp2": 162.1}}}
    assert fmt.event_message(sig, Event("filled", 0, 153.1), cfg) == (
        "✅ SOLUSDT | تأیید 4H آمد\nاقدام: همین الان مارکت خرید (حدود 153.10)\n"
        "حد ضرر: 150.10\nTP1: 159.10\nTP2: 162.10")
    for reason in ("fill_rr", "fill_sl_too_wide", "fill_sl_too_tight", "fill_stop_wrong_side"):
        msg = fmt.event_message(sig, Event("cancelled", 0, 153.1, 0, reason), cfg)
        assert msg.startswith("❌ SOLUSDT | لغو شد: پس از تأیید 4H") and "fill_" not in msg


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
