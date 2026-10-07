"""Rule-based news scoring and coin extraction. The rules live in rules.json, which the
Cloudflare worker (cloudflare/news-worker) bundles too, so both paths score a title the same way."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules.json")
LEVEL_FA = {2: "خیلی مثبت", 1: "مثبت", 0: "خنثی", -1: "منفی", -2: "خیلی منفی"}

# tickers in exchange titles: "(HYPE)", "CTUSDT Perpetual", "AEON/USD", "for QNT crypto"
_PAREN = re.compile(r"\(([A-Z0-9]{2,12})\)")
_PERP = re.compile(r"\b([A-Z0-9]{2,15})USDT\b")
_PAIR = re.compile(r"\b([A-Z0-9]{2,12})/(?:USDT|USDC|USD)\b")
_FOR = re.compile(r"\bfor ([A-Z0-9]{2,12})(?:,| and| crypto|$)")
_DOLLAR = re.compile(r"\$([A-Z][A-Z0-9]{1,11})\b")
_WORD = re.compile(r"\b[A-Z][A-Z0-9]{2,11}\b")


@dataclass(frozen=True)
class Score:
    level: int
    rule: str | None
    fa: str


@lru_cache(maxsize=1)
def load_rules(path: str | None = None) -> dict:
    with open(path or RULES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        "ignore": [re.compile(p, re.I) for p in raw["ignore"]],
        "rules": [{**r, "rx": re.compile(r["pattern"], re.I)} for r in raw["rules"]],
        "stop": {w.upper() for w in raw["coin_stopwords"]},
    }


def classify(title: str, source: str) -> Score:
    """source: 'binance', 'okx', or anything else for news sites ('media' rules)."""
    rules = load_rules()
    if any(rx.search(title) for rx in rules["ignore"]):
        return Score(0, "ignored", "")
    kind = source if source in ("binance", "okx") else "media"
    for r in rules["rules"]:
        if kind in r.get("source", [kind]) and r["rx"].search(title):
            return Score(int(r["level"]), r["id"], r["fa"])
    return Score(0, None, "")


def exchange_coins(title: str) -> list[str]:
    """Tickers named in an exchange announcement title, in order, without duplicates."""
    stop = load_rules()["stop"]
    out: list[str] = []
    for rx in (_PAREN, _PERP, _PAIR, _FOR):
        for m in rx.finditer(title):
            t = m.group(1).upper()
            if t not in stop and t not in out:
                out.append(t)
    return out


def media_coins(title: str, coins: list[tuple[str, str]]) -> list[str]:
    """Coins of the universe a news headline is about. coins: [(ticker, name)].
    A ticker counts when written as $TICK or as an upper-case word that is not a common
    word; a name counts as a whole word (case-insensitive, at least 4 letters)."""
    stop = load_rules()["stop"]
    tickers = {t.upper(): t.upper() for t, _ in coins}
    out: list[str] = []

    def add(t):
        if t not in out:
            out.append(t)

    for m in _DOLLAR.finditer(title):
        if m.group(1) in tickers:
            add(m.group(1))
    for m in _WORD.finditer(title):
        w = m.group(0)
        if w in tickers and w not in stop:
            add(w)
    low = title.lower()
    for t, name in coins:
        n = (name or "").strip().lower()
        if len(n) >= 4 and n.upper() not in stop and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", low):
            add(t.upper())
    return out
