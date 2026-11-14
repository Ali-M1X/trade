"""Status panel: one static, self-refreshing HTML page built from the live databases.

`trade-agent panel --out DIR` writes DIR/index.html. On a server it runs after every agent
run and a web server serves the folder on a subdomain. It only reads the databases.

Ali (2026-11-14) wants it to work like an exchange account: a simulated $100 account from
`panel.start` (2026-11-06) trading
  - the confirmed signals (A/B, sent to Telegram), from the live databases, and
  - the unconfirmed ones (Watch: scored with a plan but not sent), from the shadow books
    (signals/shadow.py) and, before those existed, from a replay (panel/backfill.json,
    .github/workflows/panel-backfill.yml).
Each trade risks its plan's risk % of the account's balance when it fills; its result in R
after fees, slippage and funding (backtest/costs.py) is booked when it closes.
"""
from __future__ import annotations

import html
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .backtest.costs import exit_fills, trade_costs
from .notify.formatter import fmt_price
from .store.repository import Repository

try:
    from zoneinfo import ZoneInfo
    TEHRAN = ZoneInfo("Asia/Tehran")
except Exception:                                   # no tz database: Iran has no DST since 2022
    TEHRAN = timezone(timedelta(hours=3, minutes=30))

DAY = 86_400_000
CLOSED = ("sl", "breakeven", "tp3")
UNFILLED = ("expired", "cancelled")
STATUS_FA = {
    "pending": "سفارش در انتظار", "active": "باز", "tp1": "باز (TP1 گرفته)", "tp2": "باز (TP2 گرفته)",
    "tp3": "بسته با سود", "sl": "استاپ", "breakeven": "بسته (سر به سر بعد از TP)",
    "expired": "منقضی (بدون ورود)", "cancelled": "لغو (بدون ورود)",
}
GATE_FA = {"regime": "رژیم بازار", "phase": "فاز", "rr": "نسبت سود به ضرر", "confirmation": "تأیید ورود"}
FLAG_FA = {"not_confirmed": "تأیید ورود ندارد", "btc_weak": "BTC ضعیف", "chase": "قیمت دور از سطح",
           "lower_cycle_correcting": "سیکل پایین در اصلاح", "stage_against": "استیج مخالف",
           "counter_trend_needs_A": "خلاف روند (A لازم)", "neutral_regime_needs_A": "رژیم خنثی (A لازم)",
           "grade_b_off": "رده B خاموش", "score_too_high": "امتیاز بیش از حد",
           "btc_extended": "BTC کشیده", "cycle_avoided": "سیکل نامناسب", "level_worn": "سطح فرسوده",
           "weak_level": "سطح ضعیف", "funding_crowded": "فاندینگ شلوغ"}
TASK_FA = {"run-1h": "بررسی سیگنال", "run-15m": "مدیریت پوزیشن‌ها", "run-4h": "قیف و شورت‌لیست",
           "run-daily": "روزانه", "run-weekly": "هفتگی"}
STALE_MIN = 20                                      # live runs every 5 min; older than this is a problem
ACCOUNTS = (("approved", "تأییدشده", "سیگنال‌های A/B که به تلگرام رفتند", "#2f7de1"),
            ("watch", "تأییدنشده (Watch)", "ستاپ‌هایی که امتیاز/شرط کافی نداشتند و ارسال نشدند", "#e07b2f"),
            ("all", "همه با هم", "هر دو نوع در یک حساب", "#7a5af8"))


# ------------------------------------------------------------------ format helpers
def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _t(ms: int | None, fmt: str = "%m-%d %H:%M") -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=TEHRAN).strftime(fmt)


def _ago(ms: int | None, now_ms: int) -> str:
    if not ms:
        return "هرگز"
    m = max(0, (now_ms - ms) // 60_000)
    if m < 60:
        return f"{m} دقیقه پیش"
    if m < 48 * 60:
        return f"{m // 60} ساعت پیش"
    return f"{m // 1440} روز پیش"


def _cls(x: float | None) -> str:
    return "pos" if x and x > 0 else "neg" if x and x < 0 else ""


def _r(x: float | None) -> str:
    if x is None:
        return "—"
    return f'<span dir="ltr" class="{_cls(x)}">{x:+.2f}R</span>'


def _usd(x: float | None, sign: bool = True) -> str:
    if x is None:
        return "—"
    if not sign:
        return f'<span dir="ltr">${x:,.2f}</span>'
    mark = "+" if x > 0.005 else "−" if x < -0.005 else ""
    return f'<span dir="ltr" class="{_cls(x)}">{mark}${abs(x):,.2f}</span>'


def _pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f'<span dir="ltr" class="{_cls(x)}">{x:+.1f}٪</span>'


def _side(side) -> str:
    long = side in ("long", 1, "1")
    return f'<span class="badge {"long" if long else "short"}">{"لانگ" if long else "شورت"}</span>'


def _kind(approved: bool) -> str:
    return ('<span class="badge ok">تأییدشده</span>' if approved
            else '<span class="badge watch">Watch</span>')


def _price(p) -> str:
    return fmt_price(p) if p else "—"


# --------------------------------------------------------------------- data
def _start_ms(cfg: dict) -> int:
    day = str((cfg.get("panel") or {}).get("start") or "2026-11-06")
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _last_price(repo: Repository | None, base: str) -> float | None:
    """Close of the newest stored candle of the coin (5m when it is managed live)."""
    if repo is None:
        return None
    try:
        row = repo.conn.execute(
            "SELECT close FROM candles WHERE symbol LIKE ? ORDER BY ts DESC LIMIT 1",
            (f"{base}/%",)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _open(path) -> Repository | None:
    return Repository.open_readonly(path) if path and Path(path).exists() else None


def _db_trades(repo: Repository, system: str, approved: bool, since: int) -> list[dict]:
    out = []
    for s in repo.get_signals():
        if s["created_at"] < since:
            continue
        p = s["payload"]
        lc = p["lifecycle"]
        events = [(e["kind"], e["ts"], e["payload"].get("price")) for e in repo.get_events(s["id"])
                  if e["kind"] != "created"]
        out.append({
            "system": system, "approved": approved, "source": "live", "base": s["symbol"],
            "side": lc["side"], "grade": s["grade"], "score": s["score"], "created": s["created_at"],
            "filled_at": lc.get("filled_at"), "closed_at": lc.get("closed_at"), "status": s["status"],
            "entry": lc["entry"], "sl": lc["sl"], "sl_now": lc.get("sl_now", lc["sl"]),
            "tp1": lc["tp1"], "tp2": lc["tp2"], "risk_pct": (p.get("plan") or {}).get("risk_pct") or 0.0,
            "gross_r": lc["realized_r"], "open_frac": lc.get("open_frac", 1.0), "events": events,
            "leverage": (p.get("plan") or {}).get("leverage"),
            "why": [FLAG_FA.get(f, f) for f in (p.get("evaluation") or {}).get("flags") or []],
        })
    return out


def _backfill(cfg: dict, since: int, watch_until: dict[str, int | None]) -> tuple[list[dict], dict]:
    """Watch trades of the replay that opened before that system's live shadow book
    started (watch_until). Also returns the replay's meta (with its own confirmed trades,
    for the fidelity check on the method page)."""
    path = (cfg.get("panel") or {}).get("backfill")
    if not path or not Path(path).exists():
        return [], {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out, replay_ok = [], []
    for t in data.get("trades", []):
        name = "سیستم ۱" if t["system"] == "system1" else "سیستم ۲"
        if t["created"] < since:
            continue
        if t["approved"]:
            replay_ok.append(t)
            continue
        until = watch_until.get(name)
        if until and t["created"] >= until:
            continue
        events = [tuple(e) for e in t.get("events") or []]
        left = 1.0 - sum(f for _, _, f in exit_fills({"events": events}, cfg))
        out.append({
            "system": name, "approved": False, "source": "replay", "base": t["base"], "side": t["side"],
            "grade": t["grade"], "score": t["score"], "created": t["created"],
            "filled_at": t["filled_at"], "closed_at": t["closed_at"], "status": t["status"],
            "entry": t["entry"], "sl": t["sl"], "sl_now": t["sl"], "tp1": t.get("tp1"), "tp2": t.get("tp2"),
            "risk_pct": t.get("risk_pct") or 0.0, "gross_r": t["gross_r"], "open_frac": max(0.0, left),
            "events": events, "leverage": None, "why": [],
            "costs": {k: t.get(k) for k in ("fee_r", "slippage_r", "funding_r", "net_r")},
        })
    meta = data.get("meta", {})
    meta["replay_confirmed"] = replay_ok
    return out, meta


def _value(t: dict, cfg: dict, price_repo) -> None:
    """Costs in R (fees, slippage, funding) and, for open trades, the mark price and R now."""
    if t["status"] in CLOSED and t.get("costs", {}).get("net_r") is not None:
        c = t["costs"]
        t["cost_r"] = (c["fee_r"] or 0) + (c["slippage_r"] or 0) + (c["funding_r"] or 0)
        t["net_r"] = c["net_r"]
    else:
        c = trade_costs({**t, "gross_r": t["gross_r"]}, cfg)
        t["cost_r"] = c["fee_r"] + c["slippage_r"] + c["funding_r"]
        t["net_r"] = c["net_r"]
    t["sl_pct"] = abs(t["entry"] - t["sl"]) / t["entry"] * 100 if t["entry"] else None
    t["open"] = t["status"] not in CLOSED + UNFILLED
    t["price"] = t["open_r"] = None
    if t["open"]:
        t["price"] = _last_price(price_repo, t["base"])
        risk = abs(t["entry"] - t["sl"])
        if t["price"] and risk and t["filled_at"]:
            t["open_r"] = t["gross_r"] + t["open_frac"] * t["side"] * (t["price"] - t["entry"]) / risk


def simulate(trades: list[dict], balance: float, start: int, now: int) -> dict:
    """Book every filled trade on one account: risk = its risk % of the balance at the fill,
    result = risk x net R at the close. Returns the account and per-trade rows (copies)."""
    rows = [dict(t) for t in trades]
    steps = []
    for t in rows:
        if t["filled_at"]:
            steps.append((t["filled_at"], 1, t))
            if t["status"] in CLOSED and t["closed_at"]:
                steps.append((t["closed_at"], 0, t))
    bal, peak, mdd = balance, balance, 0.0
    curve = [(start, balance)]
    for ts, kind, t in sorted(steps, key=lambda x: (x[0], x[1])):     # closes before opens
        if kind == 1:
            t["risk_usd"] = bal * t["risk_pct"] / 100
            continue
        t["pnl_usd"] = t["risk_usd"] * t["net_r"]
        t["fee_usd"] = t["risk_usd"] * t["cost_r"]
        bal += t["pnl_usd"]
        t["balance_after"] = bal
        curve.append((ts, bal))
        peak = max(peak, bal)
        mdd = max(mdd, (peak - bal) / peak if peak else 0)
    closed = [t for t in rows if "pnl_usd" in t]
    open_ = [t for t in rows if t["open"] and t["filled_at"]]
    unreal = sum(t["risk_usd"] * t["open_r"] for t in open_ if t.get("open_r") is not None)
    for t in open_:
        if t["sl_pct"]:
            t["size_usd"] = t["risk_usd"] / (t["sl_pct"] / 100)
    wins = [t for t in closed if t["pnl_usd"] > 0]
    gain, loss = sum(t["pnl_usd"] for t in wins), -sum(t["pnl_usd"] for t in closed if t["pnl_usd"] < 0)
    curve.append((now, bal + unreal))
    return {"rows": rows, "balance": bal, "equity": bal + unreal, "unrealized": unreal,
            "start_balance": balance, "closed": closed, "open": open_, "wins": len(wins),
            "pnl": bal - balance, "max_dd": mdd * 100, "curve": curve,
            "pf": gain / loss if loss else None, "fees": sum(t["fee_usd"] for t in closed),
            "pending": [t for t in rows if t["status"] == "pending"],
            "unfilled": [t for t in rows if t["status"] in UNFILLED]}


def collect(cfg: dict, now_ms: int) -> dict:
    start = _start_ms(cfg)
    bal = float((cfg.get("panel") or {}).get("balance_usd") or 100)
    s2, sh = cfg.get("system2") or {}, cfg.get("shadow") or {}
    dbs = [("سیستم ۱", (cfg.get("live") or {}).get("variant") or "base", cfg["storage"]["db_path"],
            sh.get("db_path") if sh.get("enabled") else None)]
    if s2.get("enabled"):
        dbs.append(("سیستم ۲", s2.get("variant") or "", s2["db_path"],
                    sh.get("s2_db_path") if sh.get("enabled") else None))
    price_repo = _open(cfg["storage"]["db_path"])
    out = {"systems": [], "now": now_ms, "start": start, "trades": [], "shadow_since": {}}
    try:
        for name, variant, path, shadow_path in dbs:
            repo = _open(path)
            if repo is None:
                continue
            try:
                out["trades"] += _db_trades(repo, name, True, start)
                news = []
                try:
                    news = [n for n in repo.get_news(now_ms - 14 * DAY) if n["alerted"]]
                except sqlite3.Error:
                    pass
                out["systems"].append({
                    "name": name, "variant": variant,
                    "last_runs": repo.get_state("last_runs", {}) or {},
                    "funnel": repo.get_state("funnel", {}) or {},
                    "funnel_dom": repo.get_state("funnel_dominance", {}) or {},
                    "evaluations": repo.get_state("evaluations", {}) or {},
                    "loss_streak": repo.get_state("loss_streak", {}) or {},
                    "news": news,
                })
            finally:
                repo.close()
            srepo = _open(shadow_path)
            if srepo is not None:
                try:
                    out["trades"] += _db_trades(srepo, name, False, start)
                    out["shadow_since"][name] = srepo.get_state("shadow_since")
                finally:
                    srepo.close()
        backfill, out["replay"] = _backfill(cfg, start, out["shadow_since"])
        out["trades"] += backfill
        for t in out["trades"]:
            _value(t, cfg, price_repo)
    finally:
        if price_repo is not None:
            price_repo.close()
    if len(out["systems"]) > 1:                   # system 2's regime is computed into system 1's db
        first = out["systems"][0]
        for s in out["systems"][1:]:
            s["funnel_dom"] = s["funnel_dom"] or first["funnel_dom"]
            s["funnel"] = s["funnel"] or first["funnel"]
    tr = sorted(out["trades"], key=lambda t: t["created"])
    out["accounts"] = {
        "approved": simulate([t for t in tr if t["approved"]], bal, start, now_ms),
        "watch": simulate([t for t in tr if not t["approved"]], bal, start, now_ms),
        "all": simulate(tr, bal, start, now_ms),
    }
    return out


# ------------------------------------------------------------------- render
def _equity_svg(data: dict) -> str:
    series = [(label, color, data["accounts"][key]["curve"]) for key, label, _, color in ACCOUNTS]
    if not any(len(c) > 2 for _, _, c in series):
        return '<p class="muted">هنوز معامله‌ی بسته‌شده‌ای نیست.</p>'
    w, h, pad, right = 720, 240, 18, 56
    t0, t1 = data["start"], data["now"]
    lo = min(v for _, _, c in series for _, v in c)
    hi = max(v for _, _, c in series for _, v in c)
    base = series[0][2][0][1]
    lo, hi = min(lo, base), max(hi, base)
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.08, hi + span * 0.08
    span = hi - lo

    def x(ts):
        return pad + (ts - t0) / ((t1 - t0) or 1) * (w - pad - right)

    def y(v):
        return pad + (hi - v) / span * (h - 2 * pad)

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="منحنی موجودی">',
             f'<line x1="{pad}" x2="{w - right}" y1="{y(base):.1f}" y2="{y(base):.1f}" class="zero"/>',
             f'<text x="{w - right + 6}" y="{y(base) + 4:.1f}" class="axis">${base:,.0f}</text>']
    for v in (hi - span * 0.08, lo + span * 0.08):
        if abs(y(v) - y(base)) < 14:                # would overlap the start balance label
            continue
        parts.append(f'<text x="{w - right + 6}" y="{y(v) + 4:.1f}" class="axis">${v:,.1f}</text>')
    day = t0
    while day <= t1:
        parts.append(f'<text x="{x(day):.1f}" y="{h - 2}" class="axis" text-anchor="middle">'
                     f'{_t(day, "%m/%d")}</text>')
        day += 2 * DAY
    for _, color, c in series:
        pts, prev = [], None
        for ts, v in c:                          # step line: the balance changes at each close
            if prev is not None:
                pts.append(f"{x(ts):.1f},{y(prev):.1f}")
            pts.append(f"{x(ts):.1f},{y(v):.1f}")
            prev = v
        parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="2.2"/>')
        parts.append(f'<circle cx="{x(c[-1][0]):.1f}" cy="{y(c[-1][1]):.1f}" r="4" fill="{color}"/>')
    parts.append("</svg>")
    legend = " ".join(f'<span class="legend"><i style="background:{c}"></i>{_e(lb)}: ${cv[-1][1]:,.2f}</span>'
                      for lb, c, cv in series)
    return "".join(parts) + f'<div class="legends">{legend}</div>'


def _account_cards(data: dict) -> str:
    out = []
    for key, label, hint, color in ACCOUNTS:
        a = data["accounts"][key]
        n = len(a["closed"])
        ret = a["pnl"] / a["start_balance"] * 100 if a["start_balance"] else 0
        wr = f"{a['wins'] / n * 100:.0f}٪" if n else "—"
        pf = "—" if a["pf"] is None else f"{a['pf']:.2f}"
        out.append(
            f'<div class="acct" style="border-top-color:{color}"><div class="acct-h">{_e(label)}'
            f'<small>{_e(hint)}</small></div>'
            f'<div class="big">{_usd(a["equity"], sign=False)}</div>'
            f'<div class="muted">موجودی بسته‌شده {_usd(a["balance"], sign=False)} · باز {_usd(a["unrealized"])}</div>'
            f'<div class="grid4"><div><span class="l">سود/زیان</span>{_usd(a["pnl"])} {_pct(ret)}</div>'
            f'<div><span class="l">معامله‌ی بسته</span>{n} <small>برد {wr}</small></div>'
            f'<div><span class="l">بیشترین افت</span>{a["max_dd"]:.1f}٪</div>'
            f'<div><span class="l">ضریب سود</span>{pf}</div>'
            f'<div><span class="l">کارمزد پرداختی</span>{_usd(-a["fees"])}</div>'
            f'<div><span class="l">پوزیشن باز</span>{len(a["open"])} <small>+ {len(a["pending"])} سفارش در انتظار</small></div>'
            '</div></div>')
    return '<div class="accts">' + "".join(out) + "</div>"


def _by_system(data: dict) -> str:
    rows = []
    for key, label, _, _ in ACCOUNTS[:2]:
        a = data["accounts"][key]
        for sname in sorted({t["system"] for t in a["rows"]}):
            closed = [t for t in a["closed"] if t["system"] == sname]
            n = len(closed)
            if not n and not any(t["system"] == sname for t in a["rows"]):
                continue
            wins = sum(t["pnl_usd"] > 0 for t in closed)
            rows.append(f"<tr><td>{_e(label)}</td><td>{_e(sname)}</td><td>{n}</td>"
                        f"<td>{f'{wins / n * 100:.0f}٪' if n else '—'}</td>"
                        f"<td>{_r(sum(t['net_r'] for t in closed) if n else None)}</td>"
                        f"<td>{_usd(sum(t['pnl_usd'] for t in closed) if n else None)}</td></tr>")
    if not rows:
        return ""
    return ('<div class="scroll"><table><thead><tr><th>نوع</th><th>سیستم</th><th>بسته</th><th>برد</th>'
            '<th>مجموع R خالص</th><th>سود/زیان</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def _regime(s: dict, now: int) -> str:
    f = (s["funnel_dom"] or s["funnel"]) if s["name"] == "سیستم ۲" else s["funnel"]
    reg, maj = f.get("regime") or {}, f.get("majors") or {}
    bias = {"long": "فقط لانگ", "short": "فقط شورت", "both": "هر دو طرف"}.get(reg.get("bias"), reg.get("bias") or "—")
    bits = [f'رژیم: <b>{_e(reg.get("name", "—"))}</b> ({_e(bias)})']
    if maj:
        bits.append(f'امتیاز BTC: {_e(maj.get("btc"))} | ETH: {_e(maj.get("eth"))} | ETH/BTC: {_e(maj.get("ethbtc"))}')
        if maj.get("btc_weak"):
            bits.append('<span class="warn">BTC ضعیف است (لانگ آلت‌ها امتیاز بالاتر لازم دارد)</span>')
    ls = s["loss_streak"]
    if ls.get("paused_until") and ls["paused_until"] > now:
        bits.append(f'<span class="warn">توقف بعد از ضررهای پشت‌سرهم تا {_t(ls["paused_until"])}</span>')
    return " · ".join(bits)


def _runs(s: dict, now_ms: int) -> str:
    rows = []
    for task, label in TASK_FA.items():
        ts = s["last_runs"].get(task)
        stale = task in ("run-1h", "run-15m") and (not ts or now_ms - ts > STALE_MIN * 60_000)
        rows.append(f'<span class="run {"bad" if stale else ""}">{label}: {_ago(ts, now_ms)}</span>')
    return " ".join(rows)


def _attrs(t: dict) -> str:
    return f'data-k="{"approved" if t["approved"] else "watch"}" data-s="{_e(t["system"])}"'


def _positions(data: dict) -> str:
    acc = {id(t): t for t in data["accounts"]["all"]["open"]}
    rows = []
    for t in sorted(acc.values(), key=lambda t: -t["created"]):
        rows.append(
            f"<tr {_attrs(t)}><td>{_kind(t['approved'])}</td><td>{_e(t['system'])}</td>"
            f"<td><b>{_e(t['base'])}</b>/USDT</td><td>{_side(t['side'])}</td>"
            f"<td>{_e(t['grade'])} <small>{t['score']:.0f}</small></td>"
            f"<td>{_price(t['entry'])}</td><td>{_price(t['price'])}</td>"
            f"<td>{_price(t['sl_now'])}</td><td>{_price(t['tp1'])}</td><td>{_price(t['tp2'])}</td>"
            f"<td>{_usd(t.get('size_usd'), sign=False)}</td><td>{_usd(t['risk_usd'], sign=False)}</td>"
            f"<td>{_usd(t['risk_usd'] * t['open_r'] if t.get('open_r') is not None else None)}</td>"
            f"<td>{_r(t.get('open_r'))}</td><td>{_e(STATUS_FA.get(t['status'], t['status']))}</td>"
            f"<td>{_t(t['filled_at'])}</td></tr>")
    if not rows:
        return '<p class="muted">الان پوزیشن بازی نیست.</p>'
    return ('<p class="muted">اعداد دلاری از حساب «همه با هم» است. ریسک هر پوزیشن درصدی از موجودی همان لحظه است.</p>'
            '<div class="scroll"><table class="f"><thead><tr><th>نوع</th><th>سیستم</th><th>جفت‌ارز</th>'
            '<th>جهت</th><th>رده</th><th>ورود</th><th>قیمت فعلی</th><th>استاپ</th><th>TP1</th><th>TP2</th>'
            '<th>حجم</th><th>ریسک</th><th>سود/زیان باز</th><th>R</th><th>وضعیت</th><th>زمان ورود</th>'
            '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def _pending(data: dict) -> str:
    rows = []
    for t in sorted(data["accounts"]["all"]["pending"], key=lambda t: -t["created"]):
        rows.append(
            f"<tr {_attrs(t)}><td>{_kind(t['approved'])}</td><td>{_e(t['system'])}</td>"
            f"<td><b>{_e(t['base'])}</b></td><td>{_side(t['side'])}</td>"
            f"<td>{_e(t['grade'])} <small>{t['score']:.0f}</small></td><td>{_price(t['entry'])}</td>"
            f"<td>{_price(t['price'])}</td><td>{_price(t['sl'])}</td><td>{_price(t['tp1'])}</td>"
            f"<td>{t['risk_pct']:.2f}٪</td><td>{_t(t['created'])}</td></tr>")
    if not rows:
        return '<p class="muted">سفارش در انتظاری نیست.</p>'
    return ('<div class="scroll"><table class="f"><thead><tr><th>نوع</th><th>سیستم</th><th>کوین</th><th>جهت</th>'
            '<th>رده</th><th>قیمت سفارش</th><th>قیمت فعلی</th><th>استاپ</th><th>TP1</th><th>ریسک</th>'
            '<th>زمان سیگنال</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def _history(data: dict) -> str:
    acc = data["accounts"]
    own = {}
    for key in ("approved", "watch"):
        for t in acc[key]["closed"]:
            own[(t["system"], t["base"], t["created"], t["approved"])] = t
    rows = []
    every = acc["all"]["rows"]
    for t in sorted(every, key=lambda t: -(t["closed_at"] or t["created"])):
        if t["status"] not in CLOSED + UNFILLED:
            continue
        mine = own.get((t["system"], t["base"], t["created"], t["approved"]), {})
        rows.append(
            f"<tr {_attrs(t)}><td>{_t(t['created'])}</td><td>{_t(t['closed_at'])}</td>"
            f"<td>{_kind(t['approved'])}</td><td>{_e(t['system'])}</td><td><b>{_e(t['base'])}</b></td>"
            f"<td>{_side(t['side'])}</td><td>{_e(t['grade'])} <small>{t['score']:.0f}</small></td>"
            f"<td>{_e(STATUS_FA.get(t['status'], t['status']))}</td>"
            f"<td>{_r(t['gross_r'] if t['filled_at'] else None)}</td>"
            f"<td>{_r(t['net_r'] if t['filled_at'] else None)}</td>"
            f"<td>{_usd(t.get('pnl_usd'))}</td><td>{_usd(mine.get('pnl_usd'))}</td>"
            f"<td>{_usd(mine.get('balance_after'), sign=False)}</td>"
            f"<td><small>{'بازسازی' if t['source'] == 'replay' else 'زنده'}</small></td></tr>")
    if not rows:
        return '<p class="muted">هنوز معامله‌ای بسته نشده است.</p>'
    return ('<p class="muted">«سود/زیان (همه)» در حساب مشترک است؛ «در حساب خودش» در حساب تأییدشده یا Watch.</p>'
            '<div class="scroll"><table class="f"><thead><tr><th>سیگنال</th><th>بسته شدن</th><th>نوع</th>'
            '<th>سیستم</th><th>کوین</th><th>جهت</th><th>رده</th><th>نتیجه</th><th>R خام</th><th>R خالص</th>'
            '<th>سود/زیان (همه)</th><th>در حساب خودش</th><th>موجودی بعد</th><th>منبع</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


def _candidates(systems: list[dict], limit: int = 20) -> str:
    items = []
    for s in systems:
        for it in (s["evaluations"].get("items") or []):
            items.append((s["name"], it))
    items.sort(key=lambda x: -(x[1].get("score") or 0))
    rows = []
    for name, it in items[:limit]:
        failed = [GATE_FA.get(g, g) for g, ok in (it.get("gates") or {}).items() if not ok]
        flags = [FLAG_FA.get(f, f) for f in it.get("flags") or []]
        why = "، ".join(dict.fromkeys(failed + flags)) or ("" if it.get("grade") in ("A", "B") else "امتیاز کم")
        plan = it.get("plan") or {}
        rows.append(
            f"<tr><td>{_e(name)}</td><td><b>{_e(it.get('base'))}</b></td><td>{_side(it.get('side'))}</td>"
            f"<td>{(it.get('score') or 0):.0f}</td><td>{_e(it.get('grade') or '—')}</td>"
            f"<td>{_price(it.get('price'))}</td><td>{_price(plan.get('entry'))}</td><td>{_price(plan.get('sl'))}</td>"
            f"<td>{_price(plan.get('tp1'))}</td><td>{_e(why) or '—'}</td></tr>")
    if not rows:
        return '<p class="muted">در آخرین بررسی کاندیدی نبود.</p>'
    ts = max((s["evaluations"].get("ts") or 0) for s in systems)
    return (f'<p class="muted">آخرین بررسی: {_t(ts)} · سیگنال = رده‌ی A یا B بدون مانع؛ Watch = ستاپ نزدیک که فقط دنبال می‌شود</p>'
            '<div class="scroll"><table><thead><tr><th>سیستم</th><th>کوین</th><th>جهت</th><th>امتیاز</th>'
            '<th>رده</th><th>قیمت</th><th>ورود</th><th>استاپ</th><th>TP1</th><th>چرا سیگنال نشد</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


def _watchlists(s: dict) -> str:
    f = s["funnel"]
    short = f.get("shortlist") or []
    watch = f.get("watchlist") or {}
    chips = "".join(f'<span class="chip">{_e(x.get("base"))} {_side(x.get("side"))}</span>' for x in short)
    wl = "".join(f'<span class="chip">{_e(b)} <small>{_e(", ".join(v) if isinstance(v, list) else v)}</small></span>'
                 for b, v in list(watch.items())[:80])
    return (f'<p><b>شورت‌لیست ({len(short)}):</b> {chips or "—"}</p>'
            f'<p><b>واچ‌لیست ({len(watch)}):</b> {wl or "—"}</p>'
            f'<p class="muted">به‌روزرسانی قیف: {_t(int(f["ts"])) if f.get("ts") else "—"}</p>')


def _news(systems: list[dict], limit: int = 25) -> str:
    news = sorted((n for s in systems for n in s["news"]), key=lambda n: -n["seen_at"])[:limit]
    if not news:
        return '<p class="muted">در ۱۴ روز اخیر هشدار خبری نبود.</p>'
    rows = []
    for n in news:
        title = _e(n["title"])
        if (n.get("url") or "").startswith(("http://", "https://")):
            title = f'<a href="{_e(n["url"])}" target="_blank" rel="noopener noreferrer">{title}</a>'
        rows.append(f"<tr><td>{_t(n['seen_at'])}</td><td>{_e(', '.join(n['coins']))}</td>"
                    f"<td>{'⭐' * int(n['level'] or 0)}</td><td>{title}</td><td>{_e(n['source'])}</td></tr>")
    return ('<div class="scroll"><table><thead><tr><th>زمان</th><th>کوین</th><th>اهمیت</th><th>خبر</th>'
            '<th>منبع</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def _method(cfg: dict, data: dict) -> str:
    b = cfg["backtest"]
    since = data["shadow_since"]
    sh = "، ".join(f"{k}: از {_t(v)}" for k, v in since.items() if v) or "هنوز شروع نشده"
    meta = data.get("replay") or {}
    rep = ""
    if meta:
        ok = [t for t in meta.get("replay_confirmed", [])]
        live_ok = [t for t in data["trades"] if t["approved"]]
        rep = (f"<li>بازسازی برای مقایسه {len(ok)} سیگنال تأییدشده پیدا کرد؛ در واقعیت {len(live_ok)} سیگنال "
               f"ارسال شد. هر چه این دو به هم نزدیک‌تر باشند، Watchهای بازسازی‌شده قابل‌اعتمادترند.</li>")
    u = cfg.get("universe") or {}
    return f"""<ul class="method">
<li>حساب‌ها از {_t(data['start'], '%Y-%m-%d')} با ${(cfg.get('panel') or {}).get('balance_usd', 100)} شروع می‌شوند.
هر معامله هنگام ورود، درصد ریسک همان سیگنال را از موجودی آن لحظه ریسک می‌کند (سود مرکب) و نتیجه هنگام بسته شدن ثبت می‌شود.</li>
<li>هزینه‌ها کم می‌شوند: کارمزد {b['taker_fee_pct']}٪ و لغزش {b['slippage_pct']}٪ در هر ورود و خروج، و فاندینگ.</li>
<li><b>تأییدشده:</b> همان سیگنال‌های A/B که واقعاً به تلگرام رفتند، با نتیجه‌ی واقعی‌شان.</li>
<li><b>Watch (تأییدنشده):</b> ستاپ‌هایی که پلن معامله داشتند ولی امتیاز یا شرط کافی نداشتند و ارسال نشدند.
مثل سیگنال B (نصف ریسک) و با همان قوانین ورود، استاپ و تارگت دنبال می‌شوند. ثبت زنده: {sh}.</li>
<li>Watchهای قبل از ثبت زنده با <b>بازسازی</b> روی کندل‌های ساعتی تاریخی ساخته شده‌اند، با تنظیمات فعلی
({u.get('coins_top_n')} کوین، حداقل حجم ${(u.get('min_volume_24h_usd') or 0) / 1e6:.0f}M) برای کل دوره.
سیستم زنده هر ۵ دقیقه و روی کندل‌های در حال شکل‌گیری کار می‌کند؛ پس بازسازی تقریبی است.</li>
{rep}
<li>این حساب واقعی نیست؛ شبیه‌سازی کاغذی است.</li></ul>"""


CSS = """
:root{--bg:#f4f5f7;--card:#fff;--fg:#1b1f24;--muted:#6b7280;--line:#e5e7eb;--pos:#0f9d58;--neg:#d93025;
--warn:#b45309;--long:#e7f6ee;--short:#fdecec;--acc:#2f7de1;--chip:#f1f3f5}
@media (prefers-color-scheme:dark){:root{--bg:#0d1014;--card:#151a20;--fg:#e6e8eb;--muted:#9aa3ad;
--line:#262d36;--pos:#34c77b;--neg:#ff6b6b;--warn:#f0a94b;--long:#11301f;--short:#3a1717;--chip:#1d242c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 Vazirmatn,Tahoma,system-ui,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:2}
.hin{max-width:1280px;margin:auto;padding:10px 16px 0}h1{font-size:18px;margin:0}
nav{display:flex;gap:4px;overflow-x:auto;margin-top:6px}nav button{background:none;border:0;
border-bottom:2px solid transparent;color:var(--muted);padding:8px 12px;font:inherit;cursor:pointer;white-space:nowrap}
nav button.on{color:var(--fg);border-bottom-color:var(--acc);font-weight:600}
main{max-width:1280px;margin:auto;padding:12px 16px}h2{font-size:16px;margin:0 0 10px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0}
.tab{display:none}.tab.on{display:block}.muted{color:var(--muted)}small{color:var(--muted)}
.accts{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.acct{border:1px solid var(--line);border-top:3px solid var(--acc);border-radius:10px;padding:12px;background:var(--card)}
.acct-h{font-weight:600}.acct-h small{display:block;font-weight:400}.big{font-size:26px;font-weight:700;margin:4px 0}
.grid4{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;margin-top:8px}.grid4 .l{display:block;color:var(--muted);font-size:12px}
.pos{color:var(--pos);font-weight:600}.neg{color:var(--neg);font-weight:600}.warn{color:var(--warn)}
.badge{padding:1px 8px;border-radius:10px;font-size:12px;white-space:nowrap}
.badge.long{background:var(--long);color:var(--pos)}.badge.short{background:var(--short);color:var(--neg)}
.badge.ok{background:var(--long);color:var(--pos)}.badge.watch{background:var(--chip);color:var(--warn)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;white-space:nowrap}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right}th{color:var(--muted);font-weight:500;font-size:12px}
td{unicode-bidi:plaintext}.run{display:inline-block;margin:2px 6px 2px 0;padding:2px 8px;
border-radius:6px;border:1px solid var(--line)}.run.bad{border-color:var(--neg);color:var(--neg)}
.chip{display:inline-block;background:var(--chip);border-radius:14px;padding:1px 8px;margin:2px}
.chart{width:100%;height:auto;direction:ltr}.chart .zero{stroke:var(--line);stroke-dasharray:4 4}
.chart .axis{fill:var(--muted);font-size:11px}.legends{margin-top:6px}.legend{margin-left:14px;white-space:nowrap}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-left:4px}a{color:inherit}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}.filters button{border:1px solid var(--line);
background:var(--card);color:var(--fg);border-radius:14px;padding:2px 12px;font:inherit;cursor:pointer}
.filters button.on{background:var(--acc);border-color:var(--acc);color:#fff}
.sys{border-top:1px dashed var(--line);padding-top:8px;margin-top:8px}.sys:first-of-type{border:0;margin:0;padding:0}
.method li{margin:6px 0}
"""

JS = """
(function(){
  var tabs=document.querySelectorAll('nav button'), pages=document.querySelectorAll('.tab');
  function show(id){tabs.forEach(function(b){b.classList.toggle('on',b.dataset.t===id)});
    pages.forEach(function(p){p.classList.toggle('on',p.id===id)});
    try{localStorage.setItem('tab',id)}catch(e){}}
  tabs.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.t)})});
  var saved=null;try{saved=localStorage.getItem('tab')}catch(e){}
  show(saved&&document.getElementById(saved)?saved:'t-home');
  var f={k:'',s:''};
  function apply(){document.querySelectorAll('table.f tbody tr').forEach(function(r){
    r.style.display=((!f.k||r.dataset.k===f.k)&&(!f.s||r.dataset.s===f.s))?'':'none'})}
  document.querySelectorAll('.filters button').forEach(function(b){b.addEventListener('click',function(){
    var g=b.dataset.g; f[g]=b.dataset.v;
    document.querySelectorAll('.filters button[data-g="'+g+'"]').forEach(function(x){
      x.classList.toggle('on',x.dataset.v===f[g])}); apply()})});
})();
"""


def _filters(systems: list[str]) -> str:
    kinds = [("", "همه"), ("approved", "تأییدشده"), ("watch", "Watch")]
    sys_ = [("", "همه‌ی سیستم‌ها")] + [(s, s) for s in systems]
    b = "".join(f'<button data-g="k" data-v="{v}" class="{"on" if not v else ""}">{lb}</button>' for v, lb in kinds)
    b += "".join(f'<button data-g="s" data-v="{_e(v)}" class="{"on" if not v else ""}">{_e(lb)}</button>' for v, lb in sys_)
    return f'<div class="filters">{b}</div>'


def render(cfg: dict, data: dict) -> str:
    now = data["now"]
    systems = data["systems"]
    names = [s["name"] for s in systems]
    head = "".join(f'<div class="sys"><b>{_e(s["name"])}</b> <small>({_e(s["variant"])})</small><br>'
                   f'{_regime(s, now)}</div>' for s in systems)
    runs = _runs(systems[0], now) if systems else "دیتابیسی پیدا نشد."
    n_open = len(data["accounts"]["all"]["open"])
    n_pend = len(data["accounts"]["all"]["pending"])
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="120"><meta name="robots" content="noindex,nofollow">
<title>پنل سیگنال‌ها</title><style>{CSS}</style></head><body>
<header><div class="hin"><h1>پنل سیستم سیگنال <small>· به‌روزرسانی {_t(now)} تهران</small></h1>
<nav><button data-t="t-home">نمای کلی</button><button data-t="t-pos">پوزیشن‌ها ({n_open})</button>
<button data-t="t-hist">تاریخچه</button><button data-t="t-next">سیگنال‌های بعدی و بازار</button>
<button data-t="t-news">اخبار</button><button data-t="t-how">روش محاسبه</button></nav></div></header>
<main>
<div class="tab" id="t-home">
<section><h2>حساب‌های شبیه‌سازی‌شده (شروع {_t(data['start'], '%Y-%m-%d')} با ${(cfg.get('panel') or {}).get('balance_usd', 100)})</h2>{_account_cards(data)}</section>
<section><h2>منحنی موجودی</h2>{_equity_svg(data)}</section>
<section><h2>به تفکیک سیستم</h2>{_by_system(data)}</section>
<section><h2>آخرین اجراها</h2>{runs}</section>
<section><h2>وضعیت بازار</h2>{head}</section>
</div>
<div class="tab" id="t-pos">
<section><h2>پوزیشن‌های باز</h2>{_filters(names)}{_positions(data)}</section>
<section><h2>سفارش‌های در انتظار ({n_pend})</h2>{_pending(data)}</section>
</div>
<div class="tab" id="t-hist"><section><h2>تاریخچه‌ی معاملات</h2>{_filters(names)}{_history(data)}</section></div>
<div class="tab" id="t-next">
<section><h2>وضعیت بازار</h2>{head}</section>
<section><h2>نزدیک‌ترین کاندیدها به سیگنال</h2>{_candidates(systems)}</section>
<section><h2>شورت‌لیست و واچ‌لیست</h2>{_watchlists(systems[0]) if systems else ""}</section>
</div>
<div class="tab" id="t-news"><section><h2>هشدارهای خبری</h2>{_news(systems)}</section></div>
<div class="tab" id="t-how"><section><h2>روش محاسبه</h2>{_method(cfg, data)}</section></div>
</main><script>{JS}</script></body></html>
"""


def build(cfg: dict, out_dir: str | Path, now_ms: int | None = None) -> Path:
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    page = render(cfg, collect(cfg, now_ms))
    tmp = out / ".index.html.tmp"
    tmp.write_text(page, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, out / "index.html")           # the web server never sees a half-written page
    return out / "index.html"
