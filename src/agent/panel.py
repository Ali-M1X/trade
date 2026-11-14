"""Status panel: one static, self-refreshing HTML page built from the live databases.

`trade-agent panel --out DIR` writes DIR/index.html. On a server it runs after every agent
run and a web server (nginx) serves the folder on a subdomain. It only reads the databases.
"""
from __future__ import annotations

import html
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .notify.formatter import fmt_price, performance_stats
from .store.repository import Repository

try:
    from zoneinfo import ZoneInfo
    TEHRAN = ZoneInfo("Asia/Tehran")
except Exception:                                   # no tz database: Iran has no DST since 2022
    TEHRAN = timezone(timedelta(hours=3, minutes=30))

CLOSED = ("sl", "breakeven", "tp3")
UNFILLED = ("expired", "cancelled")
STATUS_FA = {
    "pending": "منتظر ورود", "active": "فعال", "tp1": "TP1 گرفته", "tp2": "TP2 گرفته",
    "tp3": "بسته با سود", "sl": "استاپ", "breakeven": "بسته (سر به سر بعد از TP)",
    "expired": "منقضی (بدون ورود)", "cancelled": "لغو (بدون ورود)",
}
GATE_FA = {"regime": "رژیم بازار", "phase": "فاز", "rr": "نسبت سود به ضرر", "confirmation": "تأیید ورود"}
TASK_FA = {"run-1h": "بررسی سیگنال (L6)", "run-15m": "مدیریت پوزیشن‌ها", "run-4h": "قیف و شورت‌لیست",
           "run-daily": "روزانه", "run-weekly": "هفتگی"}
STALE_MIN = 20                                      # live runs every 5 min; older than this is a problem


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _t(ms: int | None) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=TEHRAN).strftime("%m-%d %H:%M")


def _ago(ms: int | None, now_ms: int) -> str:
    if not ms:
        return "هرگز"
    m = max(0, (now_ms - ms) // 60_000)
    if m < 60:
        return f"{m} دقیقه پیش"
    if m < 48 * 60:
        return f"{m // 60} ساعت پیش"
    return f"{m // 1440} روز پیش"


def _r(x: float | None) -> str:
    if x is None:
        return "—"
    cls = "pos" if x > 0 else "neg" if x < 0 else ""
    return f'<span class="{cls}">{x:+.2f}R</span>'


def _side(side) -> str:
    long = side in ("long", 1, "1")
    return f'<span class="badge {"long" if long else "short"}">{"لانگ" if long else "شورت"}</span>'


def _last_price(repo: Repository, base: str) -> float | None:
    """Close of the newest stored candle of the coin (5m when managed in real time)."""
    try:
        row = repo.conn.execute(
            "SELECT close FROM candles WHERE symbol LIKE ? ORDER BY ts DESC LIMIT 1",
            (f"{base}/%",)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _open_r(lc: dict, price: float | None) -> float | None:
    if price is None or not lc.get("risk") or lc.get("status") == "pending":
        return None
    return lc["realized_r"] + lc["open_frac"] * lc["side"] * (price - lc["entry"]) / lc["risk"]


def collect(cfg: dict, now_ms: int) -> dict:
    systems = [("سیستم ۱", (cfg.get("live") or {}).get("variant") or "base", cfg["storage"]["db_path"])]
    s2 = cfg.get("system2") or {}
    if s2.get("enabled"):
        systems.append(("سیستم ۲", s2.get("variant") or "", s2["db_path"]))
    out = {"systems": [], "now": now_ms}
    for name, variant, path in systems:
        if not Path(path).exists():
            continue
        repo = Repository.open_readonly(path)
        try:
            sigs = repo.get_signals()
            for s in sigs:
                lc = s["payload"]["lifecycle"]
                s["system"] = name
                if s["status"] not in CLOSED + UNFILLED:
                    s["price"] = _last_price(repo, s["symbol"])
                    s["open_r"] = _open_r(lc, s["price"])
            news = []
            try:
                news = [n for n in repo.get_news(now_ms - 14 * 86_400_000) if n["alerted"]]
            except sqlite3.Error:
                pass
            out["systems"].append({
                "name": name, "variant": variant, "signals": sigs,
                "stats": performance_stats(sigs),
                "week": performance_stats(sigs, now_ms - 7 * 86_400_000),
                "last_runs": repo.get_state("last_runs", {}) or {},
                "funnel": repo.get_state("funnel", {}) or {},
                "funnel_dom": repo.get_state("funnel_dominance", {}) or {},
                "evaluations": repo.get_state("evaluations", {}) or {},
                "loss_streak": repo.get_state("loss_streak", {}) or {},
                "news": news,
            })
        finally:
            repo.close()
    if len(out["systems"]) > 1:                   # system 2's regime is computed into system 1's db
        first = out["systems"][0]
        for s in out["systems"][1:]:
            s["funnel_dom"] = s["funnel_dom"] or first["funnel_dom"]
            s["funnel"] = s["funnel"] or first["funnel"]
    return out


def _equity_svg(systems: list[dict]) -> str:
    colors = ["#2f7de1", "#e07b2f"]
    series = []
    for i, s in enumerate(systems):
        pts = sorted((x["payload"]["lifecycle"].get("closed_at") or x["updated_at"],
                      x["payload"]["lifecycle"]["realized_r"])
                     for x in s["signals"] if x["status"] in CLOSED)
        cum, acc = [0.0], 0.0
        for _, r in pts:
            acc += r
            cum.append(acc)
        series.append((s["name"], colors[i % 2], cum))
    if not any(len(c) > 1 for _, _, c in series):
        return '<p class="muted">هنوز معامله‌ی بسته‌شده‌ای نیست.</p>'
    w, h, pad = 640, 200, 28
    n = max(len(c) for _, _, c in series) - 1 or 1
    lo = min(min(c) for _, _, c in series)
    hi = max(max(c) for _, _, c in series)
    lo, hi = min(lo, 0), max(hi, 0)
    span = (hi - lo) or 1

    def y(v):
        return pad + (hi - v) / span * (h - 2 * pad)

    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="مجموع R">',
             f'<line x1="{pad}" x2="{w - pad}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="zero"/>',
             f'<text x="{w - pad + 4}" y="{y(0) + 4:.1f}" class="axis">0</text>',
             f'<text x="{w - pad + 4}" y="{y(hi) + 4:.1f}" class="axis">{hi:+.0f}</text>',
             f'<text x="{w - pad + 4}" y="{y(lo) + 4:.1f}" class="axis">{lo:+.0f}</text>']
    for name, color, cum in series:
        if len(cum) < 2:
            continue
        xs = [pad + i / n * (w - 2 * pad) for i in range(len(cum))]
        pts = " ".join(f"{a:.1f},{y(b):.1f}" for a, b in zip(xs, cum))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        parts.append(f'<circle cx="{xs[-1]:.1f}" cy="{y(cum[-1]):.1f}" r="4" fill="{color}"/>')
    parts.append("</svg>")
    legend = " ".join(f'<span class="legend"><i style="background:{c}"></i>{_e(nm)}: {cum[-1]:+.1f}R</span>'
                      for nm, c, cum in series)
    return "".join(parts) + f'<div class="legends">{legend}</div>'


def _kpis(s: dict) -> str:
    st, wk = s["stats"], s["week"]
    wr = f"{st['wins'] / st['n'] * 100:.0f}٪" if st["n"] else "—"
    avg = st["r"] / st["n"] if st["n"] else None
    cells = [("معامله‌ی بسته", st["n"]), ("نرخ برد", wr), ("مجموع R", _r(st["r"] if st["n"] else None)),
             ("میانگین هر معامله", _r(avg)), ("۷ روز اخیر", _r(wk["r"]) + f' <small>({wk["n"]})</small>'),
             ("باز", st["open"]), ("بدون ورود", st["unfilled"])]
    return "".join(f'<div class="kpi"><div class="k">{k}</div><div class="v">{v}</div></div>' for k, v in cells)


def _regime(s: dict) -> str:
    f = (s["funnel_dom"] or s["funnel"]) if s["name"] == "سیستم ۲" else s["funnel"]
    reg, maj = f.get("regime") or {}, f.get("majors") or {}
    bias = {"long": "فقط لانگ", "short": "فقط شورت", "both": "هر دو طرف"}.get(reg.get("bias"), reg.get("bias") or "—")
    bits = [f'رژیم: <b>{_e(reg.get("name", "—"))}</b> ({_e(bias)})']
    if maj:
        bits.append(f'امتیاز BTC: {_e(maj.get("btc"))} | ETH: {_e(maj.get("eth"))} | ETH/BTC: {_e(maj.get("ethbtc"))}')
        if maj.get("btc_weak"):
            bits.append('<span class="warn">BTC ضعیف است (لانگ آلت‌ها امتیاز بالاتر لازم دارد)</span>')
    ls = s["loss_streak"]
    if ls.get("paused_until") and ls["paused_until"] > s.get("_now", 0):
        bits.append(f'<span class="warn">توقف بعد از ضررهای پشت‌سرهم تا {_t(ls["paused_until"])}</span>')
    return " · ".join(bits)


def _runs(s: dict, now_ms: int) -> str:
    rows = []
    for task, label in TASK_FA.items():
        ts = s["last_runs"].get(task)
        stale = task in ("run-1h", "run-15m") and (not ts or now_ms - ts > STALE_MIN * 60_000)
        rows.append(f'<span class="run {"bad" if stale else ""}">{label}: {_ago(ts, now_ms)}</span>')
    return " ".join(rows)


def _open_table(sigs: list[dict]) -> str:
    rows = []
    for s in sigs:
        lc = s["payload"]["lifecycle"]
        price = s.get("price")
        rows.append(
            f"<tr><td>{_e(s['system'])}</td><td><b>{_e(s['symbol'])}</b></td><td>{_side(s['side'])}</td>"
            f"<td>{_e(s['grade'])} <small>({s['score']:.0f})</small></td>"
            f"<td>{_e(STATUS_FA.get(s['status'], s['status']))}</td>"
            f"<td>{fmt_price(lc['entry'])}</td><td>{fmt_price(price) if price else '—'}</td>"
            f"<td>{fmt_price(lc['sl_now'])}</td><td>{fmt_price(lc['tp1'])}</td><td>{fmt_price(lc['tp2'])}</td>"
            f"<td>{_r(s.get('open_r'))}</td><td>{_t(s['created_at'])}</td></tr>")
    if not rows:
        return '<p class="muted">الان پوزیشن یا سفارش بازی نیست.</p>'
    return ('<div class="scroll"><table><thead><tr><th>سیستم</th><th>کوین</th><th>جهت</th><th>رده</th>'
            '<th>وضعیت</th><th>ورود</th><th>قیمت فعلی</th><th>استاپ فعلی</th><th>TP1</th><th>TP2</th>'
            '<th>R الان</th><th>زمان سیگنال</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def _closed_table(sigs: list[dict], limit: int = 60) -> str:
    done = [s for s in sigs if s["status"] in CLOSED + UNFILLED]
    done.sort(key=lambda s: s["payload"]["lifecycle"].get("closed_at") or s["updated_at"], reverse=True)
    rows = []
    for s in done[:limit]:
        lc = s["payload"]["lifecycle"]
        r = lc["realized_r"] if s["status"] in CLOSED else None
        rows.append(
            f"<tr><td>{_e(s['system'])}</td><td><b>{_e(s['symbol'])}</b></td><td>{_side(s['side'])}</td>"
            f"<td>{_e(s['grade'])} <small>({s['score']:.0f})</small></td>"
            f"<td>{_e(STATUS_FA.get(s['status'], s['status']))}</td><td>{_r(r)}</td>"
            f"<td>{_t(s['created_at'])}</td><td>{_t(lc.get('closed_at') or s['updated_at'])}</td></tr>")
    if not rows:
        return '<p class="muted">هنوز سیگنالی بسته نشده است.</p>'
    return ('<div class="scroll"><table><thead><tr><th>سیستم</th><th>کوین</th><th>جهت</th><th>رده</th>'
            '<th>نتیجه</th><th>R</th><th>زمان سیگنال</th><th>زمان بسته شدن</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


def _candidates(systems: list[dict], limit: int = 15) -> str:
    items = []
    for s in systems:
        for it in (s["evaluations"].get("items") or []):
            items.append((s["name"], it))
    items.sort(key=lambda x: -(x[1].get("score") or 0))
    rows = []
    for name, it in items[:limit]:
        failed = [GATE_FA.get(g, g) for g, ok in (it.get("gates") or {}).items() if not ok]
        why = "، ".join(failed) or ("" if it.get("grade") in ("A", "B") else "امتیاز کم")
        rows.append(
            f"<tr><td>{_e(name)}</td><td><b>{_e(it.get('base'))}</b></td><td>{_side(it.get('side'))}</td>"
            f"<td>{(it.get('score') or 0):.0f}</td><td>{_e(it.get('grade') or '—')}</td>"
            f"<td>{_e(why) or '—'}</td><td>{fmt_price(it['price']) if it.get('price') else '—'}</td></tr>")
    if not rows:
        return '<p class="muted">در آخرین بررسی کاندیدی نبود.</p>'
    ts = max((s["evaluations"].get("ts") or 0) for s in systems)
    return (f'<p class="muted">آخرین بررسی: {_t(ts)} — حد سیگنال: رده‌ی A یا B</p>'
            '<div class="scroll"><table><thead><tr><th>سیستم</th><th>کوین</th><th>جهت</th><th>امتیاز</th>'
            '<th>رده</th><th>چرا سیگنال نشد</th><th>قیمت</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div>")


def _watch(s: dict) -> str:
    f = s["funnel"]
    short = f.get("shortlist") or []
    watch = f.get("watchlist") or {}
    chips = "".join(f'<span class="chip">{_e(x.get("base"))} {_side(x.get("side"))} '
                    f'<small>{(x.get("score") or 0):.0f}</small></span>' for x in short)
    wl = "".join(f'<span class="chip">{_e(b)} <small>{_e(", ".join(v) if isinstance(v, list) else v)}</small></span>'
                 for b, v in list(watch.items())[:60])
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


def _settings(cfg: dict, systems: list[dict]) -> str:
    u, t, rt = cfg.get("universe") or {}, cfg.get("trade") or {}, cfg.get("realtime") or {}
    items = [(s["name"], s["variant"]) for s in systems] + [
        ("تعداد کوین‌ها", f"{u.get('coins_top_n', '—')} برتر"),
        ("حداقل حجم ۲۴ساعته", f"${(u.get('min_volume_24h_usd') or 0) / 1e6:.0f}M"),
        ("ریسک پایه هر معامله", f"{t.get('base_risk_pct', '—')}٪"),
        ("کندل باز (لحظه‌ای)", ", ".join(rt.get("open_candle_tfs") or []) or "خاموش"),
    ]
    return "".join(f'<div class="kpi"><div class="k">{_e(k)}</div><div class="v small">{_e(v)}</div></div>'
                   for k, v in items)


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--muted:#6b7280;--line:#e5e7eb;--pos:#118a4e;--neg:#c62828;
--warn:#b45309;--long:#e7f6ee;--short:#fdecec}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e6e8eb;--muted:#9aa3ad;
--line:#2a3038;--pos:#3ccf85;--neg:#ff6b6b;--warn:#f0a94b;--long:#123323;--short:#3a1717}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 Vazirmatn,Tahoma,system-ui,sans-serif}
main{max-width:1200px;margin:auto;padding:16px}h1{font-size:20px;margin:4px 0}
h2{font-size:16px;margin:0 0 10px}section{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:14px;margin:12px 0}.muted{color:var(--muted)}small{color:var(--muted)}
.kpis{display:flex;flex-wrap:wrap;gap:8px}.kpi{border:1px solid var(--line);border-radius:8px;
padding:8px 12px;min-width:120px}.kpi .k{color:var(--muted);font-size:12px}.kpi .v{font-size:18px;font-weight:600}
.kpi .v.small{font-size:14px}.pos{color:var(--pos);font-weight:600}.neg{color:var(--neg);font-weight:600}
.warn{color:var(--warn)}.badge{padding:1px 8px;border-radius:10px;font-size:12px}
.badge.long{background:var(--long);color:var(--pos)}.badge.short{background:var(--short);color:var(--neg)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;white-space:nowrap}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right}th{color:var(--muted);font-weight:500}
td{direction:ltr;unicode-bidi:plaintext}.run{display:inline-block;margin:2px 6px 2px 0;padding:2px 8px;
border-radius:6px;border:1px solid var(--line)}.run.bad{border-color:var(--neg);color:var(--neg)}
.chip{display:inline-block;border:1px solid var(--line);border-radius:14px;padding:1px 8px;margin:2px}
.chart{width:100%;height:auto}.chart .zero{stroke:var(--line)}.chart .axis{fill:var(--muted);font-size:11px}
.legends{margin-top:6px}.legend{margin-left:14px}.legend i{display:inline-block;width:10px;height:10px;
border-radius:2px;margin-left:4px}a{color:inherit}
.sys{border-top:1px dashed var(--line);padding-top:8px;margin-top:8px}.sys:first-of-type{border:0;margin:0;padding:0}
"""


def render(cfg: dict, data: dict) -> str:
    now = data["now"]
    systems = data["systems"]
    for s in systems:
        s["_now"] = now
    all_sigs = [x for s in systems for x in s["signals"]]
    open_sigs = sorted((x for x in all_sigs if x["status"] not in CLOSED + UNFILLED),
                       key=lambda x: -x["created_at"])
    head = "".join(
        f'<div class="sys"><b>{_e(s["name"])}</b> <small>({_e(s["variant"])})</small><br>{_regime(s)}'
        f'<div class="kpis" style="margin-top:8px">{_kpis(s)}</div></div>' for s in systems)
    runs = _runs(systems[0], now) if systems else "دیتابیسی پیدا نشد."
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60"><meta name="robots" content="noindex,nofollow">
<title>پنل سیگنال‌ها</title><style>{CSS}</style></head><body><main>
<h1>پنل سیستم سیگنال</h1>
<p class="muted">به‌روزرسانی: {_t(now)} به وقت تهران · این صفحه هر دقیقه خودش تازه می‌شود ·
R طبق پلن سیگنال است، بدون کارمزد و فاندینگ</p>
<section><h2>آخرین اجراها</h2>{runs}</section>
<section><h2>وضعیت بازار و نتایج</h2>{head}</section>
<section><h2>منحنی مجموع R</h2>{_equity_svg(systems)}</section>
<section><h2>پوزیشن‌ها و سفارش‌های باز ({len(open_sigs)})</h2>{_open_table(open_sigs)}</section>
<section><h2>نزدیک‌ترین کاندیدها به سیگنال</h2>{_candidates(systems)}</section>
<section><h2>تاریخچه‌ی سیگنال‌ها</h2>{_closed_table(all_sigs)}</section>
<section><h2>شورت‌لیست و واچ‌لیست</h2>{_watch(systems[0]) if systems else ""}</section>
<section><h2>هشدارهای خبری</h2>{_news(systems)}</section>
<section><h2>تنظیمات فعلی</h2><div class="kpis">{_settings(cfg, systems)}</div></section>
</main></body></html>
"""


def build(cfg: dict, out_dir: str | Path, now_ms: int | None = None) -> Path:
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    page = render(cfg, collect(cfg, now_ms))
    tmp = out / ".index.html.tmp"
    tmp.write_text(page, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, out / "index.html")           # nginx never sees a half-written page
    return out / "index.html"
