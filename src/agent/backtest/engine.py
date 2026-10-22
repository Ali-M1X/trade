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

import pandas as pd

from ..data.dominance import dominance_series, merged_history
from ..layers.classic_systems import evaluate_classic
from ..layers.funnel import run_funnel
from ..layers.majors import Majors
from ..layers.pairs import returns_corr_beta
from ..layers.price_regime import alt_index, market_states
from ..layers.regime import Regime, compute_regime, make_price_regime
from ..layers.scanners import CoinData, liquid
from ..layers.technical import evaluate
from ..regime_systems import system_cfg
from ..layers.trade import flip_level
from ..signals.lifecycle import OPEN, on_4h_close, on_candle, on_time
from ..signals.manager import SignalBook
from ..store.repository import Repository
from .history import History

log = logging.getLogger(__name__)
HOUR = 3_600_000
H4 = 4 * HOUR
DAY = 24 * HOUR


class Backtest:
    def __init__(self, cfg: dict, data_repo, start_ms: int, end_ms: int):
        self.cfg, self.data = cfg, data_repo
        self.start = (start_ms // H4 + 1) * H4
        self.end = end_ms
        self.h = History(cfg, data_repo)
        self.book_repo = Repository()
        self.book = SignalBook(self.book_repo, cfg)
        self.rows = data_repo.get_state("bt_universe", [])
        if cfg["backtest"].get("universe_rank"):      # replay only the top N of a wider list
            self.rows = [r for r in self.rows if r.get("u_rank", 0) < cfg["backtest"]["universe_rank"]]
        self.categories = data_repo.get_state("bt_categories", {})
        self.price = cfg["regime"].get("source") == "price"
        self.systems = self._systems() if self.price else {}
        self.dom = self._price_series() if self.price else dominance_series(merged_history(data_repo))
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
            if h % DAY == 0 and self.state and self._classic():
                self.classic(h)
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
            if self.price:
                row = dom.iloc[-1]
                self._regime = (last_dom, make_price_regime(int(row["state"]), int(row["alts_dir"]),
                                                            self.cfg))
            else:
                self._regime = (last_dom, compute_regime(dom, self.cfg))
        name = self._regime[1].name
        res = run_funnel(dom, majors, coins, markets, self.categories, self.cfg_for(name),
                         breakout_watch=self.watch, now_ms=t, regime=self._regime[1])
        self.watch = res.breakout_watch
        self.state = res.to_dict()
        if self.systems.get(name, "") is None or self._classic():    # off, or a rule system
            self.state["shortlist"] = []
        self.stats["funnel_runs"] += 1
        self.stats["shortlisted"] += len(res.shortlist)

    def _systems(self) -> dict:
        """Regime name -> its system's config (None = "off"); see regime_systems.py."""
        return {name: system_cfg(self.cfg, name) for name in ("bull", "range", "bear")}

    def cfg_for(self, regime_name: str | None) -> dict:
        return self.systems.get(regime_name) or self.cfg

    def _classic(self) -> dict | None:
        """The rule system (layers/classic_systems.py) of the current regime, if it has one."""
        if not self._regime or self.systems.get(self._regime[1].name, "") is None:
            return None
        return self.cfg_for(self._regime[1].name).get("system")

    def classic(self, t: int) -> None:
        regime, majors = Regime(**self.state["regime"]), Majors(**self.state["majors"])
        cfg, sys = self.cfg_for(regime.name), self._classic()
        hits = []
        for row in self.rows:
            info = self.h.coin_row(row, t)
            if info is None or not liquid(info, cfg):
                continue
            base = row["symbol"].upper()
            d = self.h.closed(base, "1d", t).iloc[-400:]
            for side in sys.get("sides", [1, -1]):
                if not regime.allows(side):
                    continue
                self.stats["evaluations"] += 1
                hit = evaluate_classic(base, d, side, sys, regime.risk_for(side, base == "BTC", True)
                                       * majors.risk_multiplier, cfg)
                if hit:
                    hits.append(hit)
        for ev, _ in sorted(hits, key=lambda h: (-h[1], h[0].base)):
            self.stats["grade_A"] += 1
            adm = self.book.admit(ev.base, ev.side, ev.score, t, self._corr(t))
            if not adm.ok:
                self.stats[f"blocked_{adm.reason}"] += 1
                continue
            self.book.cfg = cfg
            self.book.create(ev, t, {"regime": self.state["regime"], "majors": self.state["majors"],
                                     "evaluation": ev.to_dict(), "out_of_cap": adm.out_of_cap,
                                     "system": sys["kind"]})
            self.book.cfg = self.cfg
            self.stats["signals"] += 1

    def _price_series(self):
        """Daily rows for the price regime, stamped at the day's close - 1 ms so that
        `ts <= t` keeps only closed days and a floor to the day gives the candle's own day:
        state/alts_dir (layers/price_regime.py) and total2 = the alt index (pairs read its
        daily closes in place of TOTAL2)."""
        p = self.cfg["regime"]["price"]
        cols = ["high", "low", "close"]
        btc = self.h.raw("BTC", "1d").set_index("ts")[cols].astype(float)
        alts = alt_index({b: f.set_index("ts")[cols].astype(float)
                          for b in p["basket"] if len(f := self.h.raw(b, "1d"))})
        alts = alts.reindex(btc.index).ffill()
        st = market_states(btc, alts, p).dropna()
        out = pd.DataFrame({"ts": st.index + DAY - 1, "state": st["state"].astype(int),
                            "alts_dir": st["alts"].astype(int),
                            "total2": alts["close"].reindex(st.index).to_numpy()})
        return out.reset_index(drop=True)

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
        cfg = self.cfg_for(regime.name)
        for item in self.state["shortlist"]:
            base, side = item["base"], item["side"]
            frames = self._frames(base, ["1w", "1d", "4h", "1h"], t)
            if len(frames) < 4:
                continue
            extra = [flip_level(item["flip_level"], cfg)] if item.get("flip_level") else []
            funding = self.h.funding_until(base, t, 1)
            ev = evaluate(base, side, frames, regime, majors, cfg,
                          funding=funding[0] if funding else None,
                          btc_pair_up=item["pairs"]["dirs"].get("BTC", {}).get("1d") == 1,
                          extra_levels=extra, labels=item["labels"])
            self.stats["evaluations"] += 1
            if ev.rejected:
                self.stats[f"rejected_{ev.rejected}"] += 1
            if ev.grade:
                self.stats[f"grade_{ev.grade}"] += 1
            if not ev.is_signal:
                continue
            adm = self.book.admit(base, side, ev.score, t, self._corr(t))
            if not adm.ok:
                self.stats[f"blocked_{adm.reason}"] += 1
                continue
            self.book.cfg = cfg                         # the plan's lifecycle settings
            self.book.create(ev, t, {"regime": self.state["regime"], "majors": self.state["majors"],
                                     "evaluation": ev.to_dict(), "out_of_cap": adm.out_of_cap})
            self.book.cfg = self.cfg
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
            cfg = self.cfg_for(sig["payload"]["regime"]["name"])
            events = []
            h1 = self.h.closed(base, "1h", t)
            for r in h1[h1["ts"] > lc["last_candle_ts"]].itertuples():
                events += on_candle(lc, int(r.ts), float(r.high), float(r.low), cfg)
            if t % H4 == 0 and lc["status"] in OPEN:
                f = self.h.frame(base, "4h", t)
                if f is not None and int(f.df["ts"].iloc[-1]) == t - H4:
                    choch = any(e.idx == f.n - 1 and e.kind == "CHoCH" and e.direction == -lc["side"]
                                for e in f.events)
                    events += on_4h_close(lc, t - H4, f.close, float(f.last[ma]), choch,
                                          float(f.last["open"]))
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
                "regime": p["regime"]["name"], "system": p.get("system", "house"), "status": s["status"],
                # the lifecycle's entry/stop: the plan's, or rebuilt at a confirm_4h fill
                "order": p["plan"]["order"], "entry": p["lifecycle"]["entry"],
                "sl": p["lifecycle"]["sl"], "tp1_r": _tp1_r(p),
                "risk_pct": p["plan"]["risk_pct"], "out_of_cap": p.get("out_of_cap", False),
                "gross_r": p["lifecycle"]["realized_r"],
                "filled_at": p["lifecycle"]["filled_at"], "closed_at": p["lifecycle"]["closed_at"],
                "events": [(e["kind"], e["ts"], e["payload"].get("price"))
                           for e in self.book_repo.get_events(s["id"]) if e["kind"] != "created"],
            })
        return out


def _tp1_r(payload: dict) -> float:
    lc = payload["lifecycle"]
    if "confirm" not in lc or not lc["filled_at"]:
        return payload["plan"]["tp1_r"]
    return (lc["tp1"] - lc["entry"]) * lc["side"] / lc["risk"]


def _date(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
