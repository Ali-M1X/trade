"""News as a factor when a position is opened (2026-11-16, Ali: "if there is very positive news,
by importance or by number, take it into account for opening positions; the negative too").

Each coin gets a news score from the headlines of the last `window_h` hours that the rules
classified (level -2..+2, src/agent/news/rules.json):
  weight = |level| x (exchange_weight for an exchange announcement, media_weight for a news
           site) x 0.5 ^ (age / half_life_h)
  score  = sum of sign(level) x weight
So one very strong item (a Binance listing, a delisting) and many smaller items in the same
direction both add up. For a long the score counts as it is, for a short with the sign turned.
  points = clamp(score x points_per_unit, -max_points, +max_points), added to the setup's score
  news_against: score against the side <= -against_score: the setup cannot be a signal (Watch)
Majors (BTC, ETH) are named in most market headlines, so for them only exchange items count.
"""
from __future__ import annotations

H = 3_600_000
DEFAULTS = {"enabled": False, "window_h": 72, "half_life_h": 24, "exchange_weight": 1.5,
            "media_weight": 0.5, "points_per_unit": 3.0, "max_points": 10.0, "against_score": 2.0,
            "majors": ["BTC", "ETH"]}


def params(cfg: dict) -> dict:
    return {**DEFAULTS, **(((cfg.get("technical") or {}).get("news")) or {})}


def _coin(c: str) -> str:
    c = c.upper()
    for q in ("USDT", "USDC", "USD"):
        if c.endswith(q) and len(c) > len(q):
            return c[: -len(q)]
    return c


def coin_news(items: list[dict], now: int, cfg: dict) -> dict[str, dict]:
    """items: rows of repo.get_news (level, rule, kind, coins, ts, seen_at, title).
    Returns base -> {score, n, pos, neg, top: [titles]}."""
    p = params(cfg)
    out: dict[str, dict] = {}
    for n in items:
        lvl = n.get("level") or 0
        if not lvl or n.get("rule") in (None, "ignored"):
            continue
        seen = n.get("seen_at") or now
        ts = min(n.get("ts") or seen, seen)          # a future date in the text means the event date
        age = (now - ts) / H
        if age < 0 or age > p["window_h"]:
            continue
        exchange = n.get("kind") == "exchange"
        w = abs(lvl) * (p["exchange_weight"] if exchange else p["media_weight"]) * 0.5 ** (age / p["half_life_h"])
        for c in {_coin(x) for x in n.get("coins") or []}:
            if c in p["majors"] and not exchange:
                continue
            d = out.setdefault(c, {"score": 0.0, "n": 0, "pos": 0, "neg": 0, "top": []})
            d["score"] += w if lvl > 0 else -w
            d["n"] += 1
            d["pos" if lvl > 0 else "neg"] += 1
            d["top"].append((w, n.get("title") or "", lvl))
    for d in out.values():
        d["score"] = round(d["score"], 3)
        d["top"] = [[t, lvl] for _, t, lvl in sorted(d["top"], key=lambda x: -x[0])[:3]]
    return out


def news_points(info: dict | None, side: int, cfg: dict) -> tuple[float, bool]:
    """(points added to the score, True when strong news is against the side)."""
    p = params(cfg)
    if not p["enabled"] or not info:
        return 0.0, False
    s = info["score"] * side
    pts = max(-p["max_points"], min(p["max_points"], s * p["points_per_unit"]))
    return round(pts, 2), s <= -p["against_score"]
