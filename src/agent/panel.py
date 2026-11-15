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
            "detail": _detail(p),
        })
    return out


EVAL_KEYS = ("sections", "gates", "flags", "confirmations", "notes", "phase_d", "phase_4h", "cycle",
             "labels", "trigger", "trigger_1h", "funding_pct")
PLAN_KEYS = ("support", "support_kind", "support_tf", "support_touches", "tp1_r", "tp2_r", "atr",
             "sl_pct", "leverage", "order", "entry_low", "entry_high")


def _detail(p: dict) -> dict:
    """What the system saw when it opened the trade (shown next to the chart)."""
    ev, plan = p.get("evaluation") or {}, p.get("plan") or {}
    reg, maj = p.get("regime") or {}, p.get("majors") or {}
    return {"eval": {k: ev.get(k) for k in EVAL_KEYS if ev.get(k) is not None},
            "plan": {k: plan.get(k) for k in PLAN_KEYS if plan.get(k) is not None},
            "regime": reg.get("name"), "bias": reg.get("bias"), "sub_system": p.get("system"),
            "majors": {k: maj.get(k) for k in ("btc", "eth", "ethbtc", "btc_weak") if k in maj}}


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
            "events": events, "leverage": None, "why": [], "detail": {},
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


# ------------------------------------------------------------------- page
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


WEB = Path(__file__).with_name("panel_web")
TF_SEC = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
CANDLE_BARS = {"5m": 400, "1h": 600, "4h": 400, "1d": 300}


def _tid(t: dict) -> str:
    kind = "ok" if t["approved"] else "w"
    sysn = "s1" if t["system"] == "سیستم ۱" else "s2"
    return f"{sysn}-{kind}-{t['base']}-{t['created']}"


def _num(x, nd: int = 8):
    if x is None:
        return None
    try:
        return float(f"{float(x):.{nd}g}")
    except (TypeError, ValueError):
        return None


def _trade_rows(data: dict) -> list[dict]:
    """Every trade once, with its dollar result in the shared account and in its own one."""
    acc = data["accounts"]
    own = {}
    for key in ("approved", "watch"):
        for t in acc[key]["rows"]:
            own[_tid(t)] = t
    rows = []
    for t in acc["all"]["rows"]:
        tid = _tid(t)
        mine = own.get(tid, {})
        rows.append({
            "id": tid, "system": t["system"], "approved": t["approved"], "source": t["source"],
            "base": t["base"], "side": 1 if t["side"] in (1, "long", "1") else -1,
            "grade": t["grade"], "score": _num(t["score"], 4), "created": t["created"],
            "filled_at": t["filled_at"], "closed_at": t["closed_at"], "status": t["status"],
            "status_fa": STATUS_FA.get(t["status"], t["status"]), "open": t["open"],
            "entry": _num(t["entry"]), "sl": _num(t["sl"]), "sl_now": _num(t["sl_now"]),
            "tp1": _num(t["tp1"]), "tp2": _num(t["tp2"]), "price": _num(t["price"]),
            "risk_pct": _num(t["risk_pct"], 4), "sl_pct": _num(t["sl_pct"], 4),
            "gross_r": _num(t["gross_r"], 4), "net_r": _num(t["net_r"], 4), "cost_r": _num(t["cost_r"], 4),
            "open_r": _num(t.get("open_r"), 4), "open_frac": _num(t.get("open_frac"), 4),
            "events": [[k, ts, _num(px)] for k, ts, px in t["events"]],
            "why": t["why"], "leverage": t.get("leverage"), "detail": t.get("detail") or {},
            "risk_usd": _num(t.get("risk_usd"), 6), "size_usd": _num(t.get("size_usd"), 6),
            "pnl_usd": _num(t.get("pnl_usd"), 6),
            "own_risk_usd": _num(mine.get("risk_usd"), 6), "own_pnl_usd": _num(mine.get("pnl_usd"), 6),
            "own_balance_after": _num(mine.get("balance_after"), 8),
        })
    return rows


def _account_summary(a: dict) -> dict:
    n = len(a["closed"])
    return {"balance": _num(a["balance"]), "equity": _num(a["equity"]), "unrealized": _num(a["unrealized"]),
            "start": a["start_balance"], "pnl": _num(a["pnl"]), "closed": n, "wins": a["wins"],
            "max_dd": _num(a["max_dd"], 4), "pf": _num(a["pf"], 4), "fees": _num(a["fees"], 6),
            "open": len(a["open"]), "pending": len(a["pending"]),
            "curve": [[ts, _num(v)] for ts, v in a["curve"]]}


def _stats(trades: list[dict]) -> dict:
    """Results in R of a group of closed trades (the comparison tab)."""
    closed = sorted([t for t in trades if t["status"] in CLOSED and t["filled_at"]],
                    key=lambda t: t["closed_at"] or 0)
    rs = [t["net_r"] for t in closed]
    wins = [r for r in rs if r > 0]
    loss = -sum(r for r in rs if r < 0)
    cum, peak, mdd, curve = 0.0, 0.0, 0.0, []
    for t in closed:
        cum += t["net_r"]
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
        curve.append([t["closed_at"], round(cum, 4)])
    held = [(t["closed_at"] - t["filled_at"]) / 3_600_000 for t in closed if t["closed_at"]]
    opened = [t for t in trades if t["status"] not in UNFILLED]
    return {"signals": len(trades), "filled": len([t for t in trades if t["filled_at"]]),
            "unfilled": len([t for t in trades if t["status"] in UNFILLED]),
            "open": len([t for t in opened if t["open"] and t["filled_at"]]), "closed": len(closed),
            "wins": len(wins), "win_rate": len(wins) / len(closed) * 100 if closed else None,
            "net_r": round(sum(rs), 4), "avg_r": round(sum(rs) / len(rs), 4) if rs else None,
            "best": round(max(rs), 4) if rs else None, "worst": round(min(rs), 4) if rs else None,
            "pf": round(sum(wins) / loss, 4) if loss else None, "max_dd_r": round(mdd, 4),
            "avg_hold_h": round(sum(held) / len(held), 1) if held else None,
            "longs": len([t for t in trades if t["side"] in (1, "long")]),
            "shorts": len([t for t in trades if t["side"] not in (1, "long")]),
            "open_r": round(sum(t.get("open_r") or 0 for t in opened if t["open"]), 4),
            "curve": curve}


def _compare(data: dict) -> dict:
    out = {}
    for s in sorted({t["system"] for t in data["trades"]} | {x["name"] for x in data["systems"]}):
        mine = [t for t in data["trades"] if t["system"] == s]
        out[s] = {"approved": _stats([t for t in mine if t["approved"]]),
                  "watch": _stats([t for t in mine if not t["approved"]]),
                  "all": _stats(mine)}
    return out


def _system_view(s: dict, now: int) -> dict:
    f = (s["funnel_dom"] or s["funnel"]) if s["name"] == "سیستم ۲" else s["funnel"]
    reg, maj = f.get("regime") or {}, f.get("majors") or {}
    items = []
    for it in (s["evaluations"].get("items") or []):
        failed = [GATE_FA.get(g, g) for g, ok in (it.get("gates") or {}).items() if not ok]
        flags = [FLAG_FA.get(x, x) for x in it.get("flags") or []]
        why = list(dict.fromkeys(failed + flags))
        if not why and it.get("grade") not in ("A", "B"):
            why = ["امتیاز کم"]
        plan = it.get("plan") or {}
        items.append({
            "base": it.get("base"), "side": it.get("side"), "score": _num(it.get("score"), 4),
            "grade": it.get("grade"), "price": _num(it.get("price")), "why": why,
            "sections": it.get("sections") or {}, "gates": it.get("gates") or {},
            "confirmations": it.get("confirmations") or [], "notes": it.get("notes") or [],
            "phase_d": it.get("phase_d"), "phase_4h": it.get("phase_4h"), "cycle": it.get("cycle"),
            "labels": it.get("labels") or [], "trigger": it.get("trigger"), "rejected": it.get("rejected"),
            "funding_pct": it.get("funding_pct"),
            "plan": {k: _num(plan.get(k)) if isinstance(plan.get(k), (int, float)) else plan.get(k)
                     for k in ("entry", "sl", "tp1", "tp2", "tp1_r", "tp2_r", "support", "support_kind",
                               "support_tf", "order") if plan.get(k) is not None},
        })
    items.sort(key=lambda x: -(x["score"] or 0))
    ls = s["loss_streak"] or {}
    return {
        "name": s["name"], "variant": s["variant"],
        "regime": {k: reg.get(k) for k in ("name", "bias", "risk", "usdt_d", "btc_d", "total2", "trend",
                                           "counter_risk", "min_grade") if k in reg},
        "majors": maj, "last_runs": s["last_runs"], "evaluated_at": s["evaluations"].get("ts"),
        "candidates": items[:40],
        "shortlist": [{"base": x.get("base"), "side": x.get("side"), "score": _num(x.get("score"), 4),
                       "labels": x.get("labels") or [], "parts": x.get("parts") or {},
                       "level_atr": x.get("level_atr"),
                       "pairs": {k: (x.get("pairs") or {}).get(k) for k in ("corr_btc", "beta", "rs")}}
                      for x in f.get("shortlist") or []],
        "watchlist": f.get("watchlist") or {}, "hot_categories": f.get("hot_categories") or [],
        "breakout_watch": f.get("breakout_watch") or {}, "funnel_ts": f.get("ts"),
        "paused_until": ls.get("paused_until") if (ls.get("paused_until") or 0) > now else None,
    }


def _dominance(cfg: dict) -> list:
    repo = _open(cfg["storage"]["db_path"])
    if repo is None:
        return []
    try:
        from .data.dominance import dominance_series, merged_history
        d = dominance_series(merged_history(repo))
    except Exception:
        return []
    finally:
        repo.close()
    if d is None or d.empty:
        return []
    d = d.tail(400)
    return [[int(r.ts), _num(r.usdt_d, 5), _num(r.btc_d, 5), _num(r.total2 / 1e9, 6)]
            for r in d.itertuples()]


def _strategy(cfg: dict) -> str:
    from .panel_strategy import strategy_html
    try:
        return strategy_html(cfg)
    except (Exception, SystemExit) as e:          # a config the page can't read must not stop the panel
        print(f"strategy page skipped: {e}")
        return '<section><h2>استراتژی</h2><div class="empty">توضیح استراتژی از تنظیمات ساخته نشد.</div></section>'


def payload(cfg: dict, data: dict) -> dict:
    now = data["now"]
    systems = [_system_view(s, now) for s in data["systems"]]
    trades = _trade_rows(data)
    news = sorted((n for s in data["systems"] for n in s["news"]), key=lambda n: -n["seen_at"])[:40]
    return {
        "now": now, "start": data["start"], "balance": (cfg.get("panel") or {}).get("balance_usd", 100),
        "accounts": {k: _account_summary(data["accounts"][k]) for k in ("approved", "watch", "all")},
        "trades": trades, "systems": systems, "compare": _compare(data),
        "dominance": _dominance(cfg), "shadow_since": data["shadow_since"],
        "news": [{"ts": n["seen_at"], "coins": n["coins"], "level": n["level"], "title": n["title"],
                  "url": n["url"] if (n.get("url") or "").startswith(("http://", "https://")) else None,
                  "source": n["source"]} for n in news],
        "labels": {"status": STATUS_FA, "gates": GATE_FA, "flags": FLAG_FA, "tasks": TASK_FA},
        "strategy_html": _strategy(cfg),
        "method_html": _method(cfg, data),
        "stale_min": STALE_MIN,
    }


def _candle_symbols(pl: dict) -> dict[str, int]:
    """Coin -> the earliest time its chart should show (trades from before their signal)."""
    want: dict[str, int] = {}
    now = pl["now"]
    for t in pl["trades"]:
        want[t["base"]] = min(want.get(t["base"], now), t["created"])
    for s in pl["systems"]:
        for c in s["candidates"][:25] + s["shortlist"]:
            want.setdefault(c["base"], now)
    for b in ("BTC", "ETH"):
        want.setdefault(b, now)
    return want


def _safe_name(base: str) -> str:
    return "".join(ch for ch in base if ch.isalnum() or ch in "-_") or "x"


def export_candles(cfg: dict, pl: dict, out: Path) -> dict[str, str]:
    """One small JSON per coin (loaded when its chart opens): {tf: [[t_sec, o, h, l, c, v], ...]}."""
    repo = _open(cfg["storage"]["db_path"])
    files: dict[str, str] = {}
    if repo is None:
        return files
    folder = out / "candles"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        for base, since in _candle_symbols(pl).items():
            series = {}
            for tf, bars in CANDLE_BARS.items():
                lo = since - bars // 3 * TF_SEC[tf] * 1000
                try:
                    rows = repo.conn.execute(
                        "SELECT ts, open, high, low, close, volume FROM candles WHERE symbol LIKE ? "
                        "AND timeframe=? AND ts>=? GROUP BY ts ORDER BY ts DESC LIMIT ?",   # one bar per time (spot/swap, exchanges)
                        (f"{base}/USDT%", tf, lo, bars)).fetchall()
                except sqlite3.Error:
                    rows = []
                if rows:
                    series[tf] = [[r[0] // 1000, _num(r[1]), _num(r[2]), _num(r[3]), _num(r[4]), _num(r[5], 6)]
                                  for r in reversed(rows)]
            if series:
                name = _safe_name(base)
                _write(folder / f"{name}.json", json.dumps(series, separators=(",", ":")))
                files[base] = f"candles/{name}.json"
    finally:
        repo.close()
    return files


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)                          # the web server never sees a half-written file


def _json_for_html(obj) -> str:
    """JSON that is safe inside <script>: no </script>, no HTML from coin names or news."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _font_faces(fonts: list[str]) -> str:
    """@font-face rules for the IRANSansWeb files that are present (no 404s when they are not)."""
    out = []
    for weight, stem in ((400, "IRANSansWeb"), (700, "IRANSansWeb_Bold")):
        src = [f'url(assets/{stem}{ext}) format("{fmt}")' for ext, fmt in ((".woff2", "woff2"), (".woff", "woff"))
               if f"{stem}{ext}" in fonts]
        if src:
            out.append('@font-face{font-family:"IRANSansWeb";src:%s;font-weight:%d;font-display:swap}'
                       % (",".join(src), weight))
    return "\n".join(out)


def render(cfg: dict, pl: dict, fonts: list[str] | None = None) -> str:
    page = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8").replace("@@IRANSANS@@", _font_faces(fonts or []))
    return (page.replace("@@CSS@@", css)
            .replace("@@JS@@", (WEB / "app.js").read_text(encoding="utf-8"))
            .replace("@@DATA@@", _json_for_html(pl)))


def _copy_assets(cfg: dict, out: Path) -> list[str]:
    import shutil
    dst = out / "assets"
    dst.mkdir(parents=True, exist_ok=True)
    for f in (WEB / "vendor").iterdir():
        target = dst / f.name
        if not target.exists() or target.stat().st_size != f.stat().st_size:
            shutil.copyfile(f, target)
            os.chmod(target, 0o644)
    # IRANSans is a licensed font: used when its files are put in panel.font_dir (Vazirmatn otherwise)
    fdir = (cfg.get("panel") or {}).get("font_dir")
    if fdir and Path(fdir).is_dir():
        for f in Path(fdir).glob("*.woff*"):
            target = dst / f.name
            if not target.exists() or target.stat().st_size != f.stat().st_size:
                shutil.copyfile(f, target)
                os.chmod(target, 0o644)
    return sorted(f.name for f in dst.glob("IRANSansWeb*.woff*"))


def _market_bases(pl: dict) -> list[str]:
    """Coins whose futures data the panel shows, most important first (the API is asked only
    for a few per build)."""
    out = ["BTC", "ETH"]
    live = [t for t in pl["trades"] if t["open"] or t["status"] == "pending"]
    for t in sorted(live, key=lambda t: -t["created"]):
        out.append(t["base"])
    for s in pl["systems"]:
        for c in s["candidates"][:10]:
            out.append(c["base"])
    return list(dict.fromkeys(out))


def skipped_signals(cfg: dict) -> list[dict]:
    """A/B signals each system refused (full cap, correlation, loss-streak pause, cooldown),
    newest first (runs.note_skip, 2026-11-15)."""
    from .runs import SKIP_KEY
    s2 = cfg.get("system2") or {}
    dbs = [("سیستم ۱", cfg["storage"]["db_path"])] + ([("سیستم ۲", s2["db_path"])] if s2.get("enabled") else [])
    out = []
    for name, path in dbs:
        repo = _open(path)
        if repo is None:
            continue
        try:
            out += [{**s, "system": name} for s in repo.get_state(SKIP_KEY, []) or []]
        except sqlite3.Error:
            pass
        finally:
            repo.close()
    return sorted(out, key=lambda s: -s["last"])


def insights(cfg: dict, pl: dict) -> None:
    """Adds MAE/MFE, BTC beta, system health, taken marks and market data to the payload."""
    from . import panel_insights as pi
    now = pl["now"]
    repo = _open(cfg["storage"]["db_path"])
    try:
        pi.excursions(repo, pl["trades"], now)
        live = sorted({t["base"] for t in pl["trades"] if t["open"] or t["status"] == "pending"})
        pl["beta"] = pi.beta_btc(repo, live, now)
        pl["health"] = pi.health(cfg, repo, now)
        if repo is not None:
            pl["news_view"] = pi.news_view([repo], now)
    except sqlite3.Error as e:                     # the panel still shows everything else
        print(f"panel insights skipped: {e}")
    finally:
        if repo is not None:
            repo.close()
    pl["skips"] = skipped_signals(cfg)
    try:                                            # AI reviewers (2026-11-15): advisory, never blocks the build
        from . import ai_review
        pl["ai"] = ai_review.panel_view(cfg, now)
    except Exception as e:                          # noqa: BLE001
        print(f"panel ai skipped: {e}")
        pl["ai"] = {}
    pl["marks"] = pi.read_marks(cfg)
    pl["market"] = pi.market_data(cfg, _market_bases(pl), now)
    pl["panel_api"] = bool((cfg.get("panel") or {}).get("api"))


def _app_version() -> str:
    """A fingerprint of the page's own code, so open pages reload themselves after an update."""
    import hashlib
    h = hashlib.sha1()
    for name in ("index.html", "app.js", "style.css"):
        h.update((WEB / name).read_bytes())
    return h.hexdigest()[:12]


def build(cfg: dict, out_dir: str | Path, now_ms: int | None = None) -> Path:
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = collect(cfg, now_ms)
    pl = payload(cfg, data)
    insights(cfg, pl)
    fonts = _copy_assets(cfg, out)
    pl["candles"] = export_candles(cfg, pl, out)
    pl["app"] = _app_version()
    _write(out / "data.json", _json_for_html(pl))
    _write(out / "index.html", render(cfg, pl, fonts))
    # polled often: data.json only when "now" changes, the whole page when the panel's code does
    # (written last, so a page that reloads finds the new index.html)
    _write(out / "version.json", json.dumps({"now": pl["now"], "app": pl["app"]}))
    return out / "index.html"
