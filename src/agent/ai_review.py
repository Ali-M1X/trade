"""AI reviewers (2026-11-15, Ali): several reviewers give every signal a score (the chance, 0-100,
that it ends in profit), learn from every closed trade, win or loss, and are themselves graded,
so the panel shows how far each one can be trusted. Advisory only: nothing here changes what the
systems trade or send.

Reviewers:
- base: the win rate so far. Not a reviewer; the yardstick the others must beat.
- knn: the closed trades most like this one (score, grade, sections, regime, majors, funding...).
- logit: a small regularised logistic regression on the same features.
- LLM reviewers (config `ai.llm`: Gemini, GPT through GitHub Models): read the signal, the most
  similar closed trades and the lessons they wrote after earlier trades, answer with a score;
  after each close they write one lesson. They need their API keys and run on GitHub Actions
  (.github/workflows/ai-review.yml, branch `ai`), because these APIs refuse Iranian servers.

Every score is frozen the first time it is given. Scores of trades that were already open or
closed when a reviewer started are "retro": made walk-forward, from only what had closed before
that trade opened, and shown apart from the live ones.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

LOCAL = {"base": ("مبنا (نرخ برد تا امروز)", "آماری"),
         "knn": ("همسایه‌ها (معامله‌های مشابه)", "آماری"),
         "logit": ("رگرسیون لجستیک", "آماری")}
SECTIONS = ("phase", "dow", "levels", "volume", "candles", "cycles", "patterns", "confirmation")
FEATS = (("score", 1 / 100), ("grade", 1), ("approved", 1), ("sys2", 1), ("trend", 1), ("btc", 1 / 10),
         ("eth", 1 / 10), ("phase_d", 1), ("phase_4h", 1), ("confirmed", 1), ("flags", 1 / 3),
         ("funding", 10), ("sl_pct", 1), ("leverage", 1 / 10), ("tp1_r", 1 / 3)) + tuple((s, 1 / 20) for s in SECTIONS)
LIVE_GRACE_MS = 15 * 60_000          # scored this soon after the signal: a live score
REGIME_DIR = {"bull": 1, "bear": -1, "alt_season": 1, "btc_led": 1, "risk_off": -1, "capitulation": -1}
GRADE = {"A": 3, "B": 2, "Watch": 1}


def _num(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def features(t: dict) -> dict:
    """What a reviewer sees of a trade, signed by its side where a direction matters
    (a bearish BTC is good for a short)."""
    side = 1 if t.get("side") in (1, "1", "long") else -1
    d = t.get("detail") or {}
    ev, plan, maj = d.get("eval") or {}, d.get("plan") or {}, d.get("majors") or {}
    bias = d.get("bias")
    trend = 1 if bias == "long" else -1 if bias == "short" else REGIME_DIR.get(d.get("regime") or t.get("regime") or "")
    ph = {"TREND_UP": 1, "TREND_DOWN": -1}
    sec = ev.get("sections") or {}
    gates = ev.get("gates") or {}
    x = {
        "score": _num(t.get("score")), "grade": GRADE.get(t.get("grade")), "approved": 1.0 if t.get("approved") else 0.0,
        "sys2": 1.0 if "۲" in str(t.get("system")) else 0.0,
        "trend": side * trend if trend is not None else None,
        "btc": side * maj["btc"] if _num(maj.get("btc")) is not None else None,
        "eth": side * maj["eth"] if _num(maj.get("eth")) is not None else None,
        "phase_d": side * ph[ev["phase_d"]] if ev.get("phase_d") in ph else (0.0 if ev.get("phase_d") else None),
        "phase_4h": side * ph[ev["phase_4h"]] if ev.get("phase_4h") in ph else (0.0 if ev.get("phase_4h") else None),
        "confirmed": (1.0 if gates.get("confirmation") else 0.0) if "confirmation" in gates else None,
        "flags": float(len(ev.get("flags") or [])) if ev else None,
        "funding": side * ev["funding_pct"] if _num(ev.get("funding_pct")) is not None else None,
        "sl_pct": math.log1p(abs(_num(plan.get("sl_pct")) or _num(t.get("sl_pct")) or 0)) or None,
        "leverage": _num(plan.get("leverage") or t.get("leverage")),
        "tp1_r": _num(plan.get("tp1_r")),
    }
    for s in SECTIONS:
        x[s] = _num(sec.get(s)) if sec else None
    return {k: v for k, v in x.items() if v is not None}


def cases(cfg: dict, now: int) -> list[dict]:
    """Every trade the systems ever opened (approved, Watch, replay), oldest first, with its
    features and, once closed, its result."""
    from . import panel
    c = copy.deepcopy(cfg)
    c.setdefault("panel", {})["start"] = "2020-01-01"     # all history, not only the panel account's
    data = panel.collect(c, now)
    out = []
    for t in sorted(data["trades"], key=lambda t: t["created"]):
        if t["status"] in panel.UNFILLED:
            continue
        done = t["status"] in panel.CLOSED and bool(t["filled_at"]) and t.get("net_r") is not None
        decided = t["closed_at"] if done else None
        tp1 = [ts for k, ts, _ in t.get("events") or [] if k == "tp1"]
        if not done and tp1 and t["filled_at"] and (t.get("net_r") or 0) > 0:
            # past TP1 the stop sits at entry with profit already taken: a win whatever comes next
            done, decided = True, min(tp1)
        out.append({"id": panel._tid(t), "created": t["created"], "filled_at": t["filled_at"],
                    "closed_at": decided, "done": done, "open": bool(t.get("open")),
                    "r": round(t["net_r"], 4) if done else None, "win": bool(done and t["net_r"] > 0),
                    "x": features(t),
                    "info": {"base": t["base"], "side": 1 if t["side"] in (1, "long", "1") else -1,
                             "system": t["system"], "approved": t["approved"], "grade": t["grade"],
                             "score": round(t["score"] or 0, 1), "status": t["status"],
                             "regime": (t.get("detail") or {}).get("regime") or t.get("regime"),
                             "flags": ((t.get("detail") or {}).get("eval") or {}).get("flags") or [],
                             "sections": ((t.get("detail") or {}).get("eval") or {}).get("sections") or {}}})
    return out


# ------------------------------------------------------------------ local learners
class Model:
    """Trained on the trades closed before a moment; predicts P(win)."""

    def __init__(self, train: list[dict]):
        self.train = train
        self.n = len(train)
        self.wins = sum(1 for c in train if c["win"])
        self.base = (self.wins + 1) / (self.n + 2)          # Laplace: 50% with no data
        self.keys = [k for k, _ in FEATS]
        self.scale = dict(FEATS)
        cols = {k: [c["x"][k] * self.scale[k] for c in train if k in c["x"]] for k in self.keys}
        self.mean = {k: (sum(v) / len(v) if v else 0.0) for k, v in cols.items()}
        self.sd = {k: (max(1e-3, (sum((a - self.mean[k]) ** 2 for a in v) / len(v)) ** 0.5) if len(v) > 1 else 1.0)
                   for k, v in cols.items()}
        self.X = [self.vec(c["x"]) for c in train]
        self.y = [1.0 if c["win"] else 0.0 for c in train]
        self.w = None

    def vec(self, x: dict) -> list[float]:
        """Standardised; a missing feature sits at the mean (0)."""
        return [((x[k] * self.scale[k] - self.mean[k]) / self.sd[k]) if k in x else 0.0 for k in self.keys]

    def knn(self, x: dict, k: int = 7) -> float:
        if self.n < 3:
            return self.base
        v = self.vec(x)
        d = sorted((math.dist(v, xi), yi) for xi, yi in zip(self.X, self.y))[:k]
        w = [1 / (dd + 0.5) for dd, _ in d]
        # shrunk toward the base rate: a handful of neighbours is weak evidence
        return (sum(wi * yi for wi, (_, yi) in zip(w, d)) + 2 * self.base) / (sum(w) + 2)

    def logit(self, x: dict, lam: float = 0.15) -> float:
        if self.n < 8 or self.wins in (0, self.n):
            return self.base
        if self.w is None:
            import numpy as np
            X = np.array([xi + [1.0] for xi in self.X])
            y = np.array(self.y)
            w = np.zeros(X.shape[1])
            w[-1] = math.log(self.base / (1 - self.base))
            for _ in range(400):                         # gradient descent, L2 on all but the bias
                p = 1 / (1 + np.exp(-X @ w))
                g = X.T @ (p - y) / len(y)
                g[:-1] += lam * w[:-1]                   # strong: a few dozen trades, ~20 features
                w -= 0.3 * g
            self.w = w
        z = sum(a * b for a, b in zip(self.vec(x) + [1.0], self.w))
        return 1 / (1 + math.exp(-max(-30, min(30, z))))

    def similar(self, x: dict, k: int = 6) -> list[dict]:
        v = self.vec(x)
        return [c for _, c in sorted(((math.dist(v, xi), c) for xi, c in zip(self.X, self.train)),
                                     key=lambda p: p[0])[:k]]


def known_at(all_cases: list[dict], ts: int) -> list[dict]:
    return [c for c in all_cases if c["done"] and c["closed_at"] and c["closed_at"] < ts]


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def local_path(cfg: dict) -> Path:
    return Path(cfg["storage"]["db_path"]).parent / "ai_local.json"


def llm_path(cfg: dict) -> Path:
    return Path(cfg["storage"]["db_path"]).parent / "ai_llm.json"


def score_local(cfg: dict, all_cases: list[dict], now: int) -> dict:
    """Give every case not yet scored a frozen score from each local reviewer."""
    path = local_path(cfg)
    store = _load(path) or {}
    preds = store.setdefault("preds", {})
    store.setdefault("since", now)
    models: dict[int, Model] = {}
    changed = False
    for c in all_cases:
        have = preds.setdefault(c["id"], {})
        todo = [r for r in LOCAL if r not in have]
        if not todo:
            continue
        live = now - c["created"] <= LIVE_GRACE_MS
        at = now if live else c["created"]               # retro: only what had closed by then
        train = known_at(all_cases, at)
        key = len(train) if not live else -1
        m = models.get(key) or Model(train)
        models[key] = m
        fn = {"base": lambda x: m.base, "knn": m.knn, "logit": m.logit}
        for r in todo:
            have[r] = [now if live else c["created"], round(fn[r](c["x"]) * 100, 1), 0 if live else 1]
        changed = True
    if changed:
        _save(path, store)
    return store


# ------------------------------------------------------------------ LLM reviewers
def _chat(rv: dict, messages: list[dict], post=None) -> str | None:
    key = os.environ.get(rv.get("key_env") or "")
    if not key:
        return None
    import requests
    post = post or requests.post
    body = {"model": rv["model"], "messages": messages, "temperature": 0.2}
    try:
        r = post(rv["url"].rstrip("/") + "/chat/completions", json=body, timeout=60,
                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    except Exception as e:                             # noqa: BLE001
        log.warning("%s: %s", rv["key"], e)
        return None
    if r.status_code != 200:
        log.warning("%s: HTTP %s %s", rv["key"], r.status_code, r.text[:200])
        return None
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _json_from(text: str | None) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def _brief(c: dict) -> dict:
    i = c["info"]
    out = {"coin": i["base"], "side": "long" if i["side"] > 0 else "short", "system": i["system"],
           "sent_to_telegram": i["approved"], "grade": i["grade"], "score": i["score"], "regime": i["regime"],
           "flags": i["flags"], "sections": {k: round(v, 1) for k, v in (i["sections"] or {}).items()},
           "features_signed_by_side": {k: round(v, 3) for k, v in c["x"].items()}}
    return out


SYSTEM_PROMPT = (
    "You review signals of a rules-based crypto futures system (paper trading). Estimate the probability, "
    "0-100, that this trade ends with a net profit (a stop at entry after TP1 is still a profit). Most of "
    "this system's trades have lost money, so be calibrated: stay near the base rate unless the evidence "
    "is clear. Features named *_signed_by_side are positive when they favour the trade's direction "
    "(trend, btc, eth = BTC/ETH strength scores, phase_d/phase_4h = trend phase, funding). "
    'Reply with JSON only: {"p_win": <0-100>, "reason": "<one short sentence in Persian>"}')
LESSON_PROMPT = (
    "You are reviewing a finished trade of a rules-based crypto futures system to learn from it. Write ONE "
    "general lesson, at most 30 words, in Persian, that would help you score future signals better (a "
    "pattern, not a restatement of this result). "
    'Reply with JSON only: {"lesson": "..."}')


def _predict_llm(rv: dict, c: dict, train: list[dict], lessons: list[str], post=None):
    m = Model(train)
    sim = [{"trade": _brief(s), "net_r": s["r"], "profit": s["win"]} for s in m.similar(c["x"])] if train else []
    user = {"base_rate_pct": round(m.base * 100), "closed_trades_known": len(train), "signal": _brief(c),
            "most_similar_closed_trades": sim, "your_lessons_so_far": lessons[-30:]}
    js = _json_from(_chat(rv, [{"role": "system", "content": SYSTEM_PROMPT},
                               {"role": "user", "content": json.dumps(user, ensure_ascii=False)}], post))
    p = _num((js or {}).get("p_win"))
    if p is None:
        return None
    return max(0.0, min(100.0, p)), str((js or {}).get("reason") or "")[:200]


def _lesson_llm(rv: dict, c: dict, pred, post=None) -> str | None:
    user = {"trade": _brief(c), "net_r": c["r"], "profit": c["win"],
            "your_score_before": pred[1] if pred else None}
    js = _json_from(_chat(rv, [{"role": "system", "content": LESSON_PROMPT},
                               {"role": "user", "content": json.dumps(user, ensure_ascii=False)}], post))
    les = str((js or {}).get("lesson") or "").strip()
    return les[:300] or None


def run_llm(cfg: dict, all_cases: list[dict], now: int, post=None) -> dict:
    """One pass of every LLM reviewer: lessons from newly closed trades, then scores for
    trades it has not scored (open ones first, then retro), within its call budget."""
    path = llm_path(cfg)
    store = _load(path) or {}
    preds, lessons, done_ids = store.setdefault("preds", {}), store.setdefault("lessons", {}), store.setdefault("post", {})
    store.setdefault("errors", {})
    for rv in (cfg.get("ai") or {}).get("llm") or []:
        k = rv["key"]
        if not os.environ.get(rv.get("key_env") or ""):
            store["errors"][k] = "no key"
            continue
        budget = int(rv.get("budget", 6))
        mine, post_done = lessons.setdefault(k, []), done_ids.setdefault(k, [])
        failures = 0
        for c in [c for c in all_cases if c["done"] and c["id"] not in post_done and (preds.get(c["id"]) or {}).get(k)]:
            if budget <= 0 or failures >= 2:
                break
            budget -= 1
            text = _lesson_llm(rv, c, preds[c["id"]][k], post)
            if text is None:
                failures += 1
                continue
            mine.append([now, c["id"], text])
            post_done.append(c["id"])
        del mine[:-60]
        todo = sorted([c for c in all_cases if k not in (preds.get(c["id"]) or {})],
                      key=lambda c: (c["done"], -c["created"]))     # open first, newest first
        for c in todo:
            if budget <= 0 or failures >= 2:
                break
            live = not c["done"] and now - c["created"] <= 3 * 3_600_000
            at = now if live else c["created"]
            train = known_at(all_cases, at)
            usable = [x[2] for x in mine if x[0] < at] if live else []     # retro: no lessons from its future
            budget -= 1
            res = _predict_llm(rv, c, train, usable, post)
            if res is None:
                failures += 1
                continue
            preds.setdefault(c["id"], {})[k] = [now if live else c["created"], round(res[0], 1), 0 if live else 1, res[1]]
        store["errors"][k] = "API failed" if failures >= 2 else ""
        store.setdefault("ran", {})[k] = now
    store["ts"] = now
    _save(path, store)
    return store


def pull_llm(cfg: dict, every_s: float = 300) -> None:
    """The LLM reviewers' file from the `ai` branch (written by GitHub Actions), every few minutes."""
    ai = cfg.get("ai") or {}
    branch, path = ai.get("llm_branch"), llm_path(cfg)
    if not branch or not ai.get("llm"):
        return
    try:
        if time.time() - path.stat().st_mtime < every_s:
            return
    except OSError:
        pass
    try:
        remote = cfg["storage"].get("git_remote", "origin")
        subprocess.run(["git", "fetch", "--depth=1", remote, branch], check=True, capture_output=True, timeout=30)
        data = subprocess.run(["git", "show", "FETCH_HEAD:ai_llm.json"], check=True, capture_output=True, timeout=10).stdout
        json.loads(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        log.info("ai branch not read: %s", e)
        try:
            path.touch()                                   # retried after every_s, not every build
        except OSError:
            pass


# ------------------------------------------------------------------ grading
def grade(rows: list[tuple[float, bool, float]], base_rows: list[tuple[float, bool, float]]) -> dict:
    """rows: (p 0-1, win, net R) of resolved scores; base_rows: the base rate's for the same trades."""
    n = len(rows)
    if not n:
        return {"n": 0, "trust": 0, "verdict": "few", "calib": []}
    brier = sum((p - w) ** 2 for p, w, _ in rows) / n
    bb = sum((p - w) ** 2 for p, w, _ in base_rows) / len(base_rows) if base_rows else None
    hit = sum(1 for p, w, _ in rows if (p >= 0.5) == w) / n
    hi = [r for p, _, r in rows if p >= 0.6]
    lo = [r for p, _, r in rows if p <= 0.4]
    ps, rs = [p for p, _, _ in rows], [r for _, _, r in rows]
    corr = None
    if n >= 5:
        mp, mr = sum(ps) / n, sum(rs) / n
        sp = sum((p - mp) ** 2 for p in ps) ** 0.5
        sr = sum((r - mr) ** 2 for r in rs) ** 0.5
        corr = sum((p - mp) * (r - mr) for p, r in zip(ps, rs)) / (sp * sr) if sp > 1e-9 and sr > 1e-9 else None
    wins = [p for p, w, _ in rows if w]
    losses = [p for p, w, _ in rows if not w]
    auc = None
    if wins and losses:
        auc = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in wins for b in losses) / (len(wins) * len(losses))
    calib = []
    for lo_, hi_ in ((0, 0.4), (0.4, 0.6), (0.6, 1.01)):
        b = [(p, w) for p, w, _ in rows if lo_ <= p < hi_]
        if b:
            calib.append([lo_, min(hi_, 1), len(b), round(sum(p for p, _ in b) / len(b) * 100, 1),
                          round(sum(1 for _, w in b if w) / len(b) * 100, 1)])
    bss = (1 - brier / bb) if bb else None
    # is it really better than the base rate, or luck? paired t of the per-trade Brier gains
    tstat = None
    if base_rows and n >= 5:
        d = [(b - w) ** 2 - (p - w) ** 2 for (p, w, _), (b, _, _) in zip(rows, base_rows)]
        md = sum(d) / n
        sd = (sum((x - md) ** 2 for x in d) / (n - 1)) ** 0.5
        tstat = md / (sd / n ** 0.5) if sd > 1e-12 else (99.0 if md > 0 else -99.0 if md < 0 else 0.0)
    trust = 0 if bss is None else round(max(0.0, min(1.0, bss / 0.25)) * min(1.0, n / 60)
                                        * (min(1.0, max(0.0, tstat) / 2) if tstat is not None else 0) * 100)
    return {"n": n, "brier": round(brier, 4), "brier_base": round(bb, 4) if bb is not None else None,
            "bss": round(bss, 3) if bss is not None else None, "hit": round(hit * 100, 1),
            "auc": round(auc, 3) if auc is not None else None, "corr": round(corr, 3) if corr is not None else None,
            "hi_n": len(hi), "hi_r": round(sum(hi) / len(hi), 3) if hi else None,
            "lo_n": len(lo), "lo_r": round(sum(lo) / len(lo), 3) if lo else None,
            "calib": calib, "trust": trust, "t": round(tstat, 2) if tstat is not None else None,
            "verdict": _verdict(n, bss, tstat)}


def _verdict(n: int, bss, tstat) -> str:
    if n < 20 or bss is None:
        return "few"
    if bss >= 0.05:
        return "good" if (tstat or 0) >= 2 else "maybe"
    return "bad" if bss <= -0.05 and (tstat or 0) <= -1 else "same"


def view(cfg: dict, all_cases: list[dict], now: int) -> dict:
    """The panel's AI tab."""
    loc, llm = _load(local_path(cfg)), _load(llm_path(cfg))
    names = {k: v for k, v in LOCAL.items()}
    for rv in (cfg.get("ai") or {}).get("llm") or []:
        names[rv["key"]] = (rv.get("name") or rv["key"], "مدل زبانی")
    preds: dict[str, dict] = {}
    for src in (loc.get("preds") or {}, llm.get("preds") or {}):
        for cid, by in src.items():
            preds.setdefault(cid, {}).update(by)
    byid = {c["id"]: c for c in all_cases}
    done = [c for c in all_cases if c["done"]]
    out_rev = []
    for k, (name, kind) in names.items():
        def rows(live_only):
            res, base = [], []
            for c in done:
                p = (preds.get(c["id"]) or {}).get(k)
                if not p or p[0] >= (c["closed_at"] or 0) or (live_only and p[2]):
                    continue
                res.append((p[1] / 100, c["win"], c["r"]))
                b = (preds.get(c["id"]) or {}).get("base")
                base.append(((b[1] if b else 50) / 100, c["win"], c["r"]))
            return res, base
        allr, allb = rows(False)
        liver, liveb = rows(True)
        g = grade(allr, allb)
        g["live"] = grade(liver, liveb)
        out_rev.append({"key": k, "name": name, "kind": kind, **g,
                        "scored": sum(1 for c in all_cases if k in (preds.get(c["id"]) or {})),
                        "ran": (llm.get("ran") or {}).get(k), "error": (llm.get("errors") or {}).get(k, "")})
    trust = {r["key"]: r["trust"] for r in out_rev}
    open_rows, recent = [], []
    for c in all_cases:
        by = preds.get(c["id"]) or {}
        sc = {k: v[1] for k, v in by.items()}
        why = {k: v[3] for k, v in by.items() if len(v) > 3 and v[3]}
        voters = [k for k in sc if k != "base"]
        cons = None
        if voters:
            w = [max(0.1, trust.get(k, 0) / 100) for k in voters]
            cons = round(sum(sc[k] * wi for k, wi in zip(voters, w)) / sum(w), 1)
        row = {"id": c["id"], **c["info"], "created": c["created"], "scores": sc, "why": why, "consensus": cons}
        if not c["done"]:
            open_rows.append(row)
        else:
            recent.append({**row, "r": c["r"], "win": c["win"], "closed_at": c["closed_at"]})
    recent.sort(key=lambda r: -(r["closed_at"] or 0))
    return {"reviewers": out_rev, "open": sorted(open_rows, key=lambda r: -r["created"]), "recent": recent[:40],
            "lessons": {k: v[-12:][::-1] for k, v in (llm.get("lessons") or {}).items()},
            "n_cases": len(all_cases), "n_done": len(done),
            "base_rate": round(sum(1 for c in done if c["win"]) / len(done) * 100, 1) if done else None,
            "local_since": loc.get("since"), "llm_ts": llm.get("ts"), "llm_branch": (cfg.get("ai") or {}).get("llm_branch")}


def panel_view(cfg: dict, now: int) -> dict:
    """Score new trades with the local reviewers and build the panel view (every panel build)."""
    if not (cfg.get("ai") or {}).get("enabled"):
        return {}
    all_cases = cases(cfg, now)
    score_local(cfg, all_cases, now)
    return view(cfg, all_cases, now)
