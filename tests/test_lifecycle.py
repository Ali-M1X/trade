import pytest

from agent.signals.lifecycle import H4, new_signal, on_4h_close, on_candle, on_time
from agent.signals.manager import SignalBook
from agent.store.repository import Repository

T0 = 1_760_000_000_000
M15 = 900_000


def plan(side=1, order="limit", entry=100.0, sl=95.0, tp1=110.0, tp2=115.0):
    return {"side": side, "order": order, "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2}


def feed(s, cfg, bars):
    """bars: [(high, low)], one 15m candle each after creation."""
    out = []
    for i, (h, l) in enumerate(bars):
        out += on_candle(s, T0 + (i + 1) * M15, h, l, cfg)
    return out


def kinds(events):
    return [e.kind for e in events]


def test_limit_fill_tp1_tp2_trail_exit(cfg):
    s = new_signal(plan(), T0, cfg)
    assert s["status"] == "pending"
    ev = feed(s, cfg, [(103, 101), (101, 99.5), (111, 104), (116, 110)])
    assert kinds(ev) == ["filled", "tp1", "tp2"]
    assert s["sl_now"] == 100.0 and s["open_frac"] == pytest.approx(0.2)
    # a 4H close under MA25 closes the last 20%
    ev = on_4h_close(s, T0 + 8 * H4, 112.0, 113.0, False)
    assert kinds(ev) == ["tp3"] and ev[0].reason == "ma25"
    # 0.5*2R + 0.3*3R + 0.2*2.4R
    assert s["realized_r"] == pytest.approx(1.0 + 0.9 + 0.48)


def test_stop_before_target_in_the_same_candle(cfg):
    s = new_signal(plan(order="market"), T0, cfg)
    assert s["status"] == "active"
    ev = feed(s, cfg, [(111, 94)])                           # touches TP1 and SL
    assert kinds(ev) == ["sl"] and ev[0].r == pytest.approx(-1)


def test_breakeven_after_tp1(cfg):
    s = new_signal(plan(order="market"), T0, cfg)
    ev = feed(s, cfg, [(110.5, 101), (104, 99.9)])
    assert kinds(ev) == ["tp1", "breakeven"]
    assert ev[-1].r == pytest.approx(0.5 * 2)                # only the TP1 half counted


def test_fill_and_stop_in_one_candle(cfg):
    s = new_signal(plan(), T0, cfg)
    assert kinds(feed(s, cfg, [(101, 94)])) == ["filled", "sl"]


def test_tp1_before_entry_cancels(cfg):
    s = new_signal(plan(entry=100, tp1=110), T0, cfg)
    ev = feed(s, cfg, [(105, 101), (110.2, 102)])
    assert kinds(ev) == ["cancelled"] and ev[0].reason == "tp1_before_entry"
    assert s["realized_r"] == 0


def test_expiry_and_invalidation(cfg):
    s = new_signal(plan(), T0, cfg)
    assert on_time(s, T0 + 6 * H4 - 1) == []
    assert kinds(on_time(s, T0 + 6 * H4)) == ["expired"]
    s = new_signal(plan(), T0, cfg)
    assert kinds(on_4h_close(s, T0, 94.0, 99.0, False)) == ["cancelled"]


def test_short_mirror(cfg):
    s = new_signal(plan(side=-1, entry=100, sl=105, tp1=90, tp2=85), T0, cfg)
    ev = feed(s, cfg, [(100.5, 98), (99, 89.5), (95, 84)])
    assert kinds(ev) == ["filled", "tp1", "tp2"]
    ev = on_4h_close(s, T0 + H4, 88.0, 87.0, False)          # close above MA25
    assert kinds(ev) == ["tp3"] and s["realized_r"] == pytest.approx(1.0 + 0.9 + 0.2 * 2.4)


def test_candles_are_processed_once(cfg):
    s = new_signal(plan(order="market"), T0, cfg)
    assert kinds(on_candle(s, T0 + M15, 110.5, 101, cfg)) == ["tp1"]
    assert on_candle(s, T0 + M15, 116, 101, cfg) == []            # same candle again
    assert on_candle(s, T0 - M15, 116, 101, cfg) == []            # before creation


# ----------------------------------------------------------------- the book
class Ev:
    def __init__(self, base, side=1, score=80, grade="A"):
        from agent.layers.trade import TradePlan
        self.base, self.side, self.score, self.grade = base, side, score, grade
        self.plan = TradePlan(side, "limit", 100, 99.8, 100.2, 95, 110, 115, 2, 3, False,
                              100, "cluster", "4h", 2, 1.0)


def book_with(cfg, n):
    book = SignalBook(Repository(), cfg)
    for i in range(n):
        book.create(Ev(f"C{i}"), T0, {})
    return book


def test_duplicate_and_cap_with_a_plus_override(cfg):
    book = book_with(cfg, 5)
    assert book.admit("C1", 1, 80, T0).reason == "duplicate"
    assert book.admit("NEW", 1, 84, T0).reason == "max_active"
    adm = book.admit("NEW", 1, 85, T0)
    assert adm.ok and adm.out_of_cap
    assert book_with(cfg, 4).admit("NEW", 1, 70, T0).ok


def test_correlated_cap(cfg):
    book = book_with(cfg, 3)
    corr = lambda a, b: 0.9 if b in ("C0", "C1") else 0.2          # noqa: E731
    assert book.admit("NEW", 1, 80, T0, corr).reason == "correlated"
    assert book.admit("NEW", -1, 80, T0, corr).ok                  # other side is fine
    assert book_with(cfg, 1).admit("NEW", 1, 80, T0, corr).ok


def test_loss_streak_pause_until_next_daily_close(cfg):
    from agent.signals.lifecycle import Event
    book = book_with(cfg, 4)
    sigs = book.open_signals()
    t = T0 + 5 * 3_600_000
    notice = None
    for i, s in enumerate(sigs[:3]):
        s["payload"]["lifecycle"]["status"] = "sl"
        notice = book.save(s, [Event("sl", t + i, 95, -1.0)], t + i)
    assert notice == "loss_streak_pause"
    until = book.pause()["paused_until"]
    assert until == (t // 86_400_000 + 1) * 86_400_000
    assert book.admit("NEW", 1, 90, until - 1).reason == "loss_streak_pause"
    assert book.admit("NEW", 1, 90, until).ok


def test_a_win_resets_the_streak(cfg):
    from agent.signals.lifecycle import Event
    book = book_with(cfg, 4)
    s = book.open_signals()
    book.save(s[0], [Event("sl", T0, 95, -1.0)], T0)
    book.save(s[1], [Event("sl", T0, 95, -1.0)], T0)
    book.save(s[2], [Event("tp3", T0, 112, 1.5)], T0)
    book.save(s[3], [Event("sl", T0, 95, -1.0)], T0)
    assert book.pause() == {"count": 1, "paused_until": 0}


def test_cooldown_after_stop(cfg):
    import copy

    from agent.signals.lifecycle import Event
    c = copy.deepcopy(cfg)
    c["lifecycle"]["cooldown_after_stop_h"] = 24
    book = SignalBook(Repository(), c)
    book.create(Ev("SOL"), T0, {})
    [s] = book.open_signals()
    s["payload"]["lifecycle"].update(status="sl", closed_at=T0 + 3_600_000)
    book.save(s, [Event("sl", T0 + 3_600_000, 95, -1.0)], T0 + 3_600_000)
    assert book.admit("SOL", 1, 90, T0 + 10 * 3_600_000).reason == "cooldown"
    assert book.admit("ADA", 1, 90, T0 + 10 * 3_600_000).ok
    assert book.admit("SOL", 1, 90, T0 + 26 * 3_600_000).ok
    # without the switch, a stopped coin can be signalled again right away
    assert SignalBook(book.repo, cfg).admit("SOL", 1, 90, T0 + 2 * 3_600_000).ok
