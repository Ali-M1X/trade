"""System 3 test report (2026-11-17): system 1 / system 2 rules on small coins (engine replays,
one JSON per period slice) against the pump-and-dump catcher (scripts/s3_pump.py), split into a
tuning period and a test period at TEST_FROM. Choices are read on the tuning period only.

Usage: python scripts/s3_report.py TEST_FROM PUMP.json ENGINE_SLICE.json ... > report.md
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone

CLOSED = ("sl", "breakeven", "tp3")
NAMES = {"s3_s1_small": "قوانین سیستم ۱ روی کوین‌های کوچک", "s3_s2_small": "قوانین سیستم ۲ روی کوین‌های کوچک"}


def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m")


def stats(tr: list[dict]) -> dict:
    tr = sorted(tr, key=lambda t: t["closed_at"])
    n = len(tr)
    if not n:
        return {"n": 0}
    net = [t["net_r"] for t in tr]
    wins = [r for r in net if r > 0]
    loss = -sum(r for r in net if r <= 0)
    cum = peak = dd = 0.0
    for r in net:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    months = defaultdict(float)
    for t in tr:
        months[_month(t["closed_at"])] += t["net_r"]
    random.seed(7)
    pos = sum(1 for _ in range(2000) if sum(random.choice(net) for _ in range(n)) > 0) / 2000
    return {"n": n, "win": len(wins) / n * 100, "avg": sum(net) / n, "tot": sum(net),
            "gross": sum(t["gross_r"] for t in tr), "pf": sum(wins) / loss if loss else float("inf"),
            "dd": dd, "mpos": sum(1 for v in months.values() if v > 0), "mn": len(months), "p": pos * 100}


def row(name: str, s: dict) -> str:
    if not s.get("n"):
        return f"| {name} | 0 | – | – | – | – | – | – | – |"
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"| {name} | {s['n']} | {s['win']:.0f}% | {s['avg']:+.2f} | {s['tot']:+.1f} | {s['gross']:+.1f} | {pf} | "
            f"{s['dd']:.1f} | {s['mpos']}/{s['mn']} · {s['p']:.0f}% |")


HEAD = ("| گزینه | معامله | برد | میانگین R | جمع R خالص | جمع R قبل از هزینه | ضریب سود | بیشترین افت R | ماه مثبت · احتمال جمع مثبت |\n"
        "|---|---|---|---|---|---|---|---|---|")


def main(cut_day: str, pump_path: str, *engine_paths: str) -> None:
    cut = _ms(cut_day)
    groups: dict[str, list] = {}
    open_left = defaultdict(int)
    start = end = None
    for p in engine_paths:
        r = json.load(open(p, encoding="utf-8"))
        name = r.get("variant", p)
        for t in r["trades"]:
            if not t.get("filled_at"):
                continue
            if t["status"] in CLOSED:
                groups.setdefault(name, []).append(t)
            else:
                open_left[name] += 1
        m = r.get("meta", {})
        start = min(start or m.get("start", 0), m.get("start", 0) or start or 0)
        end = max(end or 0, m.get("end", 0))
    pump = json.load(open(pump_path, encoding="utf-8"))
    for name, v in pump["variants"].items():
        groups[name] = v["trades"]
    order = [k for k in NAMES if k in groups] + list(pump["variants"])

    def part(name, test):
        return [t for t in groups.get(name, []) if (t["created"] >= cut) == test]

    out = [f"# سیستم ۳: مقایسه‌ی دو گزینه ({_month(pump['start'])} تا {_month(pump['end'])})", "",
           f"دوره‌ی آموزش: تا {cut_day} (برای انتخاب). دوره‌ی آزمون: از {cut_day} (فقط گزارش). "
           f"همه‌ی R ها بعد از کارمزد ۰٫۰۵٪ و لغزش {pump['slippage_pct']}٪ در هر ورود و خروج‌اند. "
           f"کوین‌ها: بدون ۲۰ کوین اول بازار، حجم روزانه حداقل ۳ میلیون دلار ({pump['coins']} کوین با کندل ساعتی).", ""]
    for title, test in (("دوره‌ی آموزش", False), ("دوره‌ی آزمون", True)):
        out += [f"## {title}", "", HEAD]
        out += [row(NAMES.get(k, k), stats(part(k, test))) for k in order]
        out.append("")
    best = {}
    for kind in ("pump", "dump"):
        cands = [k for k, v in pump["variants"].items() if v["side"] == kind]
        tr = {k: stats(part(k, False)) for k in cands}
        ok = [k for k in cands if tr[k].get("n", 0) >= 20]
        if ok:
            best[kind] = max(ok, key=lambda k: tr[k]["avg"])
    if best:
        out += ["## انتخاب روی دوره‌ی آموزش", ""]
        for kind, k in best.items():
            out.append(f"- {'پامپ (لانگ)' if kind == 'pump' else 'دامپ (شورت)'}: **{k}** · آزمون: "
                       + row(k, stats(part(k, True))).split("|", 2)[2].strip(" |").replace(" | ", " · "))
        out.append("")
    if open_left:
        out += ["معامله‌هایی که در پایان هر برش زمانی هنوز باز بودند حساب نشده‌اند: "
                + "، ".join(f"{NAMES.get(k, k)} {v}" for k, v in open_left.items()), ""]
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], *sys.argv[3:])
