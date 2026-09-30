"""`check-sources`: probe every configured free data source and report what answers."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import requests

from .coingecko import CoinGecko
from .exchange import UnsupportedExchange, make_exchange, perp_symbol


@dataclass
class Probe:
    source: str
    check: str
    ok: bool
    ms: int
    detail: str


def _run(source: str, check: str, fn) -> Probe:
    t0 = time.monotonic()
    try:
        detail = fn()
        ok = True
    except Exception as e:  # report every failure, never abort the check
        detail, ok = f"{type(e).__name__}: {str(e)[:160]}", False
    return Probe(source, check, ok, int((time.monotonic() - t0) * 1000), str(detail))


def probe_exchange(name: str, cfg: dict, factory=make_exchange) -> list[Probe]:
    try:
        ex = factory(name, cfg)
    except UnsupportedExchange as e:
        return [Probe(name, "ccxt", False, 0, str(e))]
    perp = perp_symbol("BTC", cfg["exchange"]["quote"])
    probes = [_run(name, "load_markets", lambda: f"{len(ex.load_markets())} markets")]
    if not probes[0].ok:
        return probes
    probes.append(_run(name, "ohlcv 4h BTC perp",
                       lambda: f"last close {ex.fetch_ohlcv(perp, '4h', limit=5)[-1][4]}"))
    probes.append(_run(name, "ohlcv 1d ETH/BTC spot",
                       lambda: f"last close {ex.fetch_ohlcv('ETH/BTC', '1d', limit=5)[-1][4]}"))
    probes.append(_run(name, "funding rate",
                       lambda: f"{ex.fetch_funding_rate(perp)['fundingRate']}"))

    def oi():
        if ex.has.get("fetchOpenInterestHistory"):
            return f"{len(ex.fetch_open_interest_history(perp, '1d', limit=5))} points (history)"
        if ex.has.get("fetchOpenInterest"):
            return f"{ex.fetch_open_interest(perp).get('openInterestAmount')} (current only)"
        raise NotImplementedError("no open interest endpoint in ccxt")
    probes.append(_run(name, "open interest", oi))
    return probes


def probe_coingecko(cfg: dict, api_key: str | None, client=None) -> list[Probe]:
    cg = client or CoinGecko(cfg, api_key=api_key)
    return [
        _run("coingecko", "ping", lambda: cg.ping().get("gecko_says", "")),
        _run("coingecko", "global",
             lambda: f"btc.d {cg.global_data()['market_cap_percentage']['btc']:.2f}%"),
        _run("coingecko", "top universe", lambda: f"{len(cg.top_universe())} coins"),
        _run("coingecko", "categories", lambda: f"{len(cg.categories())} categories"),
    ]


def probe_telegram(cfg: dict, token: str | None) -> list[Probe]:
    if not token:
        return [Probe("telegram", "getMe", False, 0, "TELEGRAM_BOT_TOKEN not set (dry-run mode)")]
    url = f"{cfg['telegram']['api_base']}/bot{token}/getMe"

    def get_me():
        r = requests.get(url, timeout=cfg["telegram"]["timeout_s"])
        r.raise_for_status()
        return "@" + r.json()["result"]["username"]
    return [_run("telegram", "getMe", get_me)]


def check_sources(cfg: dict, secrets, factory=make_exchange, cg_client=None) -> list[Probe]:
    probes: list[Probe] = []
    for name in [cfg["exchange"]["primary"], *cfg["exchange"]["fallbacks"]]:
        probes += probe_exchange(name, cfg, factory)
    probes += probe_coingecko(cfg, secrets.coingecko_api_key, cg_client)
    probes += probe_telegram(cfg, secrets.telegram_bot_token)
    if not cfg["tvdatafeed"]["enabled"]:
        probes.append(Probe("tvdatafeed", "-", False, 0, "disabled in config"))
    return probes


def working_exchanges(probes: list[Probe]) -> list[str]:
    """Exchanges whose perp candles answered, in configured order."""
    return [p.source for p in probes if p.check == "ohlcv 4h BTC perp" and p.ok]


def format_report(probes: list[Probe]) -> str:
    lines = [f"{'source':<14} {'check':<24} {'ok':<4} {'ms':>6}  detail"]
    for p in probes:
        lines.append(f"{p.source:<14} {p.check:<24} {'yes' if p.ok else 'NO':<4} {p.ms:>6}  {p.detail}")
    ok = working_exchanges(probes)
    lines.append("")
    lines.append("working exchanges (perp candles): " + (", ".join(ok) if ok else "none"))
    return "\n".join(lines)


def as_dicts(probes: list[Probe]) -> list[dict]:
    return [asdict(p) for p in probes]
