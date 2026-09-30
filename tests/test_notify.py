import io
import re
from pathlib import Path

import pytest

from agent.config import load_secrets
from agent.layers.technical import Evaluation
from agent.layers.trade import TradePlan
from agent.notify import formatter as fmt
from agent.notify.telegram import Notifier, split
from agent.signals.lifecycle import Event

STRATEGY = Path(__file__).resolve().parents[1] / "docs" / "STRATEGY.md"


def template() -> str:
    text = STRATEGY.read_text(encoding="utf-8")
    section = text[text.index("## لایه ۷"):]
    return re.search(r"```\n(.*?)\n```", section, re.S).group(1)


def example_eval() -> Evaluation:
    plan = TradePlan(1, "limit", 142.7, 142.30, 143.10, 137.80, 152.00, 158.50, 2.1, 3.4, True,
                     142.0, "flip", "1d", 1, 2.0, risk_pct=1.0, sl_pct=3.4, size_pct=29.4,
                     leverage=10, margin_pct=2.94)
    sections = {k: 5.0 for k in ("phase", "dow", "levels", "volume", "candles", "cycles",
                                 "patterns", "confirmation")}
    return Evaluation("SOL", 1, score=81, grade="A", sections=sections,
                      gates={"regime": True, "phase": True, "rr": True, "confirmation": True},
                      confirmations=["retest", "choch", "rsi"], notes=["pullback_volume"],
                      phase_d="TREND_UP", phase_4h="TREND_UP", cycle="fresh_turn",
                      cycle_w=1, cycle_d=1, labels=["PULLBACK", "RS_LEADER"], plan=plan,
                      funding_pct=0.01)


def test_signal_message_matches_strategy_template(cfg):
    msg = fmt.signal_message(example_eval(), {"name": "alt_season"}, {"btc": 4, "ethbtc_d": 1}, 3, cfg)
    assert msg == template()


def test_short_and_out_of_cap_variants(cfg):
    ev = example_eval()
    ev.side, ev.plan.side, ev.a_plus = -1, -1, True
    msg = fmt.signal_message(ev, {"name": "risk_off"}, {"btc": -4, "ethbtc_d": -1}, 6, cfg,
                             out_of_cap=True)
    lines = msg.split("\n")
    assert lines[0].startswith("🔴 SHORT | SOLUSDT") and "رده A+" in lines[0]
    assert lines[1].startswith("⚠️ خارج از سقف")
    assert "(+3.4%)" in msg and "کلوز 4H بالای 137.80" in msg and "CHoCH نزولی" in msg
    assert "سیگنال‌های فعال: 6 از 5" in msg


@pytest.mark.parametrize("p, s", [(142.3, "142.30"), (83712.5, "83712.50"), (0.53124, "0.5312"),
                                  (0.0000123456, "0.00001235"), (1.5, "1.500")])
def test_fmt_price(p, s):
    assert fmt.fmt_price(p) == s


def test_event_messages(cfg):
    sig = {"symbol": "SOL", "side": "long"}
    assert fmt.event_message(sig, Event("filled", 0, 142.5), cfg).startswith("✅ ورود فعال شد | SOLUSDT LONG")
    assert "+2.1R" in fmt.event_message(sig, Event("tp1", 0, 152.0, 2.1), cfg)
    assert "-1.00R" in fmt.event_message(sig, Event("sl", 0, 137.8, -1.0), cfg)
    assert "TP1" in fmt.event_message(sig, Event("cancelled", 0, 152.0, 0, "tp1_before_entry"), cfg)
    assert "24 ساعت" in fmt.event_message(sig, Event("expired", 0), cfg)
    assert "MA25" in fmt.event_message(sig, Event("tp3", 0, 150.0, 2.3, "ma25"), cfg)


def test_watch_and_daily(cfg):
    ev = example_eval()
    ev.grade, ev.flags = "Watch", ["not_confirmed"]
    assert "منتظر تایید ورود در 1H" in fmt.watch_message(ev)
    funnel = {"regime": {"name": "neutral", "usdt_d": -1, "btc_d": 0, "total2": 1},
              "majors": {"btc": 3, "eth": 2, "ethbtc": 1, "ethbtc_d": 1, "btc_weak": False,
                         "divergence": True},
              "hot_categories": ["AI"],
              "shortlist": [{"base": "PUMP", "side": 1, "score": 80.7, "labels": ["EARLY_TREND"]}]}
    sigs = [{"symbol": "UNI", "side": "long", "status": "active", "grade": "B", "score": 70}]
    msg = fmt.daily_message(funnel, sigs, 1_759_276_800_000, cfg)          # 2025-10-01
    assert "خنثی/رنج" in msg and "واگرایی BTC و TOTAL2" in msg
    assert "PUMPUSDT LONG" in msg and "UNIUSDT LONG – active" in msg
    assert "روز 529 پس از هاوینگ 2024-04-20" in msg


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
