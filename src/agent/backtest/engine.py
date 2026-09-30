"""Replay L1-L6, the signal book and the lifecycle hour by hour over stored history.

Same code as the live runs; only the data source differs. Order within each hour h:
  1. manage open signals with the 1H candle that closed at h (and the 4H close at h),
  2. at a 4H close, run L1-L5 (regime, majors, scanners, pairs, shortlist),
  3. score the shortlist with L6 and publish through the signal book.
Trades are managed on 1H candles (live uses 15m); a stop and a target inside the same
candle count as the stop.
"""
from __future__ import annotations

import logging
from collections import Counter

from ..data.dominance import dominance_series, merged_history
from ..layers.funnel import run_funnel
from ..layers.majors import Majors
from ..layers.pairs import returns_corr_beta
from ..layers.regime import Regime, compute_regime
from ..layers.scanners import CoinData
from ..layers.technical import evaluate
from ..layers.trade import flip_level
from ..signals.lifecycle import OPEN, on_4h_close, on_candle, on_time
from ..signals.manager import SignalBook
from ..store.repository import Repository
from .history import History

log = logging.getLogger(__name__)
HOUR = 3_600_000
H4 = 4 * HOUR


class Backtest:
    def __init__(self, cfg: dict, data_repo, start_ms: int, end_ms: int):
        self.cfg, self.data = cfg, data_repo
        self.start = (start_ms // H4 + 1) * H4
        self.end = end_ms
        self.h = History(cfg, data_repo)
        self.book_repo = Repository()
        self.book = SignalBook(self.book_repo, cfg)
        self.rows = data_repo.get_state("bt_universe", [])
        self.categories = data_repo.get_state("bt_categories", {})
        self.dom = dominance_series(merged_history(data_repo))
        self.watch: dict = {}
        self._regime: tuple | None = None       # (last dominance ts, Regime)
        self.state: dict | None = None
        self.stats = Counter()
        self.regime_hours = Counter()

    # ------------------------------------------------------------------ loop
    def run(self, progress_every: int = 24 * 30) -> list[dict]:
        h, i = self.start, 0
        while h <= self.end:
            self.manage(h)
            if h % H4 == 0:
                self.funnel(h)
            if self.state:
                self.regime_hours[self.state["regime"]["name"]] += 1
                self.l6(h)
            i += 1
            if progress_every and i % progress_every == 0:
                log.info("backtest at %s: %d signals", _date(h), self.stats["signals"])
            h += HOUR
        return self.trades()

    # ------------------------------------------------------------ L1 - L5
    def funnel(self, t: int) -> None:
        dom = self.dom[self.dom["ts"] <= t]
        if len(dom) < 2:
            return
        tfs = list(self.cfg["majors"]["weights"])
        majors = {"BTC": self._frames("BTC", tfs, t), "ETH": self._frames("ETH", tfs, t),
                  "ETHBTC": self._frames("ETH", tfs, t, "BTC")}
        if "1d" not in majors["BTC"]:
            return
        coins, markets = [], []
        for row in self.rows:
            info = self.h.coin_row(row, t)
            if info is None:
                continue
            markets.append(info)
            base = row["symbol"].upper()
            frames = self._frames(base, ["1d", "4h"], t)
            if len(frames) < 2:
                continue
            coins.append(CoinData(
                base=base, info=info, frames=frames,
                btc_frames=self._frames(base, ["1d", "4h"], t, "BTC") if base != "BTC" else {},
                eth_frames=self._frames(base, ["1d", "4h"], t, "ETH") if base != "ETH" else {},
                funding=self.h.funding_until(base, t, 10),
                categories=self.categories.get(row["id"], [])))
        last_dom = int(dom["ts"].iloc[-1])
        if not self._regime or self._regime[0] != last_dom:     # dominance is daily
            self._regime = (last_dom, compute_regime(dom, self.cfg))
        res = run_funnel(dom, majors, coins, markets, self.categories, self.cfg,
                         breakout_watch=self.watch, now_ms=t, regime=self._regime[1])
        self.watch = res.breakout_watch
        self.state = res.to_dict()
        self.stats["funnel_runs"] += 1
        self.stats["shortlisted"] += len(res.shortlist)

    def _frames(self, base, tfs, t, quote_base=None) -> dict:
        out = {}
        for tf in tfs:
            f = self.h.frame(base, tf, t, quote_base)
            if f is not None:
                out[tf] = f
        return out

    # ------------------------------------------------------------------- L6
    def l6(self, t: int) -> None:
        regime, majors = Regime(**self.state["regime"]), Majors(**self.state["majors"])
        for item in self.state["shortlist"]:
            base, side = item["base"], item["side"]
            frames = self._frames(base, ["1w", "1d", "4h", "1h"], t)
            if len(frames) < 4:
                continue
            extra = [flip_level(item["flip_level"], self.cfg)] if item.get("flip_level") else []
            funding = self.h.funding_until(base, t, 1)
            ev = evaluate(base, side, frames, regime, majors, self.cfg,
                          funding=funding[0] if funding else None,
                          btc_pair_up=item["pairs"]["dirs"].get("BTC", {}).get("1d") == 1,
                          extra_levels=extra, labels=item["labels"])
            self.stats["evaluations"] += 1
            if ev.grade:
                self.stats[f"grade_{ev.grade}"] += 1
            if not ev.is_signal:
                continue
            adm = self.book.admit(base, side, ev.score, t, self._corr(t))
            if not adm.ok:
                self.stats[f"blocked_{adm.reason}"] += 1
                continue
            self.book.create(ev, t, {"regime": self.state["regime"], "majors": self.state["majors"],
                                     "evaluation": ev.to_dict(), "out_of_cap": adm.out_of_cap})
            self.stats["signals"] += 1

    def _corr(self, t: int):
        days = self.cfg["pairs"]["corr_days"]

        def corr(a: str, b: str):
            return returns_corr_beta(self.h.closed(a, "1d", t), self.h.closed(b, "1d", t), days)[0]
        return corr

    # ------------------------------------------------------------- lifecycle
    def manage(self, t: int) -> None:
        ma = f"ma{self.cfg['indicators']['ma_mid']}"
        for sig in self.book.open_signals():
            lc = sig["payload"]["lifecycle"]
            base = sig["symbol"]
            events = []
            h1 = self.h.closed(base, "1h", t)
            for r in h1[h1["ts"] > lc["last_candle_ts"]].itertuples():
                events += on_candle(lc, int(r.ts), float(r.high), float(r.low), self.cfg)
            if t % H4 == 0 and lc["status"] in OPEN:
                f = self.h.frame(base, "4h", t)
                if f is not None and int(f.df["ts"].iloc[-1]) == t - H4:
                    choch = any(e.idx == f.n - 1 and e.kind == "CHoCH" and e.direction == -lc["side"]
                                for e in f.events)
                    events += on_4h_close(lc, t - H4, f.close, float(f.last[ma]), choch)
            events += on_time(lc, t)
            if events:
                self.book.save(sig, events, t)
            else:
                self.book_repo.update_signal(sig["id"], lc["status"], sig["payload"], sig["updated_at"])

    # ---------------------------------------------------------------- output
    def trades(self) -> list[dict]:
        out = []
        for s in self.book_repo.get_signals():
            p = s["payload"]
            out.append({
                "id": s["id"], "base": s["symbol"], "side": p["lifecycle"]["side"],
                "grade": s["grade"], "score": s["score"], "created": s["created_at"],
                "regime": p["regime"]["name"], "status": s["status"],
                "order": p["plan"]["order"], "entry": p["plan"]["entry"], "sl": p["plan"]["sl"],
                "tp1_r": p["plan"]["tp1_r"],
                "risk_pct": p["plan"]["risk_pct"], "out_of_cap": p.get("out_of_cap", False),
                "gross_r": p["lifecycle"]["realized_r"],
                "filled_at": p["lifecycle"]["filled_at"], "closed_at": p["lifecycle"]["closed_at"],
                "events": [(e["kind"], e["ts"], e["payload"].get("price"))
                           for e in self.book_repo.get_events(s["id"]) if e["kind"] != "created"],
            })
        return out


def _date(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
