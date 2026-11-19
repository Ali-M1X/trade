(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("panel-data").textContent);
  var LWC = window.LightweightCharts;
  var app = document.getElementById("app");
  var S = {                                   // UI state (kept across data refreshes)
    tab: "home", sel: null, tf: null, hist: {k: "", s: "", side: "", res: "", q: "", sort: "closed", dir: "desc"},
    cand: {s: ""}, live: {}, liveOk: false, hiddenSeries: {}, pdays: 3
  };
  try { var _pd = +localStorage.getItem("pdays"); if ([1, 3, 7].indexOf(_pd) >= 0) S.pdays = _pd; } catch (e) {}
  var charts = [];                            // charts of the current tab (removed on re-render)
  var candleCache = {};

  // ------------------------------------------------------------------ helpers
  // news titles in Persian (2026-11-17, Ali); the English original stays on hover and in search
  function faT(t) { return (D && D.tr && D.tr[t]) || t; }
  function trT(t) { var f = faT(t); return f === t ? esc(t) : '<span title="' + esc(t) + '">' + esc(f) + "</span>"; }
  function esc(x) {
    return String(x == null ? "" : x).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function cssv(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  var FA = {};
  function jfmt(ms, kind) {
    if (!ms) return "—";
    var opts = {
      full: {year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false},
      dt: {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false},
      date: {month: "short", day: "numeric"},
      ymd: {year: "numeric", month: "long", day: "numeric"},
      time: {hour: "2-digit", minute: "2-digit", hour12: false},
      month: {year: "numeric", month: "short"}
    }[kind || "dt"];
    var key = kind || "dt";
    if (!FA[key]) {
      try { FA[key] = new Intl.DateTimeFormat("fa-IR-u-ca-persian", Object.assign({timeZone: "Asia/Tehran"}, opts)); }
      catch (e) { FA[key] = new Intl.DateTimeFormat("fa-IR", opts); }
    }
    return FA[key].format(new Date(ms));
  }
  function ago(ms) {
    if (!ms) return "هرگز";
    var m = Math.max(0, Math.round((Date.now() - ms) / 60000));
    if (m < 1) return "همین الان";
    if (m < 60) return fa(m) + " دقیقه پیش";
    if (m < 48 * 60) return fa(Math.round(m / 60)) + " ساعت پیش";
    return fa(Math.round(m / 1440)) + " روز پیش";
  }
  function fa(n) { return Number(n).toLocaleString("fa-IR"); }
  function dur(h) {
    if (h == null) return "—";
    if (h < 24) return fa(h.toFixed(1)) + " ساعت";
    return fa((h / 24).toFixed(1)) + " روز";
  }
  function px(p) {
    if (p == null || isNaN(p)) return "—";
    var a = Math.abs(p), d = a >= 1000 ? 2 : a >= 100 ? 3 : a >= 1 ? 4 : a >= 0.01 ? 5 : a >= 0.0001 ? 7 : 9;
    return Number(p).toFixed(d).replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "");
  }
  function cls(x) { return x > 0 ? "pos" : x < 0 ? "neg" : ""; }
  function R(x) { return x == null ? "—" : '<b class="ltr ' + cls(x) + '">' + (x > 0 ? "+" : "") + x.toFixed(2) + "R</b>"; }
  function usd(x, sign) {
    if (x == null || isNaN(x)) return "—";
    if (sign === false) return '<span class="ltr">$' + x.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + "</span>";
    var m = x > 0.005 ? "+" : x < -0.005 ? "−" : "";
    return '<b class="ltr ' + cls(x) + '">' + m + "$" + Math.abs(x).toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2}) + "</b>";
  }
  function pct(x, d) { return x == null ? "—" : '<span class="ltr ' + cls(x) + '">' + (x > 0 ? "+" : "") + x.toFixed(d == null ? 1 : d) + "%</span>"; }
  function side(s) { return s > 0 || s === "long" ? '<span class="badge long">لانگ</span>' : '<span class="badge short">شورت</span>'; }
  function kind(t) {
    return (t.approved ? '<span class="badge ok">تأییدشده</span>' : '<span class="badge watch">Watch</span>') +
      (t.source === "replay" ? ' <span class="badge replay" title="بازسازی روی داده‌ی تاریخی">بازسازی</span>' : "");
  }
  function sysShort(n) { return n === "سیستم ۱" ? "S1" : n === "سیستم ۲" ? "S2" : n; }
  function trades() { return D.trades || []; }
  function byId(id) { return trades().filter(function (t) { return t.id === id; })[0]; }
  function livePrice(t) { var l = S.live[t.base]; return l ? l.price : t.price; }
  function openR(t) {
    var p = livePrice(t);
    if (!t.open || !t.filled_at || p == null) return t.open_r;
    var risk = Math.abs(t.entry - t.sl);
    if (!risk) return t.open_r;
    return t.gross_r + (t.open_frac == null ? 1 : t.open_frac) * t.side * (p - t.entry) / risk;
  }
  function plural(n, w) { return fa(n) + " " + w; }

  // ------------------------------------------------------------------ status bar & theme
  function theme() { return document.documentElement.getAttribute("data-theme") || "dark"; }
  function setTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("theme", t); } catch (e) {}
    document.getElementById("theme").textContent = t === "dark" ? "☀" : "☾";
    charts.forEach(function (c) { c.applyOptions(chartTheme()); });
  }
  document.getElementById("theme").addEventListener("click", function () { setTheme(theme() === "dark" ? "light" : "dark"); });
  function status() {
    var s0 = (D.systems || [])[0] || {};
    var run = (s0.last_runs || {})["run-1h"] || (s0.last_runs || {})["run-15m"];
    var stale = !run || Date.now() - run > (D.stale_min || 20) * 60000;
    document.getElementById("status").innerHTML =
      '<span><span class="dot ' + (stale ? "stale" : "live") + '"></span>' +
      (stale ? "اجرای ربات عقب افتاده: " : "ربات فعال · آخرین بررسی ") + esc(ago(run)) + "</span>" +
      "<span>به‌روزرسانی پنل: " + esc(ago(D.now)) + "</span>" +
      (S.liveOk ? '<span><span class="dot live"></span>قیمت زنده</span>' : "");
  }

  // ------------------------------------------------------------------ charts
  function chartTheme() {
    return {
      layout: {background: {type: "solid", color: "transparent"}, textColor: cssv("--muted"),
        fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 12, attributionLogo: false},
      grid: {vertLines: {color: cssv("--grid")}, horzLines: {color: cssv("--grid")}},
      rightPriceScale: {borderColor: cssv("--line")},
      timeScale: {borderColor: cssv("--line")},
      crosshair: {mode: 0}
    };
  }
  function tsec(ms) { return Math.floor(ms / 1000); }
  function makeChart(el, extra) {
    if (!LWC) { el.innerHTML = '<div class="empty">کتابخانه‌ی نمودار بارگذاری نشد.</div>'; return null; }
    var opts = Object.assign({
      autoSize: true,
      localization: {locale: "fa-IR", timeFormatter: function (t) { return jfmt(t * 1000, "dt"); },
        priceFormatter: function (p) { return px(p); }},
      timeScale: {timeVisible: true, secondsVisible: false, borderColor: cssv("--line"),
        tickMarkFormatter: function (t, type) {
          return jfmt(t * 1000, type <= 1 ? "month" : type === 2 ? "date" : "time");
        }}
    }, extra || {});
    var c = LWC.createChart(el, opts);
    c.applyOptions(chartTheme());
    charts.push(c);
    return c;
  }
  function clearCharts() { charts.forEach(function (c) { try { c.remove(); } catch (e) {} }); charts = []; }
  function uniq(points) {                    // LWC needs strictly increasing times
    var out = [];
    points.forEach(function (p) {
      if (out.length && out[out.length - 1].time >= p.time) out[out.length - 1] = {time: out[out.length - 1].time, value: p.value};
      else out.push(p);
    });
    return out;
  }

  // ------------------------------------------------------------------ tabs
  var TABS = {sum: summaryTab, home: home, chart: chartTab, hist: histTab, perf: perf, cmp: compare, mkt: marketTab, sys: health, news: news, cal: calendarTab, ai: aiTab, strat: strategy, how: method};
  var ALIAS = {reg: ["mkt", "mk-now"]};          // 2026-11-17: the regime page is now part of the market page
  function show(tab, push) {
    if (ALIAS[tab]) { S.jump = ALIAS[tab][1]; tab = ALIAS[tab][0]; }
    if (!TABS[tab]) tab = "home";
    S.tab = tab;
    document.querySelectorAll("#tabs button").forEach(function (b) { b.classList.toggle("on", b.dataset.t === tab); });
    try { localStorage.setItem("tab3", tab); } catch (e) {}
    if (push !== false) {
      var h = "#" + tab + (tab === "chart" && S.sel ? "/" + encodeURIComponent(S.sel) : "");
      if (location.hash !== h) window.history.replaceState(null, "", h);
    }
    render();
  }
  document.getElementById("tabs").addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (b) show(b.dataset.t);
  });
  function render() {
    clearCharts();
    status();
    TABS[S.tab]();
    decorateTips();
    var p = document.getElementById("pos-count");
    if (p) p.textContent = fa(openTrades().length);
  }
  function openTrades() { return trades().filter(function (t) { return t.open && t.filled_at; }); }
  function pendingTrades() { return trades().filter(function (t) { return t.status === "pending"; }); }
  function closedTrades() { return trades().filter(function (t) { return !t.open; }); }
  function isPartial(t) { return !!(t.open && t.filled_at && t.open_frac != null && t.open_frac < 0.999); }
  function partlyClosed() { return trades().filter(isPartial); }
  // one rule for every result in the panel (2026-11-16, Ali: "the same numbers everywhere"):
  // a trade has a result once it is closed or past TP1. The part closed at TP1/TP2 is booked
  // when it happens, like on an exchange; the rest when the trade closes.
  function isDone(t) { return !!(t.filled_at && t.net_r != null && (!t.open || isPartial(t))); }
  function doneTs(t) {
    if (!t.open) return t.closed_at;
    var e = (t.events || []).filter(function (x) { return x[0] === "tp1" || x[0] === "tp2"; }).pop();
    return e ? e[1] : t.filled_at;
  }
  // 2026-11-17 (Ali): a trade's $ is the same on every page: its own account's (approved signals in the
  // approved account, Watch in the watch account). Only views of the "all" account pass all = true.
  function tRisk(t, all) { return all || t.own_risk_usd == null ? t.risk_usd : t.own_risk_usd; }
  function tSize(t, all) { return t.size_usd == null || !t.risk_usd ? t.size_usd : t.size_usd * tRisk(t, all) / t.risk_usd; }
  function tPnl(t, all) { return all || t.own_risk_usd == null ? t.pnl_usd : t.own_pnl_usd; }
  function doneUsd(t, all) {
    var risk = tRisk(t, all);
    if (!t.open) return tPnl(t, all);
    return risk != null ? risk * t.net_r : null;
  }
  function doneTrades(list) { return (list || trades()).filter(isDone).sort(function (a, b) { return (doneTs(a) || 0) - (doneTs(b) || 0); }); }
  function openTrade(id) { S.sel = id; S.tf = null; show("chart"); window.scrollTo(0, 0); }
  app.addEventListener("click", function (e) {
    var el = e.target.closest("[data-open]");
    if (el) { openTrade(el.getAttribute("data-open")); return; }
    var c = e.target.closest("[data-cand]");
    if (c) { S.sel = c.getAttribute("data-cand"); S.tf = null; show("chart"); window.scrollTo(0, 0); }
  });

  // ------------------------------------------------------------------ home
  var ACC = [["approved", "تأییدشده", "سیگنال‌های A/B که به تلگرام رفتند", "--acc"],
             ["watch", "تأییدنشده (Watch)", "ستاپ‌هایی که ارسال نشدند ولی دنبال شدند", "--warn"],
             ["all", "همه با هم", "هر دو نوع در یک حساب", "--acc2"]];
  function accLive(key) {                    // equity with live prices for the open positions
    var a = D.accounts[key], extra = 0;
    trades().forEach(function (t) {
      if (!t.open || !t.filled_at) return;
      if (key === "approved" && !t.approved) return;
      if (key === "watch" && t.approved) return;
      var risk = tRisk(t, key === "all"), r = openR(t);
      if (risk != null && r != null) extra += risk * (r - (isPartial(t) ? t.net_r || 0 : 0));
    });
    return {equity: a.balance + extra, unreal: extra};
  }
  function accLine(key, l) {
    var a = D.accounts[key], ret = a.start ? (l.equity - a.start) / a.start * 100 : 0;
    return pct(ret, 2) + ' <small>از $' + a.start + " · موجودی قطعی " + usd(a.balance, false) + " · باز " + usd(l.unreal) + "</small>";
  }
  function home() {
    var cards = ACC.map(function (x) {
      var a = D.accounts[x[0]], l = accLive(x[0]);
      return '<div class="acct" style="--c:var(' + x[3] + ')"><div class="h"><b>' + x[1] + "</b><small>" + x[2] + "</small></div>" +
        '<div class="big ltr" id="eqv-' + x[0] + '">$' + l.equity.toFixed(2) + "</div>" +
        '<div id="eqr-' + x[0] + '">' + accLine(x[0], l) + "</div>" +
        '<div class="kv"><div><span class="l">سود/زیان قطعی</span>' + usd(a.pnl) + "</div>" +
        '<div><span class="l">نرخ برد</span>' + (a.done ? fa(Math.round(a.wins / a.done * 100)) + "٪ <small>(" + fa(a.wins) + " از " + fa(a.done) + ")</small>" : "—") + "</div>" +
        '<div><span class="l">بیشترین افت</span><span class="ltr">' + (a.max_dd || 0).toFixed(1) + "%</span></div>" +
        '<div><span class="l">ضریب سود</span><span class="ltr">' + (a.pf == null ? "—" : a.pf.toFixed(2)) + "</span></div>" +
        '<div><span class="l">کارمزد و هزینه</span>' + usd(-(a.fees || 0)) + "</div>" +
        '<div><span class="l">باز / در انتظار</span>' + fa(a.open) + " / " + fa(a.pending) + "</div></div></div>";
    }).join("");
    var open = openTrades().sort(function (a, b) { return b.created - a.created; });
    var s1 = (D.systems || []);
    app.innerHTML = eventBanner() +
      '<section><h2>حساب‌های شبیه‌سازی‌شده <small>شروع ' + esc(jfmt(D.start, "ymd")) + " با $" + D.balance + '</small></h2><div class="grid g3">' + cards + "</div></section>" + openRiskSection() +
      equitySection() +
      '<div class="grid g2"><section><h2>پوزیشن‌های باز <small>' + fa(open.length) + "</small></h2><div id=\"minipos\">" + miniPositions(open) + "</div></section>" +
      "<section><h2>وضعیت بازار</h2>" + s1.map(regimeMini).join("") + "</section></div>" +
      "<section><h2>آخرین اجراها</h2>" + runs() + "</section>";
    equityChart();
  }
  function miniPositions(list) {
    if (!list.length) return '<div class="empty">الان پوزیشن بازی نیست.</div>';
    return '<div class="scroll"><table data-defsort="7:desc"><thead><tr><th>کوین</th><th>جهت</th><th>سیستم</th><th>نوع</th><th>اهرم</th><th>قیمت</th><th>سود/زیان</th><th>سود٪</th></tr></thead><tbody>' +
      list.map(function (t) {
        return '<tr class="click" data-open="' + esc(t.id) + '"><td><b>' + esc(t.base) + "</b></td><td>" + side(t.side) + "</td><td>" + esc(sysShort(t.system)) +
          "</td><td>" + kind(t) + "</td>" + levCell(t) + '<td class="num">' + px(livePrice(t)) + "</td><td>" + RU(openR(t), pnlUsd(t)) + splitNote(t) + "</td>" + pctCell(t) + "</tr>";
      }).join("") + "</tbody>" + totRow([{span: 6, v: "جمع " + fa(list.length) + " پوزیشن"}, posTot(list), ""]) + "</table></div>";
  }
  var BIAS = {long: "فقط لانگ", short: "فقط شورت", both: "هر دو طرف"};
  var REG = {alt_season: "آلت‌سیزن", btc_led: "رهبری بیت‌کوین", risk_off: "ریسک‌گریزی", capitulation: "تسلیم", neutral: "خنثی",
    bull: "صعودی", range: "رنج", bear: "نزولی"};
  function regimeMini(s) {
    var r = s.regime || {}, m = s.majors || {};
    return '<div class="stat" style="margin-bottom:8px"><span class="l">' + esc(s.name) + " <small>(" + esc(s.variant) + ")</small></span>" +
      '<span class="v">' + esc(REG[r.name] || r.name || "—") + "</span> <small>" + esc(BIAS[r.bias] || r.bias || "") + "</small>" +
      (m.btc != null ? '<div class="muted" style="font-size:12px">امتیاز BTC <span class="ltr">' + m.btc + "</span> · ETH <span class=\"ltr\">" + m.eth + "</span> · ETH/BTC <span class=\"ltr\">" + m.ethbtc + "</span>" +
        (m.btc_weak ? ' · <span class="warn">BTC ضعیف</span>' : "") + "</div>" : "") +
      (s.paused_until ? '<div class="warn">توقف بعد از ضررهای پشت‌سرهم تا ' + esc(jfmt(s.paused_until)) + "</div>" : "") + "</div>";
  }
  // signals the book refused (2026-11-15, Ali: how often did a good setup find no room?)
  var SKIPR = {max_active: ["سقف پوزیشن پر بود", "warn"], max_open_risk: ["سقف ریسک باز پر بود", "warn"], correlated: ["هم‌جهت با پوزیشن هم‌بسته", "gray"],
    loss_streak_pause: ["توقف بعد از ضررهای پشت‌سرهم", "short"], cooldown: ["تازه استاپ خورده بود", "gray"]};
  function skips(days) { var cut = Date.now() - days * 86400000; return (D.skips || []).filter(function (s) { return s.last >= cut; }); }
  function skipsSection() {
    var all = skips(30), by = {};
    all.forEach(function (s) { by[s.reason] = (by[s.reason] || 0) + 1; });
    var day = skips(1).length;
    return '<section id="skips"><h2>سیگنال‌های ردشده <small>A و B که ارسال نشدند، ۳۰ روز گذشته</small></h2>' +
      (all.length ? '<div class="grid g4">' +
        statCard("۲۴ ساعت گذشته", fa(day), day ? "سیگنال خوب جا پیدا نکرد" : "چیزی رد نشده") +
        Object.keys(by).map(function (k) { return statCard(esc((SKIPR[k] || [k])[0]), fa(by[k]), "در ۳۰ روز"); }).join("") + "</div>" +
        '<div class="scroll"><table><thead><tr><th>زمان</th><th>کوین</th><th>جهت</th><th>رده</th><th>امتیاز</th><th>سیستم</th><th>دلیل</th></tr></thead><tbody>' +
        all.slice(0, 30).map(function (s) {
          var r = SKIPR[s.reason] || [s.reason, "gray"];
          return '<tr><td data-v="' + s.ts + '">' + esc(jfmt(s.ts, "dt")) + (s.n > 1 ? ' <small class="muted">(' + fa(s.n) + " بار تا " + esc(jfmt(s.last, "time")) + ")</small>" : "") + "</td><td><b>" + esc(s.base) + "</b></td><td>" + side(s.side) +
            "</td><td>" + esc(s.grade) + "</td><td>" + fa(Math.round(s.score || 0)) + "</td><td>" + esc(sysShort(s.system)) + '</td><td><span class="badge ' + r[1] + '">' + esc(r[0]) + "</span></td></tr>";
        }).join("") + "</tbody></table></div>"
        : '<p class="pos">در ۳۰ روز گذشته هیچ سیگنال A یا B‌ای رد نشده است. (ثبت از ۲۴ آبان شروع شده.)</p>') + "</section>";
  }
  var TASKS = {"run-1h": "بررسی سیگنال", "run-15m": "مدیریت پوزیشن‌ها", "run-4h": "قیف و شورت‌لیست", "run-daily": "روزانه", "run-weekly": "هفتگی"};
  function runs() {
    var s = (D.systems || [])[0];
    if (!s) return '<div class="empty">دیتابیسی پیدا نشد.</div>';
    return Object.keys(TASKS).map(function (k) {
      var ts = (s.last_runs || {})[k];
      var bad = (k === "run-1h" || k === "run-15m") && (!ts || Date.now() - ts > (D.stale_min || 20) * 60000);
      return '<span class="run ' + (bad ? "bad" : "") + '" title="' + esc(jfmt(ts, "full")) + '">' + TASKS[k] + ": " + esc(ago(ts)) + "</span>";
    }).join("");
  }
  // equity curve (2026-11-15, Ali: "prettier"): one account as a filled baseline around the start
  // balance (green above, red below) with its closed trades as dots, or all three as lines
  S.eq = {m: "approved", r: "all"};
  function equitySection() {
    function seg(id, cur, opts) {
      return '<div class="seg" id="' + id + '">' + opts.map(function (o) { return '<button data-v="' + o[0] + '" class="' + (cur === o[0] ? "on" : "") + '">' + o[1] + "</button>"; }).join("") + "</div>";
    }
    return '<section class="eqsec"><h2>منحنی موجودی</h2><div class="filters">' +
      seg("eqm", S.eq.m, ACC.map(function (x) { return [x[0], x[1]]; }).concat([["cmp", "هر سه با هم"]])) +
      seg("eqr", S.eq.r, [["1", "۲۴ ساعت"], ["7", "۷ روز"], ["all", "از شروع"]]) +
      '</div><div class="eqhead" id="eqhead"></div><div class="chartbox eqbox" id="eq"></div></section>';
  }
  function eqHead(key, v, ts) {
    var a = D.accounts[key], ret = (v - a.start) / a.start * 100;
    return '<span class="eqv ltr">$' + v.toFixed(2) + "</span>" + pct(ret, 2) + " <small>" + usd(v - a.start) + " از شروع · " +
      esc(ts ? jfmt(ts * 1000, "dt") : "الان") + "</small>";
  }
  function equityChart() {
    var el = document.getElementById("eq"), head = document.getElementById("eqhead");
    if (!el) return;
    ["eqm", "eqr"].forEach(function (id) {
      document.getElementById(id).onclick = function (e) {
        var b = e.target.closest("button"); if (!b) return;
        S.eq[id === "eqm" ? "m" : "r"] = b.dataset.v;
        this.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
        clearCharts(); el.innerHTML = ""; equityChart();
      };
    });
    var c = makeChart(el, {rightPriceScale: {scaleMargins: {top: 0.12, bottom: 0.08}},
      grid: {vertLines: {visible: false}, horzLines: {color: cssv("--grid"), style: 1}},
      crosshair: {mode: 1, vertLine: {labelVisible: false}}, handleScroll: false, handleScale: false});
    if (!c) return;
    c.applyOptions({grid: {vertLines: {visible: false}}, localization: {priceFormatter: function (v) { return "$" + v.toFixed(2); }}});
    var from = S.eq.r === "all" ? 0 : tsec(Date.now() - Number(S.eq.r) * 86400000);
    function points(key) {
      var a = D.accounts[key], l = accLive(key);
      var pts = uniq((a.eq_curve && a.eq_curve.length ? a.eq_curve : a.curve || []).map(function (p) { return {time: tsec(p[0]), value: p[1]}; }));
      var now = tsec(Date.now());
      if (!pts.length || pts[pts.length - 1].time < now) pts.push({time: now, value: l.equity});
      else pts[pts.length - 1] = {time: pts[pts.length - 1].time, value: l.equity};
      if (from) {
        var before = pts.filter(function (p) { return p.time < from; }).pop();
        pts = pts.filter(function (p) { return p.time >= from; });
        if (before) pts.unshift({time: from, value: before.value});
      }
      return uniq(pts);
    }
    var fmt = {type: "custom", formatter: function (v) { return "$" + v.toFixed(2); }};
    var main = null, mainKey = S.eq.m;
    if (S.eq.m === "cmp") {
      ACC.forEach(function (x, i) {
        var s = c.addLineSeries({color: cssv(x[3]), lineWidth: i === 0 ? 3 : 2, priceFormat: fmt, priceLineVisible: false, lastValueVisible: true,
          crosshairMarkerRadius: 4});
        s.setData(points(x[0]));
        if (i === 0) { main = s; s.createPriceLine({price: D.balance, color: cssv("--muted"), lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: "شروع"}); }
      });
      mainKey = "approved";
      head.innerHTML = ACC.map(function (x) {
        var l = accLive(x[0]);
        return '<span class="eqk"><i style="background:var(' + x[3] + ')"></i>' + x[1] + ' <b class="ltr">$' + l.equity.toFixed(2) + "</b> " +
          pct((l.equity - D.accounts[x[0]].start) / D.accounts[x[0]].start * 100, 2) + "</span>";
      }).join("");
    } else {
      var pos = cssv("--pos"), neg = cssv("--neg");
      main = c.addBaselineSeries({baseValue: {type: "price", price: D.balance}, priceFormat: fmt, lineWidth: 2,
        topLineColor: pos, topFillColor1: hexA(pos, 0.32), topFillColor2: hexA(pos, 0.02),
        bottomLineColor: neg, bottomFillColor1: hexA(neg, 0.02), bottomFillColor2: hexA(neg, 0.32),
        priceLineVisible: false, lastValueVisible: true, crosshairMarkerRadius: 5});
      var pts = points(S.eq.m);
      main.setData(pts);
      main.createPriceLine({price: D.balance, color: cssv("--muted"), lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: "شروع"});
      // a dot for every closed trade of this account: green win, red loss
      var t0 = pts.length ? pts[0].time : 0, t1 = pts.length ? pts[pts.length - 1].time : 0;
      var marks = closedTrades().filter(function (t) {
        if (!t.filled_at || t.net_r == null || !t.closed_at) return false;
        if (S.eq.m === "approved" && !t.approved) return false;
        if (S.eq.m === "watch" && t.approved) return false;
        var s = tsec(t.closed_at); return s >= t0 && s <= t1;
      }).map(function (t) {
        var s = tsec(t.closed_at), at = pts.filter(function (p) { return p.time <= s; }).pop();
        return {time: at ? at.time : t0, position: "inBar", color: t.net_r > 0 ? pos : neg, shape: "circle", size: 0.4};
      }).sort(function (a, b) { return a.time - b.time; });
      main.setMarkers(marks);
      var last = pts[pts.length - 1];
      head.innerHTML = last ? eqHead(S.eq.m, last.value) : "";
      c.subscribeCrosshairMove(function (p) {
        var v = p && p.time && p.seriesData ? p.seriesData.get(main) : null;
        head.innerHTML = v ? eqHead(S.eq.m, v.value, p.time) : (last ? eqHead(S.eq.m, last.value) : "");
      });
    }
    c.timeScale().fitContent();
  }
  function hexA(color, a) {                  // "#2ebd85" -> "rgba(46,189,133,a)"
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(color.trim());
    return m ? "rgba(" + parseInt(m[1], 16) + "," + parseInt(m[2], 16) + "," + parseInt(m[3], 16) + "," + a + ")" : color;
  }

  // ------------------------------------------------------------------ chart tab
  // chart tab, exchange style (2026-11-15, Ali): market list | ticker bar, chart with MAs and an
  // OHLC readout, positions / orders / history tables under it, then the trade's details
  S.xtab = "open"; S.lq = ""; S.lk = "";
  var IND = {ma7: 1, ma25: 1, ma99: 0, vol: 1};
  try { var ind0 = JSON.parse(localStorage.getItem("ind") || "null"); if (ind0) IND = Object.assign(IND, ind0); } catch (e) {}
  var MAS = [["ma7", 7, "--warn"], ["ma25", 25, "--acc2"], ["ma99", 99, "--acc"]];
  // 2026-11-18 (Ali): the dollars put into a position (its margin: size / leverage, in its own account)
  // and the dollars that came back out (margin + P/L after costs; open trades: if closed now)
  function marginUsd(t, all) { var z = tSize(t, all); return z == null || !t.filled_at ? null : z / (t.leverage || 1); }
  function outUsd(t, all) { var m = marginUsd(t, all), p = pnlUsd(t, all); return m == null || p == null ? null : m + p; }
  function usdCell(v, cls) { return '<td' + (cls ? ' class="' + cls + '"' : "") + ' data-v="' + (v == null ? "" : v.toFixed(4)) + '">' + usd(v, false) + "</td>"; }
  function pnlUsd(t, all) {                  // the whole trade in $; open positions at the live price
    if (!t || t.cand || tRisk(t, all) == null) return null;
    if (t.open) { if (!t.filled_at) return null; var r = openR(t); return r == null ? null : tRisk(t, all) * r; }
    return t.filled_at ? tPnl(t, all) : null;
  }
  // open P/L that is not booked yet: a partly closed trade's TP1/TP2 part is already in the balance
  function bookedUsd(t, all) { var risk = tRisk(t, all); return isPartial(t) && risk != null ? risk * (t.net_r || 0) : 0; }
  function floatUsd(t, all) {
    var risk = tRisk(t, all), r = openR(t);
    return t.open && t.filled_at && risk != null && r != null ? risk * r - bookedUsd(t, all) : null;
  }
  // a partly closed trade: what is banked and what is still open, under its total
  function splitNote(t) {
    if (!t || !t.open || !isPartial(t)) return "";
    var f = floatUsd(t);
    return '<br><small class="muted">قطعی ' + usd(bookedUsd(t)) + " + باز " + usd(f) + "</small>";
  }
  // totals cell of a positions table: the whole trades, and how much of it is booked / still open
  function posTot(list) {
    var all = sumOf(list, pnlUsd), booked = sumOf(list, function (t) { return bookedUsd(t); });
    return RU(sumOf(list, openR), all) + (booked ? '<br><small class="muted">قطعی ' + usd(booked) + " · باز " + usd(all - booked) + "</small>" : "");
  }
  // P/L as % of the position's margin, as an exchange shows it: R x stop distance % x leverage.
  // Works for open (live price), partly closed (banked part included) and closed trades.
  function pnlPct(t) {
    if (!t || !t.filled_at || !t.sl_pct) return null;
    var r = t.open ? openR(t) : t.net_r;
    return r == null ? null : r * t.sl_pct * (t.leverage || 1);
  }
  // P/L on margin; open trades also show the plain price move since the entry, small
  function pctTxt(t, move) {
    var v = pnlPct(t), p = move && t.open ? livePrice(t) : null, mv = p && t.entry ? t.side * (p - t.entry) / t.entry * 100 : null;
    return (v == null ? "—" : pct(v, 1)) + (mv == null ? "" : ' <small class="muted">قیمت ' + pct(mv, 2) + "</small>");
  }
  function pctCell(t, move) { var v = pnlPct(t); return '<td class="xpc" data-v="' + (v == null ? "" : v.toFixed(3)) + '">' + pctTxt(t, move) + "</td>"; }
  function levCell(t) { return '<td data-v="' + (t.leverage || "") + '">' + (t.leverage ? '<span class="ltr">' + t.leverage + "x</span>" : "—") + "</td>"; }
  function tsCell(ts, how) { return '<td data-v="' + (ts || "") + '">' + (ts ? esc(jfmt(ts, how)) : "—") + "</td>"; }

  // sortable tables (2026-11-16, Ali): every data table sorts by any column, by the column's own
  // kind (numbers, $, R, %, dates through data-v, text). The choice survives the panel's refresh.
  var SORTS = (function () { try { return JSON.parse(localStorage.getItem("sorts") || "{}"); } catch (e) { return {}; } })();
  var FA_DIG = "۰۱۲۳۴۵۶۷۸۹";
  function cellVal(td) {
    if (!td) return null;
    if (td.hasAttribute("data-v")) { var a = td.getAttribute("data-v"); return a === "" ? null : isNaN(+a) ? a : +a; }
    var t = td.textContent.replace(/[؟\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, "").trim().replace(/[۰-۹]/g, function (d) { return FA_DIG.indexOf(d); })
      .replace(/٫/g, ".").replace(/[٬,]/g, "").replace(/−/g, "-");
    if (!t || t === "—") return null;
    var m = t.match(/^[+\-]?\$?[+\-]?\d+(\.\d+)?/);
    return m ? parseFloat(m[0].replace("$", "").replace("+-", "-").replace("-+", "-")) : t.toLowerCase();
  }
  function tableKey(tb) {
    var sec = tb.closest("section"), h = sec && sec.querySelector("h2");
    return (tb.id || (h ? firstText(h) : "")) + "|" + Array.prototype.map.call(tb.tHead.rows[0].cells, function (c) { return firstText(c); }).join(",");
  }
  function sortTable(tb, col, dir) {
    var body = tb.tBodies[0]; if (!body) return;
    tb._cur = [col, dir];
    var rows = Array.prototype.slice.call(body.rows);
    rows.sort(function (a, b) {
      var x = cellVal(a.cells[col]), y = cellVal(b.cells[col]);
      if (x == null && y == null) return 0;
      if (x == null) return 1;                  // empty cells always last
      if (y == null) return -1;
      var c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "fa");
      return dir === "asc" ? c : -c;
    });
    rows.forEach(function (r) { body.appendChild(r); });
    Array.prototype.forEach.call(tb.tHead.rows[0].cells, function (c, i) { if (i === col) c.setAttribute("data-dir", dir); else c.removeAttribute("data-dir"); });
  }
  function makeSortable(root) {
    (root || app).querySelectorAll("table").forEach(function (tb) {
      if (tb.dataset.sortable || !tb.tHead || !tb.tBodies[0] || tb.tBodies[0].rows.length < 2) return;
      if (tb.classList.contains("cmp") || tb.classList.contains("caltab") || tb.querySelector("th.sort, tr.grp, tr.day, tr.calx")) return;
      tb.dataset.sortable = "1";
      var key = tableKey(tb);
      Array.prototype.forEach.call(tb.tHead.rows[0].cells, function (th, i) {
        if (!firstText(th)) return;
        th.classList.add("sort");
        th.addEventListener("click", function (e) {
          if (e.target.closest(".qi")) return;
          var cur = tb._cur, dir = cur && cur[0] === i && cur[1] === "desc" ? "asc" : "desc";
          SORTS[key] = [i, dir];
          try { localStorage.setItem("sorts", JSON.stringify(SORTS)); } catch (err) {}
          sortTable(tb, i, dir);
        });
      });
      var s = SORTS[key] || (tb.dataset.defsort ? [+tb.dataset.defsort.split(":")[0], tb.dataset.defsort.split(":")[1]] : null);
      if (s) sortTable(tb, s[0], s[1]);
    });
  }

  // totals row at the foot of a table (2026-11-16, Ali: "a sum row wherever it makes sense")
  function totRow(cells) {
    return '<tfoot><tr class="tot">' + cells.map(function (c) {
      return c && typeof c === "object" ? '<td colspan="' + c.span + '">' + c.v + "</td>" : "<td>" + (c == null ? "" : c) + "</td>";
    }).join("") + "</tr></tfoot>";
  }
  function sumOf(list, f) { return list.reduce(function (s, x) { var v = f(x); return s + (v == null || isNaN(v) ? 0 : v); }, 0); }
  function RU(r, u) { return R(r) + (u == null ? "" : ' <small class="usdv">' + usd(u) + "</small>"); }
  function byCreated(a, b) { return b.created - a.created; }
  function chartTab() {
    var open = openTrades().sort(byCreated), pend = pendingTrades();
    var closed = closedTrades().filter(function (t) { return t.filled_at; }).sort(function (a, b) { return (b.closed_at || 0) - (a.closed_at || 0); });
    var cands = candidates();
    var f0 = findSel(S.sel);
    S.sel = f0 ? f0.id : (open[0] || pend[0] || closed[0] || cands[0] || {}).id || null;   // #chart/BTC -> that coin's trade
    app.innerHTML = '<div class="xlayout"><aside class="card tlist" id="tlist"><input class="search" id="lq" placeholder="جستجوی کوین…" value="' + esc(S.lq) + '">' +
      '<div class="seg lseg" id="lk"></div><div id="litems"></div></aside>' +
      '<div class="xmain"><section class="xview" id="tview"></section>' +
      '<section class="xbottom"><div class="xtabs" id="xtabs"></div><div class="scroll" id="xtable"></div></section>' +
      '<section id="tinfo"></section></div></div>';
    document.getElementById("lq").addEventListener("input", function (e) { S.lq = e.target.value.trim().toUpperCase(); fillList(); });
    document.getElementById("lk").addEventListener("click", function (e) { var b = e.target.closest("button"); if (b) { S.lk = b.dataset.v; fillList(); } });
    fillList();
    renderBottom();
    tradeView(findSel(S.sel));
  }
  function fillList() {
    var el = document.getElementById("litems"); if (!el) return;
    var open = openTrades().sort(byCreated), pend = pendingTrades();
    var closed = closedTrades().filter(function (t) { return t.filled_at; }).sort(function (a, b) { return (b.closed_at || 0) - (a.closed_at || 0); });
    var cands = candidates();
    var G = [["open", "باز", open, function (t) { return RU(openR(t), pnlUsd(t)); }],
             ["pend", "در انتظار", pend, function (t) { return '<small>' + esc(t.grade) + "</small>"; }],
             ["cand", "کاندید", cands, function (t) { return '<small class="ltr">' + (t.score || 0).toFixed(0) + "</small>"; }],
             ["closed", "بسته", closed.slice(0, 80), function (t) { return RU(t.net_r, pnlUsd(t)); }]];
    document.getElementById("lk").innerHTML = [["", "همه"]].concat(G.map(function (g) { return [g[0], g[1]]; })).map(function (o) {
      return '<button data-v="' + o[0] + '" class="' + (S.lk === o[0] ? "on" : "") + '">' + o[1] + "</button>";
    }).join("");
    function item(t, r) {
      var lp = S.live[t.base];
      return '<div class="titem ' + (t.id === S.sel ? "sel" : "") + '" data-open="' + esc(t.id) + '"><span class="c">' + esc(t.base) +
        '<small class="ltr">' + (lp ? px(lp.price) : "") + "</small></span>" + side(t.side) +
        ' <small>' + esc(sysShort(t.system)) + (t.cand ? "" : " · " + (t.approved ? "تأیید" : "Watch")) + '</small><span class="r">' + r + "</span></div>";
    }
    el.innerHTML = G.filter(function (g) { return !S.lk || S.lk === g[0]; }).map(function (g) {
      var list = g[2].filter(function (t) { return !S.lq || t.base.indexOf(S.lq) >= 0; });
      if (!list.length && g[0] !== "open") return "";
      var titles = {open: "پوزیشن‌های باز (", pend: "سفارش در انتظار (", cand: "کاندیدهای نزدیک به سیگنال", closed: "معاملات بسته ("};
      return "<h4>" + titles[g[0]] + (g[0] === "cand" ? "" : fa(list.length) + ")") + "</h4>" +
        (list.map(function (t) { return item(t, g[3](t)); }).join("") || '<small class="muted" style="padding:6px">—</small>');
    }).join("");
    decorateTips(el);
  }
  function renderBottom() {
    var tabs = document.getElementById("xtabs"); if (!tabs) return;
    var open = openTrades().sort(byCreated), pend = pendingTrades().sort(byCreated);
    var closed = closedTrades().filter(function (t) { return t.filled_at; }).sort(function (a, b) { return (b.closed_at || 0) - (a.closed_at || 0); }).slice(0, 40);
    var openUsd = sumOf(open, function (t) { return floatUsd(t); });
    tabs.innerHTML = [["open", "پوزیشن‌های باز", open.length], ["pend", "سفارش‌های در انتظار", pend.length], ["closed", "معاملات بسته‌ی اخیر", null]].map(function (b) {
      return '<button data-x="' + b[0] + '" class="' + (S.xtab === b[0] ? "on" : "") + '">' + b[1] + (b[2] != null ? " <small>(" + fa(b[2]) + ")</small>" : "") + "</button>";
    }).join("") + (open.length ? '<span class="xsum">سود/زیان باز: <span id="xsum">' + usd(openUsd) + "</span></span>" : "");
    tabs.onclick = function (e) { var b = e.target.closest("button"); if (b) { S.xtab = b.dataset.x; renderBottom(); } };
    var el = document.getElementById("xtable"), rows, head, foot = "";
    function sym(t) { return '<b>' + esc(t.base) + '</b><small class="muted">/USDT · ' + esc(sysShort(t.system)) + "</small>"; }
    if (S.xtab === "open") {
      head = ["نماد", "جهت", "نوع", "اهرم", "ورود", "قیمت لحظه‌ای", "استاپ / TP1", "حجم", "دلار ورودی", "دلار خروجی", "سود/زیان", "سود٪", "زمان ورود"];
      rows = open.map(function (t) {
        return '<tr class="click ' + (t.id === S.sel ? "sel" : "") + '" data-open="' + esc(t.id) + '"><td>' + sym(t) + "</td><td>" + side(t.side) + "</td><td>" + kind(t) +
          "</td>" + levCell(t) + '<td class="num">' + px(t.entry) + '</td><td class="num xp">' + px(livePrice(t)) + '</td><td class="num"><span class="neg">' + px(t.sl_now) +
          '</span> / <span class="pos">' + px(t.tp1) + "</span></td><td>" + usd(tSize(t) == null ? null : tSize(t) * (t.open_frac == null ? 1 : t.open_frac), false) + "</td>" +
          usdCell(marginUsd(t)) + usdCell(outUsd(t), "xo") + '<td class="xu">' + pnlCell(t) + "</td>" + pctCell(t, true) + tsCell(t.filled_at) + "</tr>";
      });
      foot = totRow([{span: 7, v: "جمع " + fa(open.length) + " پوزیشن"}, usd(sumOf(open, function (t) { return tSize(t) == null ? null : tSize(t) * (t.open_frac == null ? 1 : t.open_frac); }), false),
        usd(sumOf(open, function (t) { return marginUsd(t); }), false), '<span id="xosum">' + usd(sumOf(open, function (t) { return outUsd(t); }), false) + "</span>", posTot(open), "", ""]);
    } else if (S.xtab === "pend") {
      head = ["نماد", "جهت", "نوع", "اهرم", "ورود", "قیمت لحظه‌ای", "فاصله تا ورود", "استاپ", "TP1", "ریسک / سود در TP1", "رده", "زمان سیگنال"];
      rows = pend.map(function (t) {
        var p = livePrice(t), d = p && t.entry ? (t.entry - p) / p * 100 : null;
        return '<tr class="click" data-open="' + esc(t.id) + '"><td>' + sym(t) + "</td><td>" + side(t.side) + "</td><td>" + kind(t) + "</td>" + levCell(t) + '<td class="num">' + px(t.entry) +
          '</td><td class="num xp">' + px(p) + "</td><td>" + pct(d, 2) + '</td><td class="num neg">' + px(t.sl) + '</td><td class="num pos">' + px(t.tp1) +
          "</td><td>" + (tRisk(t) != null ? usd(-tRisk(t)) + " / " + (t.tp1 && t.entry !== t.sl ? usd(tRisk(t) * Math.abs(t.tp1 - t.entry) / Math.abs(t.entry - t.sl)) : "—") : "—") +
          "</td><td>" + esc(t.grade) + "</td>" + tsCell(t.created) + "</tr>";
      });
      foot = totRow([{span: 9, v: "جمع " + fa(pend.length) + " سفارش"}, usd(-sumOf(pend, function (t) { return tRisk(t); })) + ' <small class="muted">ریسک کل اگر همه پر شوند</small>', {span: 2, v: ""}]);
    } else {
      head = ["نماد", "جهت", "نوع", "اهرم", "ورود", "خروج", "نتیجه", "دلار ورودی", "دلار خروجی", "سود/زیان", "سود٪", "مدت", "زمان بسته شدن"];
      rows = closed.map(function (t) {
        var ex = (t.events || []).filter(function (e) { return e[2] != null && e[0] !== "filled"; }).pop();
        var h = t.closed_at && t.filled_at ? (t.closed_at - t.filled_at) / 3600000 : null;
        return '<tr class="click" data-open="' + esc(t.id) + '"><td>' + sym(t) + "</td><td>" + side(t.side) + "</td><td>" + kind(t) + "</td>" + levCell(t) + '<td class="num">' + px(t.entry) +
          '</td><td class="num">' + px(ex ? ex[2] : null) + "</td><td>" + esc(t.status_fa) + "</td>" + usdCell(marginUsd(t)) + usdCell(outUsd(t)) + "<td>" + usd(pnlUsd(t)) + " " + R(t.net_r) + "</td>" + pctCell(t) +
          '<td data-v="' + (h == null ? "" : h) + '">' + (h == null ? "—" : dur(h)) + "</td>" + tsCell(t.closed_at) + "</tr>";
      });
      foot = totRow([{span: 7, v: "جمع " + fa(closed.length) + " معامله‌ی اخیر"}, usd(sumOf(closed, function (t) { return marginUsd(t); }), false),
        usd(sumOf(closed, function (t) { return outUsd(t); }), false), usd(sumOf(closed, pnlUsd)) + " " + R(sumOf(closed, function (t) { return t.net_r; })), "", {span: 2, v: ""}]);
    }
    el.innerHTML = rows.length ? "<table class=\"xt\"" + (S.xtab === "open" ? ' data-defsort="11:desc"' : "") + "><thead><tr>" + head.map(function (h) { return "<th>" + h + "</th>"; }).join("") + "</tr></thead><tbody>" + rows.join("") + "</tbody>" + foot + "</table>"
      : '<div class="empty">' + (S.xtab === "open" ? "الان پوزیشن بازی نیست." : S.xtab === "pend" ? "سفارشی در انتظار نیست." : "هنوز معامله‌ی بسته‌ای نیست.") + "</div>";
    decorateTips(el.parentNode);
  }
  function pnlCell(t) {
    return usd(pnlUsd(t)) + " " + R(openR(t)) + splitNote(t);
  }
  function liveX(base) {                     // 24h numbers: from the live ticker, else from the last build
    var l = S.live[base], d = deriv(base) || {};
    return Object.assign({}, d, l && l.x ? l.x : {});
  }
  function tickerBar(t, p) {
    var x = liveX(t.base);
    function cell(label, v, id) { return '<div class="xc"><span class="l">' + label + '</span><span class="v"' + (id ? ' id="' + id + '"' : "") + ">" + v + "</span></div>"; }
    return '<div class="xhead"><div class="xsym"><span class="coin">' + esc(t.base) + '<small>/USDT</small></span><small class="muted">قرارداد دائمی</small></div>' +
      '<div class="xpx"><span class="px ltr" id="tv-px">' + px(p) + '</span><span id="tv-chg">' + pct(x.change, 2) + "</span></div>" +
      cell("بیشترین ۲۴ ساعت", '<span class="ltr">' + px(x.high) + "</span>", "tv-hi") + cell("کمترین ۲۴ ساعت", '<span class="ltr">' + px(x.low) + "</span>", "tv-lo") +
      cell("حجم ۲۴ ساعت", musd(x.vol_usd), "tv-vol") + cell("فاندینگ", fpct(x.funding != null ? x.funding * 100 : null, 4), "tv-fund") +
      (x.oi_usd ? cell("اوپن‌اینترست", musd(x.oi_usd) + " " + fpct(x.oi_change, 1)) : "") + "</div>" +
      '<div class="xbadges">' + side(t.side) + (t.cand ? '<span class="badge gray">کاندید</span>' : kind(t) + '<span class="badge gray">' + esc(t.status_fa) + "</span>") +
      ' <small>' + esc(t.system) + " · رده " + esc(t.grade || "—") + ' · امتیاز <span class="ltr">' + (t.score || 0).toFixed(1) + "</span></small>" +
      '<span class="xpnl" id="tv-pnl">' + headPnl(t) + "</span></div>";
  }
  function headPnl(t) {
    if (t.cand) return "";
    if (t.open && t.filled_at) return "سود/زیان باز: " + RU(openR(t), pnlUsd(t)) + (isPartial(t) ? ' <small class="muted">(قطعی ' + usd(bookedUsd(t)) + " + باز " + usd(floatUsd(t)) + ")</small>" : "");
    if (!t.open && t.filled_at) return "نتیجه: " + RU(t.net_r, pnlUsd(t));
    return "";
  }
  function tradeView(t) {
    var box = document.getElementById("tview"), info = document.getElementById("tinfo");
    if (!t) { box.innerHTML = '<div class="empty">معامله‌ای برای نمایش نیست.</div>'; info.innerHTML = ""; return; }
    var p = livePrice(t);
    box.innerHTML = tickerBar(t, p) +
      '<div class="xtool"><div class="seg tfs" id="tfs"></div><div class="seg ind" id="ind">' +
      MAS.map(function (m) { return '<button data-i="' + m[0] + '" class="' + (IND[m[0]] ? "on" : "") + '"><i style="background:var(' + m[2] + ')"></i>MA' + m[1] + "</button>"; }).join("") +
      '<button data-i="vol" class="' + (IND.vol ? "on" : "") + '">حجم</button></div><span class="ohlc ltr" id="ohlc"></span></div>' +
      '<div class="chartbox tall" id="tv-chart"><div class="empty">در حال بارگذاری نمودار…</div></div>' +
      '<div class="legend"><span><i style="background:' + cssv("--acc") + '"></i>ورود</span><span><i style="background:' + cssv("--neg") + '"></i>استاپ</span>' +
      '<span><i style="background:' + cssv("--pos") + '"></i>تارگت‌ها</span><small>فلش‌ها: زمان سیگنال، ورود و خروج‌ها · کندل آخر با قیمت زنده جلو می‌رود</small></div>';
    info.innerHTML = levels(t, p) + markBox(t) + aiBox(t) + '<div class="grid g2 tools"><div>' + derivBox(t.base, t.side) + excBox(t) + "</div><div>" + calcBox(t) + "</div></div>" + detail(t);
    wireCalc(t); wireMarks(t); decorateTips(box); decorateTips(info);
    document.getElementById("ind").onclick = function (e) {
      var b = e.target.closest("button"); if (!b) return;
      var k = b.dataset.i; IND[k] = IND[k] ? 0 : 1; b.classList.toggle("on", !!IND[k]);
      try { localStorage.setItem("ind", JSON.stringify(IND)); } catch (er) {}
      if (current && current.base === t.base) current.showInd();
    };
    loadCandles(t.base).then(function (data) {
      var el = document.getElementById("tv-chart");
      if (!el || S.sel !== t.id) return;
      data = data || {};
      var tf = Object.keys(data).length ? pickTf(t, data) : (S.tf || (t.open ? "5m" : "1h"));
      document.getElementById("tfs").innerHTML = TFS.map(function (x) {
        return '<button data-tf="' + x[0] + '" class="' + (x[0] === tf ? "on" : "") + '">' + x[1] + "</button>";
      }).join("");
      document.getElementById("tfs").onclick = function (e) {
        var b = e.target.closest("button"); if (!b) return;
        S.tf = b.dataset.tf; tradeView(t);
      };
      var rows = data[tf] || [];
      if (rows.length) { el.innerHTML = ""; drawTrade(el, t, rows, tf); }
      freshCandles(t.base, tf).then(function (fr) {           // the exchange's latest candles over the stored ones
        if (!fr || !fr.length || S.sel !== t.id || !document.getElementById("tv-chart")) return;
        var merged = mergeRows(rows, fr);
        if (candleCache[t.base]) candleCache[t.base][tf] = merged;
        if (current && current.base === t.base && current.tf === tf) current.apply(merged);
        else { el.innerHTML = ""; drawTrade(el, t, merged, tf); }
      });
      if (!rows.length) el.innerHTML = '<div class="empty">در حال گرفتن کندل‌ها از صرافی…</div>';
    });
  }
  var OKX_TF = {"5m": "5m", "1h": "1H", "4h": "4H", "1d": "1Dutc"};
  function freshCandles(base, tf) {
    var g = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=" + encodeURIComponent(base) + "_USDT&interval=" + tf + "&limit=500";
    return fetch(g).then(function (r) { if (!r.ok) throw 0; return r.json(); }).then(function (j) {
      if (!Array.isArray(j) || !j.length) throw 0;
      return j.map(function (k) { return [Number(k.t), +k.o, +k.h, +k.l, +k.c, +(k.sum || k.v || 0)]; });
    }).catch(function () {
      return fetch("https://www.okx.com/api/v5/market/candles?instId=" + encodeURIComponent(base) + "-USDT-SWAP&bar=" + OKX_TF[tf] + "&limit=300")
        .then(function (r) { return r.json(); }).then(function (j) {
          return (j.data || []).map(function (k) { return [Math.floor(+k[0] / 1000), +k[1], +k[2], +k[3], +k[4], +(k[7] || k[6] || 0)]; }).reverse();
        });
    }).catch(function () { return null; });
  }
  function mergeRows(old, fresh) {
    if (!fresh || !fresh.length) return old;
    var byT = {}, ratios = [];
    old.forEach(function (r) { byT[r[0]] = r; });
    fresh.forEach(function (r) { var o = byT[r[0]]; if (o && o[5] > 0 && r[5] > 0) ratios.push(o[5] / r[5]); });
    ratios.sort(function (a, b) { return a - b; });
    var k = ratios.length ? ratios[Math.floor(ratios.length / 2)] : 1;   // keep one volume unit
    var t0 = fresh[0][0];
    return old.filter(function (r) { return r[0] < t0; }).concat(fresh.map(function (r) { return [r[0], r[1], r[2], r[3], r[4], r[5] * k]; }));
  }
  function sma(rows, n) {
    var out = [], s = 0;
    for (var i = 0; i < rows.length; i++) {
      s += rows[i][4]; if (i >= n) s -= rows[i - n][4];
      if (i >= n - 1) out.push({time: rows[i][0], value: s / n});
    }
    return out;
  }
  var current = null;                        // the candle series on screen (live updates)
  function drawTrade(el, t, rows, tf) {
    var c = makeChart(el, {rightPriceScale: {scaleMargins: {top: 0.08, bottom: 0.22}}});
    if (!c) return;
    c.applyOptions({crosshair: {mode: 0}});
    var up = cssv("--pos"), dn = cssv("--neg");
    var cs = c.addCandlestickSeries({upColor: up, downColor: dn, borderUpColor: up, borderDownColor: dn, wickUpColor: up, wickDownColor: dn,
      priceFormat: {type: "custom", formatter: px, minMove: 1e-9}});
    var vol = c.addHistogramSeries({priceScaleId: "vol", priceFormat: {type: "volume"}, lastValueVisible: false, priceLineVisible: false});
    c.priceScale("vol").applyOptions({scaleMargins: {top: 0.82, bottom: 0}});
    var mas = MAS.map(function (m) {
      return c.addLineSeries({color: cssv(m[2]), lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false});
    });
    function apply(rs) {
      cs.setData(rs.map(function (r) { return {time: r[0], open: r[1], high: r[2], low: r[3], close: r[4]}; }));
      vol.setData(rs.map(function (r) { return {time: r[0], value: r[5] || 0, color: r[4] >= r[1] ? hexA(up, 0.35) : hexA(dn, 0.35)}; }));
      mas.forEach(function (s, i) { s.setData(sma(rs, MAS[i][1])); });
      var lb = rs[rs.length - 1];
      if (current && current.series === cs) { current.rows = rs; current.last = {time: lb[0], open: lb[1], high: lb[2], low: lb[3], close: lb[4]}; }
      ohlc(null);
    }
    function showInd() { mas.forEach(function (s, i) { s.applyOptions({visible: !!IND[MAS[i][0]]}); }); vol.applyOptions({visible: !!IND.vol}); }
    function ohlc(bar) {
      var o = document.getElementById("ohlc"); if (!o || !current) return;
      var b = bar || current.last; if (!b) return;
      var ch = b.open ? (b.close - b.open) / b.open * 100 : 0;
      o.innerHTML = "O <b>" + px(b.open) + "</b> H <b>" + px(b.high) + "</b> L <b>" + px(b.low) + "</b> C <b class=\"" + cls(ch) + "\">" + px(b.close) + "</b> " +
        '<b class="' + cls(ch) + '">' + (ch > 0 ? "+" : "") + ch.toFixed(2) + "%</b>";
    }
    function line(price, color, title, style) {
      if (price == null) return;
      cs.createPriceLine({price: price, color: color, lineWidth: 2, lineStyle: style || 0, axisLabelVisible: true, title: title});
    }
    line(t.entry, cssv("--acc"), "ورود");
    line(t.sl, dn, "استاپ", 2);
    if (t.sl_now != null && Math.abs(t.sl_now - t.sl) > 1e-12) line(t.sl_now, dn, "استاپ الان", 0);
    line(t.tp1, up, "TP1", 2);
    line(t.tp2, up, "TP2", 2);
    // keep the entry, the current stop and TP1 on screen, even when price has moved far away
    var keep = [t.entry, t.sl_now != null ? t.sl_now : t.sl, t.tp1].filter(function (v) { return v != null && isFinite(v) && v > 0; });
    if (keep.length) cs.applyOptions({autoscaleInfoProvider: function (orig) {
      var r = orig(); if (!r || !r.priceRange) return r;
      return {priceRange: {minValue: Math.min.apply(null, keep.concat([r.priceRange.minValue])),
        maxValue: Math.max.apply(null, keep.concat([r.priceRange.maxValue]))}, margins: r.margins};
    }});
    var lb0 = rows[rows.length - 1];
    current = {series: cs, base: t.base, tf: tf, step: TFSEC[tf] || 3600, rows: rows, apply: apply, showInd: showInd, ohlc: ohlc,
      last: {time: lb0[0], open: lb0[1], high: lb0[2], low: lb0[3], close: lb0[4]}};
    apply(rows);
    showInd();
    var step = TFSEC[tf] || 3600, first = rows[0][0], last = lb0[0];
    function snap(ms) { var s = Math.floor(tsec(ms) / step) * step; return s >= first && s <= last + step ? Math.min(s, last) : null; }
    var long = t.side > 0, marks = [];
    function mark(ms, text, color, pos, shape) { var s = snap(ms); if (s != null) marks.push({time: s, position: pos, color: color, shape: shape, text: text}); }
    if (t.cand) mark(t.created || D.now, "کاندید", cssv("--warn"), long ? "belowBar" : "aboveBar", long ? "arrowUp" : "arrowDown");
    else {
      mark(t.created, "سیگنال", cssv("--muted"), long ? "belowBar" : "aboveBar", "circle");
      if (t.filled_at) mark(t.filled_at, "ورود", cssv("--acc"), long ? "belowBar" : "aboveBar", long ? "arrowUp" : "arrowDown");
      var EV = {tp1: ["TP1", up], tp2: ["TP2", up], tp3: ["خروج با سود", up], sl: ["استاپ", dn], breakeven: ["سر به سر", cssv("--muted")],
        expired: ["منقضی", cssv("--muted")], cancelled: ["لغو", cssv("--muted")]};
      (t.events || []).forEach(function (e) {
        var m = EV[e[0]]; if (!m) return;
        mark(e[1], m[0], m[1], long ? "aboveBar" : "belowBar", long ? "arrowDown" : "arrowUp");
      });
    }
    marks.sort(function (a, b) { return a.time - b.time; });
    cs.setMarkers(marks);
    c.subscribeCrosshairMove(function (p) { ohlc(p && p.time && p.seriesData ? p.seriesData.get(cs) : null); });
    // show the trade: from a little before the signal to its end (or now)
    var from = t.created ? snap(t.created) : null, to = t.closed_at ? snap(t.closed_at) : last;
    if (from != null) {
      var pad = step * 40;
      c.timeScale().setVisibleRange({from: Math.max(first, from - pad), to: Math.min(last, (to || last) + step * 25)});
    } else c.timeScale().fitContent();
  }
  function candidates() {
    var out = [];
    (D.systems || []).forEach(function (s) {
      (s.candidates || []).slice(0, 12).forEach(function (c) {
        if (!c.plan || c.plan.entry == null) return;
        out.push({id: "cand-" + sysShort(s.name) + "-" + c.base, cand: true, system: s.name, base: c.base, side: c.side,
          grade: c.grade, score: c.score, entry: c.plan.entry, sl: c.plan.sl, sl_now: c.plan.sl, tp1: c.plan.tp1,
          tp2: c.plan.tp2, price: c.price, why: c.why, item: c, created: s.evaluated_at, events: [], status: "candidate"});
      });
    });
    return out;
  }
  function findSel(id) {
    if (!id) return null;
    if (id.indexOf("-") < 0) id = latestForBase(id.toUpperCase()) || id;   // #chart/BTC from a Telegram link
    return byId(id) || candidates().filter(function (c) { return c.id === id; })[0] || null;
  }
  function loadCandles(base) {
    if (candleCache[base]) return Promise.resolve(candleCache[base]);
    var path = (D.candles || {})[base];
    if (!path) return Promise.resolve(null);
    return fetch(path + "?v=" + D.now).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { candleCache[base] = j; return j; }).catch(function () { return null; });
  }
  var TFS = [["5m", "۵ دقیقه"], ["1h", "۱ ساعت"], ["4h", "۴ ساعت"], ["1d", "روزانه"]];
  var TFSEC = {"5m": 300, "1h": 3600, "4h": 14400, "1d": 86400};
  function pickTf(t, data) {
    if (S.tf && data[S.tf]) return S.tf;
    var c = tsec(t.created || D.now);
    if (t.open && data["5m"] && data["5m"].length && data["5m"][0][0] <= c) return "5m";
    if (data["1h"] && data["1h"].length && data["1h"][0][0] <= c - 3600 * 12) return "1h";
    if (data["4h"]) return "4h";
    return Object.keys(data)[0];
  }
  function levels(t, p) {
    var rk = t.entry != null && t.sl != null ? Math.abs(t.entry - t.sl) : 0;
    function lv(label, v, ref, money) {
      var d = (ref && v != null && p) ? (v - p) / p * 100 : null;
      var u = money && rk && tRisk(t) != null && v != null ? tRisk(t) * (t.open_frac == null ? 1 : t.open_frac) * t.side * (v - t.entry) / rk : null;
      return '<div class="lv"><span class="l">' + label + '</span><span class="v">' + px(v) + "</span>" +
        (d != null ? '<span class="d ' + cls(d * (t.side || 1)) + '">' + (d > 0 ? "+" : "") + d.toFixed(2) + "% از قیمت</span>" : "") +
        (u != null ? '<span class="d">' + usd(u) + " اگر برسد</span>" : "") + "</div>";
    }
    var risk = tRisk(t), r = t.cand ? null : (t.open ? openR(t) : t.net_r);
    var money = "";
    if (!t.cand) {
      money = '<div class="lv"><span class="l">ریسک (حساب ' + (t.approved ? "تأییدشده" : "Watch") + ')</span><span class="v">' + usd(risk, false) + "</span></div>" +
        '<div class="lv"><span class="l">حجم پوزیشن</span><span class="v">' + usd(tSize(t), false) + "</span></div>" +
        '<div class="lv"><span class="l">' + (t.open ? "سود/زیان باز" : "سود/زیان") + '</span><span class="v">' +
        usd(t.open ? (risk != null && r != null ? risk * r : null) : tPnl(t)) + "</span></div>" +
        '<div class="lv"><span class="l">درصد ریسک / فاصله‌ی استاپ</span><span class="v">' + (t.risk_pct || 0).toFixed(2) + "% / " + (t.sl_pct || 0).toFixed(2) + "%</span></div>";
    }
    return '<div class="levels">' + lv("قیمت الان", p) + lv("ورود", t.entry, 1) + lv(t.open ? "استاپ الان" : "استاپ", t.open ? t.sl_now : t.sl, 1, t.open) +
      lv("TP1", t.tp1, 1, t.open) + lv("TP2", t.tp2, 1, t.open) + money + "</div>";
  }
  var SEC = {phase: "فاز", dow: "ساختار داو", levels: "سطوح", volume: "حجم", candles: "کندل", cycles: "سیکل", patterns: "الگو و روند", confirmation: "تأیید ورود"};
  var GATES = {regime: "رژیم بازار", phase: "فاز", rr: "نسبت سود به ضرر", confirmation: "تأیید ورود"};
  function sections(sec) {
    var keys = Object.keys(sec || {});
    if (!keys.length) return "";
    return '<div class="bars">' + keys.map(function (k) {
      var v = sec[k] || 0;
      return '<div class="brow"><span>' + esc(SEC[k] || k) + '</span><span class="track"><i style="width:' + Math.min(100, v / 25 * 100).toFixed(0) + '%"></i></span><b class="ltr">' + v.toFixed(1) + "</b></div>";
    }).join("") + "</div>";
  }
  function gates(g) {
    return Object.keys(g || {}).map(function (k) {
      return '<span class="chip">' + (g[k] ? '<b class="pos">✓</b>' : '<b class="neg">✗</b>') + esc(GATES[k] || k) + "</span>";
    }).join("");
  }
  var EVFA = {filled: "ورود (پر شدن سفارش)", tp1: "TP1 گرفت", tp2: "TP2 گرفت", tp3: "خروج نهایی با سود", sl: "استاپ خورد", breakeven: "بسته شد سر به سر",
    expired: "سفارش منقضی شد", cancelled: "سفارش لغو شد", trail: "استاپ جابه‌جا شد", sl_moved: "استاپ جابه‌جا شد"};
  function detail(t) {
    var ev = (t.detail || {}).eval || (t.item ? t.item : null) || {};
    var plan = (t.detail || {}).plan || (t.item ? t.item.plan : null) || {};
    var tl = "";
    if (!t.cand) {
      var items = [["سیگنال صادر شد", t.created, ""]];
      if (t.filled_at && !(t.events || []).some(function (e) { return e[0] === "filled"; })) items.push(["ورود", t.filled_at, ""]);
      (t.events || []).forEach(function (e) {
        var good = /tp/.test(e[0]), bad = e[0] === "sl";
        items.push([(EVFA[e[0]] || e[0]) + (e[2] != null ? ' <span class="ltr">@ ' + px(e[2]) + "</span>" : ""), e[1], good ? "good" : bad ? "bad" : ""]);
      });
      items.sort(function (a, b) { return a[1] - b[1]; });
      tl = '<h3>رویدادها</h3><ul class="timeline">' + items.map(function (x) {
        return '<li class="' + x[2] + '">' + x[0] + '<br><small>' + esc(jfmt(x[1], "full")) + "</small></li>";
      }).join("") + "</ul>";
      if (t.source === "replay") tl += '<p class="muted">این معامله با بازسازی روی داده‌ی تاریخی ساخته شده است (قبل از شروع ثبت زنده‌ی Watchها).</p>';
    }
    var why = (t.why || []).length ? '<h3>' + (t.cand ? "چرا هنوز سیگنال نیست" : "یادداشت‌های سیستم") + "</h3>" + t.why.map(function (w) { return '<span class="chip">' + esc(w) + "</span>"; }).join("") : "";
    var info = [];
    var d = t.detail || {};
    if (d.regime) info.push(["رژیم هنگام سیگنال", (REG[d.regime] || d.regime) + (d.bias ? " (" + (BIAS[d.bias] || d.bias) + ")" : "")]);
    if (ev.phase_d) info.push(["فاز روزانه / ۴ ساعته", ev.phase_d + " / " + (ev.phase_4h || "—")]);
    if (ev.cycle) info.push(["سیکل", ev.cycle]);
    if (ev.trigger) info.push(["کندل ماشه", ev.trigger + (ev.trigger_1h ? " / " + ev.trigger_1h : "")]);
    if (plan.support) info.push(["سطح", px(plan.support) + " · " + (plan.support_kind || "") + " " + (plan.support_tf || "") + (plan.support_touches ? " · " + plan.support_touches + " برخورد" : "")]);
    if (plan.tp1_r) info.push(["R تارگت‌ها", plan.tp1_r.toFixed(1) + "R / " + (plan.tp2_r || 0).toFixed(1) + "R"]);
    if (ev.funding_pct != null) info.push(["فاندینگ", ev.funding_pct + "%"]);
    if (ev.news) info.push(["اخبار", (ev.news.points > 0 ? "+" : "") + ev.news.points + " امتیاز · " + (ev.news.top || []).map(function (x) { return faT(x[0]); }).join(" | ")]);
    if (t.leverage) info.push(["اهرم پیشنهادی", t.leverage + "x"]);
    var conf = (ev.confirmations || []).concat(ev.labels || []);
    var right = (ev.sections ? "<h3>امتیاز بخش‌ها</h3>" + sections(ev.sections) : "") +
      (ev.gates ? "<h3>دروازه‌ها</h3>" + gates(ev.gates) : "") +
      (conf.length ? "<h3>تأییدها و برچسب‌ها</h3>" + conf.map(function (x) { return '<span class="chip">' + esc(x) + "</span>"; }).join("") : "");
    var left = (info.length ? '<h3>جزئیات</h3><table><tbody>' + info.map(function (x) { return "<tr><td class=\"muted\">" + esc(x[0]) + "</td><td>" + esc(x[1]) + "</td></tr>"; }).join("") + "</tbody></table>" : "") + why + tl;
    if (!left && !right) return "";
    return '<div class="grid g2" style="margin-top:6px"><div>' + left + "</div><div>" + (right || '<p class="muted">جزئیات امتیاز برای این معامله ذخیره نشده است.</p>') + "</div></div>";
  }

  // ------------------------------------------------------------------ history
  function histTab() {
    var f = S.hist;
    var names = uniqList(trades().map(function (t) { return t.system; }));
    function seg(g, opts) {
      return '<div class="seg" data-g="' + g + '">' + opts.map(function (o) { return '<button data-v="' + esc(o[0]) + '" class="' + (f[g] === o[0] ? "on" : "") + '">' + esc(o[1]) + "</button>"; }).join("") + "</div>";
    }
    app.innerHTML = '<section><h2>تاریخچه‌ی معاملات</h2><div class="filters">' +
      seg("k", [["", "همه"], ["ok", "تأییدشده"], ["w", "Watch"]]) +
      seg("s", [["", "همه‌ی سیستم‌ها"]].concat(names.map(function (n) { return [n, n]; }))) +
      seg("side", [["", "هر دو جهت"], ["1", "لانگ"], ["-1", "شورت"]]) +
      seg("res", [["", "همه‌ی نتایج"], ["win", "سود"], ["loss", "ضرر"], ["part", "بخشی بسته"], ["none", "بدون ورود"]]) +
      '<input class="search" id="q" placeholder="جستجوی کوین…" value="' + esc(f.q) + '"></div><div id="hsum" class="muted"></div><div class="scroll" id="htab"></div></section>';
    app.querySelectorAll(".seg").forEach(function (sg) {
      sg.addEventListener("click", function (e) {
        var b = e.target.closest("button"); if (!b) return;
        f[sg.dataset.g] = b.dataset.v;
        sg.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
        histTable();
      });
    });
    document.getElementById("q").addEventListener("input", function (e) { f.q = e.target.value.trim().toUpperCase(); histTable(); });
    histTable();
  }
  function uniqList(a) { return a.filter(function (x, i) { return a.indexOf(x) === i; }).sort(); }
  var HCOLS = [["created", "سیگنال"], ["closed", "بسته شدن"], ["kind", "نوع"], ["system", "سیستم"], ["base", "کوین"], ["side", "جهت"], ["lev", "اهرم"],
    ["score", "رده / امتیاز"], ["status", "نتیجه"], ["hold", "مدت"], ["in", "دلار ورودی"], ["out", "دلار خروجی"], ["own", "سود/زیان"], ["pct", "سود٪"], ["net", "R خالص"]];
  // history rows show the banked result, also for partly closed trades (net_r so far)
  function hpct(t) { return t.filled_at && t.sl_pct && t.net_r != null ? t.net_r * t.sl_pct * (t.leverage || 1) : null; }
  function hval(t, k) {
    var hp = hpct(t);
    return {created: t.created, closed: t.closed_at || t.created, kind: t.approved ? 1 : 0, system: t.system, base: t.base, side: t.side, lev: t.leverage || 0, pct: hp == null ? -1e9 : hp,
      score: t.score, status: t.status, hold: t.closed_at && t.filled_at ? t.closed_at - t.filled_at : -1,
      in: marginUsd(t) == null ? -1e9 : marginUsd(t), net: t.filled_at ? t.net_r : -99,
      own: doneUsd(t) == null ? -1e9 : doneUsd(t), out: outUsd(t) == null ? -1e9 : outUsd(t)}[k];
  }
  function histTable() {
    var f = S.hist;
    // 2026-11-15 (Ali: "closed positions missing from history"): a trade past TP1/TP2 has most of
    // it closed with profit and only the rest still trailing, so it is listed too, as partly closed
    var rows = closedTrades().concat(partlyClosed()).filter(function (t) {
      if (f.res === "part" && !t.open) return false;
      if (f.k && (f.k === "ok") !== t.approved) return false;
      if (f.s && t.system !== f.s) return false;
      if (f.side && String(t.side) !== f.side) return false;
      if (f.res === "win" && !(t.filled_at && t.net_r > 0)) return false;
      if (f.res === "loss" && !(t.filled_at && t.net_r <= 0)) return false;
      if (f.res === "none" && t.filled_at) return false;
      if (f.q && t.base.toUpperCase().indexOf(f.q) < 0) return false;
      return true;
    });
    rows.sort(function (a, b) {
      var x = hval(a, f.sort), y = hval(b, f.sort);
      var c = typeof x === "string" ? x.localeCompare(y) : x - y;
      return f.dir === "asc" ? c : -c;
    });
    var filled = rows.filter(function (t) { return t.filled_at; });
    var wins = filled.filter(function (t) { return t.net_r > 0; }).length;
    var sumR = filled.reduce(function (s, t) { return s + (t.net_r || 0); }, 0);
    function rowUsd(t) { return doneUsd(t); }     // partly closed: profit banked so far
    var sumP = sumOf(rows, rowUsd);
    var nPart = rows.filter(function (t) { return t.open; }).length;
    document.getElementById("hsum").innerHTML = plural(rows.length - nPart, "معامله‌ی کامل بسته") + (nPart ? " + " + fa(nPart) + " بخشی بسته" : "") + " · برد " + (filled.length ? fa(Math.round(wins / filled.length * 100)) + "٪" : "—") +
      " · مجموع " + R(sumR) + " · " + usd(sumP) + ' <small>(روی هر ردیف کلیک کنید تا روی نمودار ببینید)</small>';
    if (!rows.length) { document.getElementById("htab").innerHTML = '<div class="empty">هنوز معامله‌ی بسته‌شده‌ای نیست.</div>'; return; }
    var head = "<tr>" + HCOLS.map(function (c) {
      return '<th class="sort" data-k="' + c[0] + '"' + (f.sort === c[0] ? ' data-dir="' + f.dir + '"' : "") + ">" + c[1] + "</th>";
    }).join("") + "</tr>";
    var body = rows.map(function (t) {
      var hold = t.closed_at && t.filled_at ? dur((t.closed_at - t.filled_at) / 3600000) : "—";
      var part = t.open, left = Math.round((t.open_frac == null ? 1 : t.open_frac) * 100);
      return '<tr class="click' + (part ? " part" : "") + '" data-open="' + esc(t.id) + '"><td>' + esc(jfmt(t.created)) + "</td><td>" +
        (part ? '<span class="badge watch">هنوز ' + fa(left) + "٪ باز</span>" : esc(jfmt(t.closed_at))) + "</td><td>" + kind(t) +
        "</td><td>" + esc(sysShort(t.system)) + "</td><td><b>" + esc(t.base) + "</b></td><td>" + side(t.side) + "</td>" + levCell(t) + "<td>" + esc(t.grade) +
        ' <small class="ltr">' + (t.score || 0).toFixed(0) + "</small></td><td>" + (part ? "بخشی بسته · " : "") + esc(t.status_fa) + "</td><td>" + hold + "</td><td>" +
        usd(marginUsd(t), false) + "</td><td>" +
        usd(outUsd(t), false) + (part && outUsd(t) != null ? '<br><small class="muted">اگر باقی‌مانده الان بسته شود</small>' : "") + "</td><td>" +
        usd(doneUsd(t)) + (part && t.filled_at && floatUsd(t) != null ? '<br><small class="muted">+ باز ' + usd(floatUsd(t)) + " = " + usd(pnlUsd(t)) + "</small>" : "") + "</td><td>" +
        (hpct(t) == null ? "—" : pct(hpct(t), 1)) + "</td><td>" + R(t.filled_at ? t.net_r : null) + "</td></tr>";
    }).join("");
    var el = document.getElementById("htab");
    var fr = rows.filter(function (t) { return t.filled_at; });
    var foot = totRow([{span: 10, v: "جمع " + fa(rows.length) + " معامله"}, usd(sumOf(fr, function (t) { return marginUsd(t); }), false),
      usd(sumOf(fr, function (t) { return outUsd(t); }), false), usd(sumP), "", R(sumOf(fr, function (t) { return t.net_r; }))]);
    el.innerHTML = "<table><thead>" + head + "</thead><tbody>" + body + "</tbody>" + foot + "</table>";
    el.querySelectorAll("th.sort").forEach(function (th) {
      th.addEventListener("click", function () {
        var k = th.dataset.k;
        if (f.sort === k) f.dir = f.dir === "asc" ? "desc" : "asc"; else { f.sort = k; f.dir = "desc"; }
        histTable();
      });
    });
  }

  // ------------------------------------------------------------------ compare (2026-11-15: rebuilt, all numbers in R and $)
  S.cmp = {u: "usd"};
  function cmpCols() {
    var names = uniqList(trades().map(function (t) { return t.system; })), cols = [];
    names.forEach(function (n) {
      [["ok", "تأییدشده"], ["w", "Watch"]].forEach(function (k) {
        cols.push({sys: n, k: k[0], label: sysShort(n) + " · " + k[1], list: trades().filter(function (t) { return t.system === n && (k[0] === "ok") === !!t.approved; })});
      });
    });
    return cols;
  }
  function cmpStats(list) {
    var closed = doneTrades(list);
    var st = stability(closed) || {}, g = groupStats(closed);
    var open = list.filter(function (t) { return t.open && t.filled_at; });
    function wr(l) { return l.length ? l.filter(function (t) { return t.net_r > 0; }).length / l.length * 100 : null; }
    var holds = closed.filter(function (t) { return !t.open && t.closed_at; }).map(function (t) { return (t.closed_at - t.filled_at) / 3600000; });
    var own = 0, ownCurve = [], rCurve = [], cr = 0;
    closed.forEach(function (t) { own += doneUsd(t) || 0; cr += t.net_r; ownCurve.push([doneTs(t), own]); rCurve.push([doneTs(t), cr]); });
    var rs = closed.map(function (t) { return t.net_r; });
    return {signals: list.length, closed: closed.length, unfilled: list.filter(function (t) { return !t.open && !t.filled_at; }).length,
      own: closed.length ? own : null, all: sumOf(closed, function (t) { return doneUsd(t, true); }),
      sum: g.sum, avg: g.avg, win: g.win, pf: g.pf, sqn: st.sqn, dd: st.dd, maxL: st.maxL, aw: st.aw, al: st.al, payoff: st.payoff,
      best: rs.length ? Math.max.apply(null, rs) : null, worst: rs.length ? Math.min.apply(null, rs) : null,
      hold: holds.length ? holds.reduce(function (a, b) { return a + b; }, 0) / holds.length : null,
      lwin: wr(closed.filter(function (t) { return t.side > 0; })), swin: wr(closed.filter(function (t) { return t.side < 0; })),
      longs: closed.filter(function (t) { return t.side > 0; }).length, shorts: closed.filter(function (t) { return t.side < 0; }).length,
      open: open.length, openUsd: sumOf(open, function (t) { return floatUsd(t, true); }),
      ownOpen: sumOf(open, function (t) { return floatUsd(t); }),
      rs: rs, closedList: closed, ownCurve: ownCurve, rCurve: rCurve};
  }
  var CMPROWS = [
    ["g", "نتیجه"],
    ["own", "سود/زیان دلاری (حساب خودش)", 1, function (v) { return usd(v); }],
    ["all", "سود/زیان دلاری (حساب همه)", 1, function (v) { return usd(v); }],
    ["sum", "جمع R خالص", 1, function (v) { return R(v); }],
    ["avg", "انتظار ریاضی (میانگین R)", 1, function (v) { return R(v); }],
    ["win", "نرخ برد", 1, function (v) { return v == null ? "—" : fa(Math.round(v)) + "٪"; }],
    ["pf", "ضریب سود", 1, function (v) { return v == null ? "—" : '<span class="ltr">' + v.toFixed(2) + "</span>"; }],
    ["sqn", "SQN", 1, function (v) { return v == null ? "—" : '<span class="ltr">' + v.toFixed(2) + "</span>"; }],
    ["g", "ریسک"],
    ["dd", "بیشترین افت (R)", -1, function (v) { return v == null ? "—" : '<span class="ltr">' + v.toFixed(2) + "R</span>"; }],
    ["maxL", "بیشترین ضرر پشت‌سرهم", -1, function (v) { return v == null ? "—" : fa(v); }],
    ["worst", "بدترین معامله", 1, function (v) { return R(v); }],
    ["best", "بهترین معامله", 1, function (v) { return R(v); }],
    ["aw", "میانگین برد", 1, function (v) { return R(v); }],
    ["al", "میانگین باخت", 1, function (v) { return R(v); }],
    ["payoff", "نسبت میانگین برد به باخت", 1, function (v) { return v == null ? "—" : '<span class="ltr">' + v.toFixed(2) + "</span>"; }],
    ["g", "رفتار"],
    ["signals", "تعداد سیگنال", 0, function (v) { return fa(v); }],
    ["closed", "معامله‌ی بسته", 0, function (v) { return fa(v); }],
    ["unfilled", "بدون ورود", 0, function (v) { return fa(v); }],
    ["lwin", "نرخ برد لانگ", 1, function (v, s) { return (v == null ? "—" : fa(Math.round(v)) + "٪") + " <small>(" + fa(s.longs) + ")</small>"; }],
    ["swin", "نرخ برد شورت", 1, function (v, s) { return (v == null ? "—" : fa(Math.round(v)) + "٪") + " <small>(" + fa(s.shorts) + ")</small>"; }],
    ["hold", "میانگین مدت معامله", 0, function (v) { return dur(v); }],
    ["open", "پوزیشن باز", 0, function (v) { return fa(v); }],
    ["ownOpen", "سود/زیان باز (حساب خودش)", 1, function (v) { return usd(v); }]];
  var CMP_COLORS = ["--acc", "--warn", "--acc2", "--pos"];
  function compare() {
    var cols = cmpCols();
    if (!cols.length) { app.innerHTML = '<section><div class="empty">داده‌ای برای مقایسه نیست.</div></section>'; return; }
    cols.forEach(function (c) { c.s = cmpStats(c.list); });
    var systems = uniqList(cols.map(function (c) { return c.sys; }));
    // 2026-11-16 (Ali): each system's approved signals and its Watch setups side by side, never mixed
    function half(c, title, acct) {
      var s = c.s;
      return '<div class="khalf ' + c.k + '"><div class="kht"><b>' + title + '</b><small>' + fa(s.closed) + " نتیجه‌دار · " + fa(s.open) + " باز</small></div>" +
        '<div class="kbig">' + usd(s.own) + " <small>" + acct + "</small></div>" +
        '<div class="kgrid">' + statCard("جمع R", R(s.sum), "") + statCard("نرخ برد", s.win == null ? "—" : fa(Math.round(s.win)) + "٪", "") +
        statCard("ضریب سود", s.pf == null ? "—" : '<span class="ltr">' + s.pf.toFixed(2) + "</span>", "") +
        statCard("سود/زیان باز", usd(s.ownOpen), "") + "</div></div>";
    }
    var kpis = systems.map(function (n) {
      var ok = cols.filter(function (c) { return c.sys === n && c.k === "ok"; })[0];
      var w = cols.filter(function (c) { return c.sys === n && c.k === "w"; })[0];
      var all = cmpStats(trades().filter(function (t) { return t.system === n; }));
      return '<div class="kpi"><div class="kh"><b>' + esc(n) + "</b><small>هر دو با هم در حساب «همه با هم»: " + usd(all.all) + " · " + R(all.sum) + "</small></div>" +
        '<div class="ksplit">' + half(ok, "تأییدشده (ارسال به تلگرام)", "حساب تأییدشده") + half(w, "Watch (ارسال‌نشده)", "حساب Watch") + "</div></div>";
    }).join("");
    var head = "<tr><th></th>" + cols.map(function (c, i) { return '<th><i class="sw" style="background:var(' + CMP_COLORS[i % 4] + ')"></i>' + esc(c.label) + "</th>"; }).join("") + "</tr>";
    var body = CMPROWS.map(function (m) {
      if (m[0] === "g") return '<tr class="grp"><td colspan="' + (cols.length + 1) + '">' + m[1] + "</td></tr>";
      var vals = cols.map(function (c) { return c.s[m[0]]; });
      var nums = vals.filter(function (v) { return typeof v === "number"; });
      var best = m[2] && nums.length > 1 ? (m[2] > 0 ? Math.max.apply(null, nums) : Math.min.apply(null, nums)) : null;
      var worst = m[2] && nums.length > 1 ? (m[2] > 0 ? Math.min.apply(null, nums) : Math.max.apply(null, nums)) : null;
      return '<tr><td class="muted">' + m[1] + "</td>" + cols.map(function (c, i) {
        var v = vals[i];
        var cl = best != null && best !== worst && v === best ? "best" : worst != null && best !== worst && v === worst ? "worst" : "";
        return '<td class="' + cl + '">' + m[3](v, c.s) + "</td>";
      }).join("") + "</tr>";
    }).join("");
    app.innerHTML = '<section><h2>خلاصه‌ی سیستم‌ها <small>از ' + esc(jfmt(D.start, "ymd")) + "، بعد از هزینه‌ها</small></h2><div class=\"grid g2\">" + kpis + "</div></section>" +
      '<section><h2>منحنی سود تجمعی</h2><div class="filters"><div class="seg" id="cu">' +
      [["usd", "دلار (حساب خودش)"], ["r", "R"]].map(function (o) { return '<button data-v="' + o[0] + '" class="' + (S.cmp.u === o[0] ? "on" : "") + '">' + o[1] + "</button>"; }).join("") +
      '</div></div><div class="chartbox" id="cmp-chart"></div><div class="legend" id="cmp-leg"></div></section>' +
      '<section><h2>جدول مقایسه <small>سبز بهترین و قرمز بدترین عدد هر ردیف</small></h2><div class="scroll"><table class="cmp"><thead>' + head + "</thead><tbody>" + body + "</tbody></table></div></section>" +
      '<div class="grid g2"><section><h2>پخش نتیجه‌ها <small>درصد معامله‌ها در هر بازه‌ی R</small></h2>' + rHist(cols) + "</section>" +
      "<section><h2>نتیجه‌ی روزانه <small>جمع دلاری حساب خودش · R</small></h2>" + dailyTable(cols) + "</section></div>";
    document.getElementById("cu").onclick = function (e) { var b = e.target.closest("button"); if (b) { S.cmp.u = b.dataset.v; render(); } };
    var c = makeChart(document.getElementById("cmp-chart"));
    if (!c) return;
    var leg = document.getElementById("cmp-leg"), isUsd = S.cmp.u === "usd";
    c.applyOptions({localization: {priceFormatter: function (v) { return isUsd ? "$" + v.toFixed(2) : v.toFixed(2) + "R"; }}});
    cols.forEach(function (col, i) {
      var curve = isUsd ? col.s.ownCurve : col.s.rCurve;
      var pts = uniq([{time: tsec(D.start), value: 0}].concat(curve.map(function (p) { return {time: tsec(p[0]), value: p[1]}; })));
      var color = cssv(CMP_COLORS[i % 4]);
      var s = c.addLineSeries({color: color, lineWidth: 2, lineStyle: col.k === "w" ? 2 : 0,
        priceFormat: {type: "custom", formatter: function (v) { return isUsd ? "$" + v.toFixed(2) : v.toFixed(2) + "R"; }}, priceLineVisible: false});
      s.setData(pts);
      if (i === 0) s.createPriceLine({price: 0, color: cssv("--muted"), lineStyle: 2, lineWidth: 1, axisLabelVisible: false});
      var span = document.createElement("span");
      var end = curve.length ? curve[curve.length - 1][1] : 0;
      span.innerHTML = '<i style="background:' + color + '"></i>' + esc(col.label) + ": " + (isUsd ? usd(end) : R(end));
      span.addEventListener("click", function () { var on = span.classList.toggle("off"); s.applyOptions({visible: !on}); });
      leg.appendChild(span);
    });
    c.timeScale().fitContent();
  }
  var RB = [[-1e9, -1, "≤ -1R"], [-1, 0, "-1R … 0"], [0, 1, "0 … 1R"], [1, 2, "1R … 2R"], [2, 3, "2R … 3R"], [3, 1e9, "≥ 3R"]];
  function rHist(cols) {
    var W = 640, H = 230, L = 34, B = 34, T = 10, gw = (W - L - 8) / RB.length, bw = Math.min(22, (gw - 10) / cols.length);
    var data = cols.map(function (c) {
      var n = c.s.rs.length;
      return RB.map(function (b) { return n ? c.s.rs.filter(function (r) { return r > b[0] && r <= b[1]; }).length / n * 100 : 0; });
    });
    var mx = Math.max(10, Math.max.apply(null, data.map(function (d) { return Math.max.apply(null, d); })));
    function Y(v) { return T + (1 - v / mx) * (H - T - B); }
    var g = "";
    [0, 25, 50, 75, 100].filter(function (v) { return v <= mx; }).forEach(function (v) {
      g += '<line x1="' + L + '" x2="' + (W - 8) + '" y1="' + Y(v) + '" y2="' + Y(v) + '" class="gl"/><text x="' + (L - 4) + '" y="' + (Y(v) + 4) + '" class="ax" text-anchor="end">' + v + "%</text>";
    });
    RB.forEach(function (b, j) {
      var x0 = L + j * gw + (gw - bw * cols.length) / 2;
      cols.forEach(function (c, i) {
        var v = data[i][j];
        g += '<rect x="' + (x0 + i * bw).toFixed(1) + '" y="' + Y(v).toFixed(1) + '" width="' + (bw - 2).toFixed(1) + '" height="' + (Y(0) - Y(v)).toFixed(1) +
          '" rx="2" style="fill:var(' + CMP_COLORS[i % 4] + ')"><title>' + esc(c.label + " · " + b[2] + " · " + v.toFixed(0) + "%") + "</title></rect>";
      });
      g += '<text x="' + (L + j * gw + gw / 2) + '" y="' + (H - B + 16) + '" class="ax" text-anchor="middle">' + b[2] + "</text>";
    });
    return '<svg class="scatter" viewBox="0 0 ' + W + " " + H + '" direction="ltr">' + g + "</svg>" +
      '<div class="legend">' + cols.map(function (c, i) { return '<span><i style="background:var(' + CMP_COLORS[i % 4] + ')"></i>' + esc(c.label) + " <small>(" + fa(c.s.rs.length) + ")</small></span>"; }).join("") + "</div>";
  }
  function dailyTable(cols) {
    var days = {}, order = [];
    cols.forEach(function (c, i) {
      c.s.closedList.forEach(function (t) {
        var ts = doneTs(t), k = jfmt(ts, "date");
        if (!days[k]) { days[k] = {ts: ts, v: {}}; order.push(k); }
        var d = days[k].v[i] = days[k].v[i] || {u: 0, r: 0, n: 0};
        d.u += doneUsd(t) || 0; d.r += t.net_r; d.n++;
        days[k].ts = Math.max(days[k].ts, ts);
      });
    });
    order.sort(function (a, b) { return days[b].ts - days[a].ts; });
    if (!order.length) return '<div class="empty">—</div>';
    var mx = 0.5;
    order.forEach(function (k) { Object.keys(days[k].v).forEach(function (i) { mx = Math.max(mx, Math.abs(days[k].v[i].r)); }); });
    return '<div class="scroll"><table class="daily"><thead><tr><th>روز</th>' + cols.map(function (c) { return "<th>" + esc(c.label) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      order.slice(0, 21).map(function (k) {
        return '<tr><td data-v="' + days[k].ts + '">' + esc(k) + "</td>" + cols.map(function (c, i) {
          var d = days[k].v[i];
          if (!d) return '<td class="muted">—</td>';
          var a = Math.min(0.55, Math.abs(d.r) / mx * 0.55);
          return '<td style="background:' + hexA(cssv(d.r >= 0 ? "--pos" : "--neg"), a) + '">' + usd(d.u) + '<br><small class="ltr">' + (d.r > 0 ? "+" : "") + d.r.toFixed(2) + "R · " + fa(d.n) + "</small></td>";
        }).join("") + "</tr>";
      }).join("") + "</tbody>" + totRow(["جمع همه‌ی روزها"].concat(cols.map(function (c, i) {
        var u = 0, r = 0, n = 0;
        order.forEach(function (k) { var d = days[k].v[i]; if (d) { u += d.u; r += d.r; n += d.n; } });
        return n ? usd(u) + '<br><small class="ltr">' + (r > 0 ? "+" : "") + r.toFixed(2) + "R · " + fa(n) + "</small>" : "—";
      }))) + "</table></div>";
  }

  // ------------------------------------------------------------------ news (2026-11-15: every headline, 1-5 stars, the measured move after it)
  S.news = {lv: "", st: "", src: "", coin: false, q: "", n: 80};
  var LVFA = {"2": "خیلی مثبت", "1": "مثبت", "0": "خنثی", "-1": "منفی", "-2": "خیلی منفی"};
  function stars(n) { return '<span class="stars s' + n + '" title="' + fa(n) + ' از ۵">' + "★".repeat(n) + '<i>' + "★".repeat(5 - n) + "</i></span>"; }
  function lvBadge(l) { return '<span class="badge ' + (l > 0 ? "long" : l < 0 ? "short" : "gray") + '">' + LVFA[String(l)] + "</span>"; }
  function newsMove(m) {
    if (!m) return '<span class="muted">—</span>';
    return ["1h", "4h", "24h"].map(function (k) { return '<span class="mv"><small>' + {"1h": "۱س", "4h": "۴س", "24h": "۲۴س"}[k] + "</small>" + (m[k] == null ? "—" : pct(m[k], 1)) + "</span>"; }).join("");
  }
  function news() {
    var NV = D.news_view;
    if (!NV) {
      var n0 = D.news || [];
      app.innerHTML = "<section><h2>هشدارهای خبری</h2>" + (n0.length ? n0.map(function (x) { return "<div>" + trT(x.title) + "</div>"; }).join("") : '<div class="empty">—</div>') + "</section>";
      return;
    }
    var f = S.news, items = NV.items || [];
    var list = items.filter(function (x) {
      if (f.lv === "pos" && x.level <= 0) return false;
      if (f.lv === "neg" && x.level >= 0) return false;
      if (f.lv === "neu" && x.level !== 0) return false;
      if (f.st && x.stars < Number(f.st)) return false;
      if (f.src && x.kind !== f.src) return false;
      if (f.coin && !(x.coins || []).length) return false;
      if (f.q && x.title.toUpperCase().indexOf(f.q) < 0 && faT(x.title).indexOf(f.q) < 0 && (x.coins || []).join(" ").indexOf(f.q) < 0) return false;
      return true;
    });
    var day = Date.now() - 86400000;
    var last24 = items.filter(function (x) { return x.ts > day; });
    var imp = items.filter(function (x) { return x.stars >= 3; });
    var pos = items.filter(function (x) { return x.level > 0; }).length, neg = items.filter(function (x) { return x.level < 0; }).length;
    var coins = {};
    items.forEach(function (x) { (x.coins || []).forEach(function (c) { var o = coins[c] = coins[c] || {n: 0, s: 0, best: 0}; o.n++; o.s += x.level; o.best = Math.max(o.best, x.stars); }); });
    var topCoins = Object.keys(coins).sort(function (a, b) { return coins[b].n - coins[a].n; }).slice(0, 24);
    function seg(g, opts) {
      return '<div class="seg" data-g="' + g + '">' + opts.map(function (o) { return '<button data-v="' + o[0] + '" class="' + (String(f[g]) === o[0] ? "on" : "") + '">' + o[1] + "</button>"; }).join("") + "</div>";
    }
    var rows = list.slice(0, f.n).map(function (x) {
      var tfa = faT(x.title), title = x.url ? '<a href="' + esc(x.url) + '" target="_blank" rel="noopener noreferrer">' + esc(tfa) + "</a>" : esc(tfa);
      if (tfa !== x.title) title += '<div class="nen">' + esc(x.title) + "</div>";
      var c0 = (x.coins || [])[0];
      return '<div class="nitem lv' + (x.level > 0 ? "p" : x.level < 0 ? "n" : "0") + '"><div class="nh">' + stars(x.stars) + lvBadge(x.level) +
        (x.rule_fa ? '<span class="chip">' + esc(x.rule_fa) + "</span>" : "") + (x.alerted ? '<span class="badge acc">ارسال‌شده به تلگرام</span>' : "") +
        '<small class="muted">' + esc(x.source) + " · " + esc(ago(x.ts)) + " · " + esc(jfmt(x.ts, "dt")) + "</small></div>" +
        '<div class="nt">' + title + "</div>" +
        ((x.coins || []).length ? '<div class="nc">' + x.coins.map(function (c) {
          return '<span class="chip click" data-coin="' + esc(c) + '"><b>' + esc(c) + "</b></span>";
        }).join("") + (c0 && x.moves && x.moves[c0] ? '<span class="moves">حرکت ' + esc(c0) + " بعد از خبر: " + newsMove(x.moves[c0]) + "</span>" : "") + "</div>" : "") + "</div>";
    }).join("");
    var rl = NV.rules || [], rn = sumOf(rl, function (r) { return r.n; });
    function wavg(k) { var w = 0, s = 0; rl.forEach(function (r) { if (r[k] != null) { w += r.n; s += r[k] * r.n; } }); return w ? s / w : null; }
    var rulesFoot = rl.length ? totRow(["<b>همه‌ی خبرها</b>", "", fa(rn), pct(wavg("avg_1h"), 2), pct(wavg("avg_4h"), 2), pct(wavg("avg_24h"), 2),
      wavg("hit") == null ? "—" : fa(Math.round(wavg("hit"))) + "٪"]) : "";
    var rules = (NV.rules || []).map(function (r) {
      return '<tr class="' + (r.n < 5 ? "thin" : "") + '"><td><b>' + esc(r.fa) + "</b></td><td>" + lvBadge(r.level) + "</td><td>" + fa(r.n) + "</td><td>" + pct(r.avg_1h, 2) +
        "</td><td>" + pct(r.avg_4h, 2) + "</td><td>" + pct(r.avg_24h, 2) + "</td><td>" + (r.hit == null ? "—" : fa(r.hit) + "٪") + "</td></tr>";
    }).join("");
    app.innerHTML = '<section><h2>خلاصه‌ی خبرها <small>' + fa(NV.days) + " روز گذشته</small></h2><div class=\"grid g4\">" +
      statCard("خبر در ۲۴ ساعت", fa(last24.length), fa(items.length) + " خبر در " + fa(NV.days) + " روز") +
      statCard("خبرهای مهم (۳ ستاره و بیشتر)", fa(imp.length), fa(imp.filter(function (x) { return x.ts > day; }).length) + " در ۲۴ ساعت") +
      statCard("مثبت / منفی", '<span class="pos">' + fa(pos) + '</span> / <span class="neg">' + fa(neg) + "</span>", "بقیه خنثی") +
      statCard("ارسال‌شده به تلگرام", fa(items.filter(function (x) { return x.alerted; }).length), "") + "</div>" + newsDays(items) + "</section>" +
      '<div class="grid g2"><section><h2>اثر واقعی هر نوع خبر <small>' + fa(NV.stats_days) + ' روز · حرکت قیمت در جهتی که خبر انتظار داشت</small></h2>' +
      (rules ? '<div class="scroll"><table><thead><tr><th>نوع خبر</th><th>جهت</th><th>تعداد</th><th>۱ ساعت</th><th>۴ ساعت</th><th>۲۴ ساعت</th><th>درست از آب درآمد</th></tr></thead><tbody>' + rules + "</tbody>" + rulesFoot + "</table></div>" +
        '<p class="muted">عدد مثبت یعنی قیمت در همان جهتی رفت که خبر انتظار داشت (برای خبر منفی، ریزش). ردیف‌های کم‌رنگ کمتر از ۵ نمونه دارند.</p>' : '<div class="empty">هنوز قیمت بعد از خبرها ثبت نشده است.</div>') + "</section>" +
      '<section><h2>کوین‌های پرخبر</h2>' + (topCoins.map(function (c) {
        var o = coins[c], tone = o.s > 0 ? "pos" : o.s < 0 ? "neg" : "";
        return '<span class="chip click" data-coin="' + esc(c) + '"><b class="' + tone + '">' + esc(c) + "</b> " + fa(o.n) + "</span>";
      }).join("") || '<div class="empty">—</div>') + '<p class="muted">رنگ سبز یعنی جمع خبرهای آن کوین مثبت بوده و قرمز یعنی منفی. روی هر کوین بزنید تا خبرهایش فیلتر شود.</p></section></div>' +
      '<section><h2>همه‌ی خبرها <small>' + fa(list.length) + " خبر</small></h2><div class=\"filters\">" +
      seg("lv", [["", "همه"], ["pos", "مثبت"], ["neg", "منفی"], ["neu", "خنثی"]]) +
      seg("st", [["", "همه‌ی ستاره‌ها"], ["2", "★۲+"], ["3", "★۳+"], ["4", "★۴+"]]) +
      seg("src", [["", "همه‌ی منابع"], ["exchange", "اعلان صرافی"], ["media", "رسانه"]]) +
      '<label class="chk"><input type="checkbox" id="ncoin"' + (f.coin ? " checked" : "") + "> فقط خبرهای کوین‌دار</label>" +
      '<input class="search" id="nq" placeholder="جستجوی کوین یا متن…" value="' + esc(f.q) + '"></div>' +
      (rows || '<div class="empty">خبری با این فیلتر نیست.</div>') +
      (list.length > f.n ? '<div style="text-align:center;margin-top:10px"><button class="btn" id="nmore">نمایش خبرهای بیشتر (' + fa(list.length - f.n) + ")</button></div>" : "") + "</section>";
    app.querySelectorAll(".seg[data-g]").forEach(function (sg) {
      sg.addEventListener("click", function (e) { var b = e.target.closest("button"); if (!b) return; f[sg.dataset.g] = b.dataset.v; f.n = 80; news(); decorateTips(); });
    });
    document.getElementById("ncoin").onchange = function (e) { f.coin = e.target.checked; news(); decorateTips(); };
    var nq = document.getElementById("nq");
    nq.addEventListener("change", function (e) { f.q = e.target.value.trim().toUpperCase(); news(); decorateTips(); });
    var more = document.getElementById("nmore");
    if (more) more.onclick = function () { f.n += 120; news(); decorateTips(); };
    app.querySelectorAll("[data-coin]").forEach(function (el) {
      el.addEventListener("click", function () { f.q = el.getAttribute("data-coin"); f.n = 80; news(); decorateTips(); document.getElementById("nq").scrollIntoView({block: "center"}); });
    });
  }
  function newsDays(items) {                 // headlines per day: positive up, negative down
    var days = {}, keys = [];
    items.forEach(function (x) {
      var k = jfmt(x.ts, "date");
      if (!days[k]) { days[k] = {ts: x.ts, p: 0, n: 0, z: 0}; keys.push(k); }
      if (x.level > 0) days[k].p++; else if (x.level < 0) days[k].n++; else days[k].z++;
    });
    keys.sort(function (a, b) { return days[a].ts - days[b].ts; });
    if (keys.length < 2) return "";
    var W = 1200, H = 170, L = 6, T = 8, B = 26, gw = (W - L * 2) / keys.length, bw = Math.max(4, Math.min(56, gw * 0.5));
    var mx = Math.max(1, Math.max.apply(null, keys.map(function (k) { return Math.max(days[k].p, days[k].n); })));
    var mid = T + (H - T - B) / 2, hh = (H - T - B) / 2;
    var g = '<line x1="' + L + '" x2="' + (W - L) + '" y1="' + mid + '" y2="' + mid + '" class="gl"/>';
    keys.forEach(function (k, i) {
      var d = days[k], x = L + i * gw + (gw - bw) / 2, hp = d.p / mx * hh, hn = d.n / mx * hh;
      g += '<rect x="' + x.toFixed(1) + '" y="' + (mid - hp).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + hp.toFixed(1) + '" rx="2" style="fill:var(--pos)"><title>' + esc(k) + " · مثبت " + d.p + "</title></rect>" +
        '<rect x="' + x.toFixed(1) + '" y="' + mid + '" width="' + bw.toFixed(1) + '" height="' + hn.toFixed(1) + '" rx="2" style="fill:var(--neg)"><title>' + esc(k) + " · منفی " + d.n + "</title></rect>";
      if (i % Math.ceil(keys.length / 8) === 0) g += '<text x="' + (x + bw / 2) + '" y="' + (H - 6) + '" class="ax" text-anchor="middle">' + esc(k) + "</text>";
    });
    return '<h3>خبرهای مثبت و منفی هر روز</h3><svg class="scatter" viewBox="0 0 ' + W + " " + H + '" direction="ltr">' + g + "</svg>";
  }

  // ------------------------------------------------------------------ market
  function gauge(label, v, max) {
    if (v == null) return "";
    var w = Math.min(50, Math.abs(v) / max * 50);
    var st = v >= 0 ? "left:50%;width:" + w + "%;background:var(--pos)" : "left:" + (50 - w) + "%;width:" + w + "%;background:var(--neg)";
    return '<div class="gauge"><span style="min-width:70px">' + label + '</span><span class="track"><i style="' + st + '"></i></span><b class="ltr ' + cls(v) + '" style="min-width:28px">' + v + "</b></div>";
  }
  function scoreBar(v) {
    var g = v >= 75 ? "a" : v >= 65 ? "b" : v >= 55 ? "w" : "";
    return '<span class="score ' + g + '" title="Watch از ۵۵ · B از ۶۵ · A از ۷۵"><i style="width:' + Math.min(100, v || 0) + '%"></i>' +
      '<b style="right:55%"></b><b style="right:65%"></b><b style="right:75%"></b></span>';
  }
  // what the regime and the majors say to do now, in the market's own direction (2026-11-15, Ali:
  // "when everything is bearish, suggest shorts; why talk about longs?")
  function marketTake(s) {
    var r = s.regime || {}, m = s.majors || {};
    var trend = r.bias === "short" ? -1 : r.bias === "long" ? 1 : (r.trend || 0);
    var maj = (m.btc || 0) + (m.eth || 0), dir = trend || (maj <= -4 ? -1 : maj >= 4 ? 1 : 0);
    var side = function (d) { return d > 0 ? "لانگ" : "شورت"; };
    var best = (s.candidates || []).filter(function (c) { return dir && c.side === dir; }).slice(0, 4).map(function (c) {
      return '<a href="#chart/' + esc(c.base) + '">' + esc(c.base) + "</a> <small>" + fa(Math.round(c.score || 0)) + "</small>";
    });
    var lines = [];
    if (!dir) lines.push("جهت بازار روشن نیست: هر دو طرف ممکن است، ولی فقط ستاپ‌های قوی (A) ارزش ورود دارند.");
    else {
      var cls = dir > 0 ? "pos" : "neg";
      lines.push('<b class="' + cls + '">بازار ' + (dir > 0 ? "صعودی" : "نزولی") + " است: " + side(dir) + " در جهت روند است</b> و با ریسک کامل گرفته می‌شود.");
      if (r.bias === "both") lines.push(side(-dir) + " (خلاف روند) هم مجاز است، ولی با " + fa(Math.round((r.counter_risk || 0.5) * 100)) + "٪ ریسک.");
      else if (r.bias === "short" || r.bias === "long") lines.push("این سیستم الان فقط " + side(dir) + " می‌گیرد.");
      if ((dir < 0 && maj < 0) || (dir > 0 && maj > 0)) lines.push("BTC و ETH هم " + (dir > 0 ? "قوی" : "ضعیف") + " هستند و جهت را تأیید می‌کنند.");
      else if (maj) lines.push('<span class="warn">BTC و ETH با این جهت هم‌سو نیستند؛ احتیاط.</span>');
      lines.push(best.length ? "بهترین کاندیدهای " + side(dir) + ": " + best.join(" · ") : "فعلاً کاندیدای " + side(dir) + " نزدیک به سیگنال نیست.");
    }
    if (m.btc_weak && dir > 0) lines.push('<span class="warn">BTC ضعیف است؛ لانگ آلت‌ها امتیاز ۸۰ به بالا لازم دارند.</span>');
    return '<div class="take">' + lines.map(function (x) { return "<p>" + x + "</p>"; }).join("") + "</div>";
  }
  // ------------------------------------------------------------------ market (2026-11-17, Ali: «رژیم بازار» and «بازار» in one page,
  // nothing dropped, laid out so the pieces help one decision). Top: every market reading in one table with its
  // time frame and its vote, and what they say together; then where the market is, the last days, mood and
  // events, the setups closest to a signal, and how our trades did in each regime.
  // market pulse (2026-11-18, Ali): the large-coin basket over 1 h / 4 h / 24 h with its history, the
  // alarms, and what two rules would have done. Hypothetical only: no trade is changed by it.
  function pulseActual(t) {                   // [R, $] of a trade so far, as every other table shows it
    var r = t.open ? openR(t) : t.net_r, risk = tRisk(t);
    if (r == null) return [null, null];
    return [r, !t.open && tPnl(t) != null ? tPnl(t) : risk == null ? null : risk * r];
  }
  function pulseRows() {
    var out = [];
    trades().forEach(function (t) {
      var p = t.pulse; if (!p || !t.filled_at) return;
      var a = pulseActual(t), risk = tRisk(t);
      if (p.entry && p.entry[2] && a[1] != null)
        out.push({t: t, rule: "half", at: t.filled_at, h1: p.entry[0], h4: p.entry[1], r: a[0], usd: a[1], wr: a[0] / 2, wusd: a[1] / 2});
      if (p.alarm && a[1] != null && risk != null)
        out.push({t: t, rule: "exit", at: p.alarm.t, h1: p.alarm.h1, h4: p.alarm.h4, r: a[0], usd: a[1], wr: p.alarm.r, wusd: p.alarm.r * risk, px: p.alarm.price});
    });
    return out.sort(function (a, b) { return b.at - a.at; });
  }
  var PULSE_RULE = {half: "نصف ریسک در ورود", exit: "بستن با هشدار"};
  // 2026-11-18 (Ali): one panel per window (each on its own scale, so the lines don't sit on top of
  // each other), a shared cross-hair with the three numbers, and 1 / 3 / 7 day views
  var PULSE_WIN = [[3, "۲۴ ساعت", "h24_pct"], [2, "۴ ساعت", "h4_pct"], [1, "۱ ساعت", "h1_pct"]];
  function pulseChart(pl) {
    var days = S.pdays || 3, all = pl.series || [];
    var s = all.filter(function (x) { return x[0] >= all[all.length - 1][0] - days * 86400000; });
    if (s.length < 2) return '<div class="empty">هنوز داده‌ی کافی برای نمودار نیست.</div>';
    var W = Math.max(320, Math.min(960, (app.clientWidth || 960) - 44)), L = 10, Rr = 70, PH = W < 600 ? 80 : 92, GAP = 30, T0 = 8, XB = 28, Hh = T0 + 3 * (PH + GAP) + XB;   // drawn at the page's own width: text keeps its size on phones
    var x0 = s[0][0], x1 = s[s.length - 1][0];
    function X(t) { return L + (t - x0) / Math.max(1, x1 - x0) * (W - L - Rr); }
    function f1(v) { return v.toFixed(1); }
    var g = "<defs>", panels = [];
    PULSE_WIN.forEach(function (w, k) {
      var thr = pl.params[w[2]] || 0, top = T0 + GAP + k * (PH + GAP), bot = top + PH;
      var m = Math.max(thr * 1.5, Math.max.apply(null, s.map(function (x) { return Math.abs(x[w[0]]); })) * 1.08);
      function Y(v) { return top + (m - v) / (2 * m) * PH; }
      panels.push({i: w[0], name: w[1], thr: thr, top: top, bot: bot, Y: Y, m: m});
      g += '<clipPath id="pcu' + k + '"><rect x="0" y="' + top + '" width="' + W + '" height="' + f1(Y(0) - top) + '"/></clipPath>' +
        '<clipPath id="pcd' + k + '"><rect x="0" y="' + f1(Y(0)) + '" width="' + W + '" height="' + f1(bot - Y(0)) + '"/></clipPath>';
    });
    g += "</defs>";
    // alarm stretches, across the three panels
    (pl.spans || []).forEach(function (sp) {
      if (sp.to < x0) return;
      var a = Math.max(X(sp.from), L), b = Math.max(Math.min(X(sp.to + 300000), W - Rr), a + 2);
      g += '<rect x="' + f1(a) + '" y="' + (T0 + GAP - 4) + '" width="' + f1(b - a) + '" height="' + (3 * (PH + GAP) - GAP + 8) + '" class="pspan ' + (sp.dir > 0 ? "pos" : "neg") + '"/>';
    });
    // days
    for (var t = Math.ceil((x0 + 12600000) / 86400000) * 86400000 - 12600000; t <= x1; t += 86400000) {
      g += '<line x1="' + f1(X(t)) + '" x2="' + f1(X(t)) + '" y1="' + (T0 + GAP) + '" y2="' + (Hh - XB) + '" class="vl"/>' +
        '<text x="' + f1(X(t)) + '" y="' + (Hh - 8) + '" class="ax" text-anchor="middle">' + esc(jfmt(t, "date")) + "</text>";
    }
    if (days === 1) for (var h = Math.ceil(x0 / 21600000) * 21600000; h <= x1; h += 21600000)
      g += '<text x="' + f1(X(h)) + '" y="' + (Hh - 8) + '" class="ax" text-anchor="middle">' + esc(jfmt(h, "time")) + "</text>";
    panels.forEach(function (p, k) {
      var lv = s[s.length - 1][p.i], ly = Math.max(p.top + 9, Math.min(p.bot - 9, p.Y(lv)));
      g += '<text x="' + (W - Rr) + '" y="' + (p.top - 9) + '" class="ptitle" text-anchor="end" direction="rtl">' + p.name + "</text>";
      g += '<line x1="' + L + '" x2="' + (W - Rr) + '" y1="' + f1(p.Y(0)) + '" y2="' + f1(p.Y(0)) + '" class="zl"/>';
      [p.thr, -p.thr].forEach(function (v) {
        g += '<line x1="' + L + '" x2="' + (W - Rr) + '" y1="' + f1(p.Y(v)) + '" y2="' + f1(p.Y(v)) + '" class="pth"/>' +
          (Math.abs(p.Y(v) - ly) < 18 ? "" : '<text x="' + (W - Rr + 8) + '" y="' + f1(p.Y(v) + 4) + '" class="ax">' + (v > 0 ? "+" : "") + v + "%</text>");
      });
      if (p.m > p.thr * 2) [p.m, -p.m].forEach(function (v) {     // the panel's own scale, at its edges
        var y = p.Y(v) + (v > 0 ? 9 : -2);
        if (Math.abs(y - ly) > 16) g += '<text x="' + (W - Rr + 8) + '" y="' + f1(y) + '" class="ax pedge">' + (v > 0 ? "+" : "−") + Math.abs(v).toFixed(1) + "%</text>";
      });
      var line = s.map(function (x, j) { return (j ? "L" : "M") + f1(X(x[0])) + " " + f1(p.Y(x[p.i])); }).join(" ");
      var area = line + " L" + f1(X(x1)) + " " + f1(p.Y(0)) + " L" + f1(X(x0)) + " " + f1(p.Y(0)) + " Z";
      g += '<path d="' + area + '" class="parea pos" clip-path="url(#pcu' + k + ')"/><path d="' + area + '" class="parea neg" clip-path="url(#pcd' + k + ')"/>' +
        '<path d="' + line + '" class="pl"/>';
      g += '<rect x="' + (W - Rr + 4) + '" y="' + f1(ly - 10) + '" width="' + (Rr - 6) + '" height="20" rx="6" class="rpill ' + (lv >= 0 ? "pos" : "neg") + '"/>' +
        '<text x="' + (W - Rr / 2 + 1) + '" y="' + f1(ly + 4) + '" class="rpt" text-anchor="middle">' + (lv > 0 ? "+" : "") + lv.toFixed(2) + "%</text>";
    });
    g += '<line class="rx" x1="0" x2="0" y1="' + (T0 + GAP - 4) + '" y2="' + (Hh - XB) + '" style="display:none"/>' +
      panels.map(function (p, k) { return '<circle class="rxd pd' + k + '" r="4" cx="0" cy="0" style="display:none"/>'; }).join("") +
      '<rect class="rov" x="' + L + '" y="' + T0 + '" width="' + (W - L - Rr) + '" height="' + (Hh - T0 - XB) + '"/>';
    pulseChart.geo = {s: s, X: X, W: W, L: L, Rr: Rr, x0: x0, x1: x1, panels: panels, Hh: Hh};
    return '<div class="regwrap pulsewrap"><svg class="regchart pulsechart" viewBox="0 0 ' + W + " " + Hh + '" direction="ltr">' + g +
      '</svg><div class="regtip" style="display:none"></div></div>' +
      '<div class="legend reglegend"><span><i class="rrib pos"></i>هشدار برای شورت‌ها (بازار رو به بالا)</span><span><i class="rrib neg"></i>هشدار برای لانگ‌ها (بازار رو به پایین)</span>' +
      '<span><i class="pli pth"></i>آستانه‌ی هر بازه</span></div>';
  }
  function pulseBind() {
    var box = document.getElementById("pulsechart"); if (!box) return;
    box.parentNode.querySelectorAll("[data-pdays]").forEach(function (b) {
      b.addEventListener("click", function () {
        S.pdays = +b.dataset.pdays;
        try { localStorage.setItem("pdays", S.pdays); } catch (e) {}
        box.parentNode.querySelectorAll("[data-pdays]").forEach(function (x) { x.classList.toggle("on", x === b); });
        box.innerHTML = pulseChart(D.pulse || {}); pulseHover();
      });
    });
    pulseHover();
  }
  function pulseHover() {
    var wrap = document.querySelector("#pulsechart .regwrap"), geo = pulseChart.geo; if (!wrap || !geo) return;
    var svg = wrap.querySelector("svg"), tip = wrap.querySelector(".regtip"), ln = svg.querySelector(".rx"), c = (D.pulse || {}).params || {};
    function hide() { tip.style.display = ln.style.display = "none"; geo.panels.forEach(function (p, k) { svg.querySelector(".pd" + k).style.display = "none"; }); }
    function move(ev) {
      var r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) / r.width * geo.W;
      var t = geo.x0 + (sx - geo.L) / (geo.W - geo.L - geo.Rr) * (geo.x1 - geo.x0), best = null;
      geo.s.forEach(function (x) { if (!best || Math.abs(x[0] - t) < Math.abs(best[0] - t)) best = x; });
      if (!best) return hide();
      var cx = geo.X(best[0]);
      ln.setAttribute("x1", cx); ln.setAttribute("x2", cx); ln.style.display = "";
      geo.panels.forEach(function (p, k) { var d = svg.querySelector(".pd" + k); d.setAttribute("cx", cx); d.setAttribute("cy", p.Y(best[p.i])); d.style.display = ""; });
      var st = best[1] >= c.h1_pct && best[2] >= c.h4_pct ? 1 : best[1] <= -c.h1_pct && best[2] <= -c.h4_pct ? -1 : 0;
      tip.innerHTML = "<b>" + esc(jfmt(best[0])) + "</b><br>۲۴ ساعت " + pct(best[3], 2) + "<br>۴ ساعت " + pct(best[2], 2) + "<br>۱ ساعت " + pct(best[1], 2) +
        "<br>" + (st > 0 ? '<b class="neg">هشدار برای شورت‌ها</b>' : st < 0 ? '<b class="neg">هشدار برای لانگ‌ها</b>' : '<span class="muted">بدون هشدار</span>');
      tip.style.display = "";
      var px = cx / geo.W * r.width, w = tip.offsetWidth;
      tip.style.left = Math.max(4, Math.min(r.width - w - 4, px + (px > r.width / 2 ? -w - 14 : 14))) + "px";
      tip.style.top = "30px";
    }
    svg.addEventListener("mousemove", move);
    svg.addEventListener("touchmove", function (e) { if (e.touches[0]) move(e.touches[0]); }, {passive: true});
    svg.addEventListener("touchstart", function (e) { if (e.touches[0]) move(e.touches[0]); }, {passive: true});
    svg.addEventListener("mouseleave", hide);
  }
  // what the three windows together say, and what it means for each side (2026-11-18, Ali: «what can
  // we conclude and what should we do»). Advice only: the systems trade by their own rules.
  function pulseVerdict(pl, rows) {
    var n = pl.now, c = pl.params || {};
    if (!n) return '<div class="pverdict v-warn"><b>داده‌ی تازه نیست</b><p>آخرین ثبت نبض بازار بیشتر از نیم ساعت پیش است؛ جمع‌بندی وقتی بات دوباره ثبت کند برمی‌گردد.</p></div>';
    function sg(v, thr) { return v >= thr ? 1 : v <= -thr ? -1 : 0; }
    var d1 = sg(n[1], c.h1_pct), d4 = sg(n[2], c.h4_pct), d24 = sg(n[3], c.h24_pct || 2);
    var UP = {1: "بالا", "-1": "پایین"}, SIDE = {1: "لانگ‌ها", "-1": "شورت‌ها"};
    var title, why, adv = {}, cls = "v-warn", dir = 0;
    if (d4 && d1 === d4) {
      dir = d4;
      if (d24 === d4) {
        title = "حرکت یکپارچه رو به " + UP[dir]; cls = "v-neg";
        why = "هر سه بازه هم‌جهت‌اند: بازار در ۲۴ ساعت، ۴ ساعت و ۱ ساعت گذشته رو به " + UP[dir] + " رفته و هنوز ادامه دارد.";
        adv[dir] = "هم‌جهت با بازارند. فرصت خوب است، ولی دنبال قیمت نرو: بعد از یک حرکت بزرگ، ورود دیر با استاپ نزدیک زود می‌خورد.";
        adv[-dir] = "در بیشترین خطرند. همان حالتی است که استاپ‌ها با هم می‌خورند: ورود جدید نه، و پوزیشن‌های باز قبل از TP1 را زیر نظر داشته باش.";
      } else if (d24 === -d4) {
        title = (dir > 0 ? "برگشت رو به بالا در یک روز نزولی" : "اصلاح رو به پایین در یک روز صعودی");
        why = "روند ۲۴ ساعته رو به " + UP[-dir] + " است، ولی ۴ ساعت و ۱ ساعت اخیر رو به " + UP[dir] + " برگشته‌اند. این برگشت‌ها معمولاً همان‌جایی است که استاپ‌های " + SIDE[-dir] + " می‌خورد.";
        adv[-dir] = "هم‌جهت با روند بزرگ‌ترند ولی الان خلاف موج کوتاه‌اند. ورود جدید را صبر کن تا ۱ ساعته دوباره رو به " + UP[-dir] + " برگردد؛ هم نقطه‌ی ورود بهتر است هم استاپ امن‌تر. پوزیشن‌های باز در خطر استاپ‌اند.";
        adv[dir] = "حرکت کوتاه با آن‌هاست ولی خلاف روند ۲۴ ساعته؛ اگر ورود است، کوتاه‌مدت و با احتیاط.";
      } else {
        title = "حرکت تازه رو به " + UP[dir]; cls = "v-neg";
        why = "۴ ساعت و ۱ ساعت اخیر با هم رو به " + UP[dir] + " رفته‌اند ولی ۲۴ ساعته هنوز جهت روشنی ندارد؛ ممکن است شروع یک حرکت باشد.";
        adv[dir] = "هم‌جهت با موج تازه‌اند؛ اگر سیستم سیگنال داد، با جهت بازار است.";
        adv[-dir] = "هشدار فعال است: ورود جدید نه، و پوزیشن‌های باز قبل از TP1 در خطرند.";
      }
    } else if (d4 && d1 === -d4) {
      dir = d4;
      title = "حرکت ۴ ساعته رو به " + UP[dir] + " دارد برمی‌گردد";
      why = "۴ ساعت اخیر رو به " + UP[dir] + " بوده ولی ۱ ساعت گذشته خلافش رفته؛ فشار کم شده یا برگشت شروع شده.";
      adv[dir] = "سود باز را نگه دار ولی پوزیشن تازه هم‌جهت با این حرکت دیر است.";
      adv[-dir] = "فشار از رویشان برداشته شده؛ اگر ۴ ساعته هم برگردد، حرکت با آن‌ها می‌شود.";
    } else if (d4) {
      dir = d4;
      title = "حرکت ۴ ساعته رو به " + UP[dir] + "، ۱ ساعته آرام";
      why = "بازار در ۴ ساعت گذشته رو به " + UP[dir] + " رفته ولی ساعت اخیر حرکت خاصی نداشته؛ حرکت ایستاده.";
      adv[dir] = "عادی؛ دنبال قیمت نرو.";
      adv[-dir] = "هنوز هشدار نیست، ولی اگر ۱ ساعته هم رو به " + UP[dir] + " برود هشدار فعال می‌شود.";
    } else if (d1) {
      dir = d1;
      title = "جهش کوتاه ۱ ساعته رو به " + UP[dir];
      why = "فقط ساعت اخیر حرکت داشته و ۴ ساعته هنوز تأییدش نکرده؛ بیشتر این جهش‌ها نویزند.";
      adv[dir] = "عادی؛ یک ساعت حرکت، دلیل ورود نیست.";
      adv[-dir] = "عادی؛ تا وقتی ۴ ساعته هم رد نشده، هشدار نیست.";
    } else {
      cls = "v-pos"; dir = d24;
      title = d24 ? "روند ۲۴ ساعته رو به " + UP[d24] + "، ساعت‌های اخیر آرام" : "بازار آرام و بی‌جهت";
      why = d24 ? "بازار در ۲۴ ساعت رو به " + UP[d24] + " رفته ولی ۴ و ۱ ساعت اخیر آرام بوده‌اند." : "هیچ‌کدام از سه بازه حرکت معناداری ندارند.";
      adv[1] = adv[-1] = "نبض بازار حرفی ندارد؛ قوانین عادی سیستم‌ها کافی است.";
    }
    // open trades before TP1 against the short-term move: what is at stake
    var against = dir ? openTrades().filter(function (t) { return t.side === -dir && !isPartial(t); }) : [];
    var risk = sumOf(against, function (t) { return tRisk(t); });
    var stake = against.length ? '<p><b>الان ' + fa(against.length) + " پوزیشن باز " + (dir > 0 ? "شورت" : "لانگ") + "</b> خلاف این حرکت است؛ اگر همه استاپ بخورند " + usd(-risk) + " ضرر می‌شود.</p>" : "";
    // what the hypothetical rules have shown so far
    var ex = rows.filter(function (r) { return r.rule === "exit"; }), hf = rows.filter(function (r) { return r.rule === "half"; });
    function diff(xs) { return sumOf(xs, function (r) { return r.wusd - r.usd; }); }
    var ev = "<p class=\"muted\">شاهد تا الان: «بستن با هشدار» روی " + fa(ex.length) + " معامله " + (ex.length ? usd(diff(ex)) : "—") +
      " و «نصف ریسک در ورود» روی " + fa(hf.length) + " معامله " + (hf.length ? usd(diff(hf)) : "—") + " فرق داشت (مثبت یعنی کمک می‌کرد). این جمع‌بندی فقط راهنماست و روی معامله‌ها اثر ندارد.</p>";
    return '<div class="pverdict ' + cls + '"><b>' + title + "</b><p>" + why + "</p>" +
      '<div class="padv"><div><span class="badge short">شورت</span> ' + adv[-1] + '</div><div><span class="badge long">لانگ</span> ' + adv[1] + "</div></div>" + stake + ev + "</div>";
  }
  function pulseSection() {
    var pl = D.pulse || {};
    if (!pl.series || !pl.series.length)
      return '<section id="mk-pulse"><h2>نبض بازار <small>حرکت سبد ۱۰ کوین بزرگ در ۱، ۴ و ۲۴ ساعت</small></h2><div class="empty">از اجرای بعدی بات روی سرور ثبت می‌شود.</div></section>';
    var n = pl.now, pr = pl.params, st = pl.state;
    var stTxt = st > 0 ? '<b class="neg">هشدار برای شورت‌ها</b>' : st < 0 ? '<b class="neg">هشدار برای لانگ‌ها</b>' : '<b class="pos">بدون هشدار</b>';
    var cards = '<div class="grid g4 pulsecards">' +
      statCard("۱ ساعت", n ? pct(n[1], 2) : "—", "آستانه " + fa(pr.h1_pct) + "٪") +
      statCard("۴ ساعت", n ? pct(n[2], 2) : "—", "آستانه " + fa(pr.h4_pct) + "٪") +
      statCard("۲۴ ساعت", n ? pct(n[3], 2) : "—", n ? "ثبت " + esc(ago(n[0])) : "داده‌ی تازه نیست") +
      statCard("وضعیت الان", stTxt, "هشدار: ۱ و ۴ ساعته هر دو از آستانه رد شوند") + "</div>";
    var rows = pulseRows();
    function acc(r) { return r.t.approved ? "approved" : "watch"; }
    var sum = [];
    ["half", "exit"].forEach(function (rule) {
      ["approved", "watch"].forEach(function (k) {
        var xs = rows.filter(function (r) { return r.rule === rule && acc(r) === k; });
        var a = sumOf(xs, function (r) { return r.usd; }), w = sumOf(xs, function (r) { return r.wusd; });
        sum.push({rule: rule, k: k, n: xs.length, a: a, w: w});
      });
    });
    var sumTab = '<div class="scroll"><table><thead><tr><th>قاعده‌ی فرضی</th><th>حساب</th><th>تعداد</th><th>نتیجه‌ی واقعی</th><th>اگر اجرا می‌شد</th><th>فرق</th></tr></thead><tbody>' +
      sum.map(function (x) {
        return "<tr><td>" + PULSE_RULE[x.rule] + "</td><td>" + (x.k === "approved" ? '<span class="badge ok">تأییدشده</span>' : '<span class="badge watch">Watch</span>') +
          '</td><td data-v="' + x.n + '">' + fa(x.n) + '</td><td data-v="' + x.a.toFixed(4) + '">' + usd(x.a) + '</td><td data-v="' + x.w.toFixed(4) + '">' + usd(x.w) +
          '</td><td data-v="' + (x.w - x.a).toFixed(4) + '">' + usd(x.w - x.a) + "</td></tr>";
      }).join("") + "</tbody>" +
      totRow(["جمع", "", fa(sumOf(sum, function (x) { return x.n; })), usd(sumOf(sum, function (x) { return x.a; })),
        usd(sumOf(sum, function (x) { return x.w; })), usd(sumOf(sum, function (x) { return x.w - x.a; }))]) + "</table></div>";
    var list = rows.length ? '<details class="pulselist"><summary>معامله‌هایی که هشدار خوردند (' + fa(rows.length) + ")</summary>" +
      '<div class="scroll"><table><thead><tr><th>زمان</th><th>کوین</th><th>جهت</th><th>سیستم</th><th>نوع</th><th>قاعده</th><th>۱ ساعت</th><th>۴ ساعت</th><th>اهرم</th><th>سود٪</th><th>واقعی</th><th>فرضی</th><th>فرق</th></tr></thead><tbody>' +
      rows.map(function (r) {
        var t = r.t;
        return '<tr class="click" data-open="' + esc(t.id) + '">' + tsCell(r.at) + "<td><b>" + esc(t.base) + "</b>" + (t.open ? ' <small class="muted">باز</small>' : "") +
          "</td><td>" + side(t.side) + "</td><td>" + esc(sysShort(t.system)) + "</td><td>" + kind(t) + "</td><td>" + PULSE_RULE[r.rule] +
          '</td><td data-v="' + r.h1 + '">' + pct(r.h1, 2) + '</td><td data-v="' + r.h4 + '">' + pct(r.h4, 2) + "</td>" + levCell(t) + pctCell(t) +
          '<td data-v="' + r.usd.toFixed(4) + '">' + RU(r.r, r.usd) + '</td><td data-v="' + r.wusd.toFixed(4) + '">' + RU(r.wr, r.wusd) +
          '</td><td data-v="' + (r.wusd - r.usd).toFixed(4) + '">' + usd(r.wusd - r.usd) + "</td></tr>";
      }).join("") + "</tbody>" +
      totRow([{span: 10, v: "جمع"}, usd(sumOf(rows, function (r) { return r.usd; })), usd(sumOf(rows, function (r) { return r.wusd; })),
        usd(sumOf(rows, function (r) { return r.wusd - r.usd; }))]) + "</table></div></details>" : '<div class="empty">هنوز معامله‌ای با هشدار نبوده.</div>';
    var pd = S.pdays || 3;
    var range = '<div class="seg pseg">' + [[1, "۱ روز"], [3, "۳ روز"], [7, "۷ روز"]].map(function (x) {
      return '<button type="button" data-pdays="' + x[0] + '" class="' + (x[0] === pd ? "on" : "") + '">' + x[1] + "</button>"; }).join("") + "</div>";
    return '<section id="mk-pulse"><h2>نبض بازار <small>حرکت سبد ۱۰ کوین بزرگ در ۱، ۴ و ۲۴ ساعت · از ' + esc(jfmt(pl.since, "date")) + "</small></h2>" + cards +
      pulseVerdict(pl, rows) + range + '<div id="pulsechart">' + pulseChart(pl) + "</div>" +
      '<h3>اثر فرضی <small>فقط برای سنجش؛ روی هیچ معامله‌ای اثر ندارد. سبز در ستون فرق یعنی قاعده کمک می‌کرد</small></h3>' + sumTab + list + "</section>";
  }
  function marketTab() {
    var rp = regimeParts(), board = marketBoard(rp), mp = marketParts();
    var parts = [["mk-now", "۱", "بازار الان کجاست", "رژیم قیمت (۴ ساعته)، نبض بازار (۱، ۴ و ۲۴ ساعت) و رژیم هر سیستم"],
      ["mk-trend", "۲", "چند روز اخیر", "حرکت سبد کوین‌های بزرگ و دامیننس"],
      ["mk-mood", "۳", "حال‌وهوا و رویدادها", "ترس و طمع، فاندینگ، اوپن‌اینترست و خبرهای اقتصادی"],
      ["mk-cand", "۴", "فرصت‌ها", "کاندیدهای نزدیک به سیگنال، شورت‌لیست و واچ‌لیست"],
      ["mk-rec", "۵", "کارنامه‌ی ما در هر رژیم", "پوزیشن‌های رژیم فعلی، هر رژیم و روز به روز"]];
    function part(i) { var x = parts[i]; return '<div class="mkpart" id="' + x[0] + '"><b>' + x[1] + "</b><span>" + x[2] + "</span><small>" + x[3] + "</small></div>"; }
    var jump = '<div class="mkjump">' + parts.map(function (x) { return '<button type="button" data-jump="' + x[0] + '">' + x[1] + ". " + x[2] + "</button>"; }).join("") + "</div>";
    var noReg = '<section><h2>رژیم بازار</h2><div class="empty">هنوز کندل کافی برای تشخیص رژیم نیست.</div></section>';
    app.innerHTML = board + jump +
      part(0) + (rp ? rp.head : noReg) + pulseSection() + mp.cards +
      part(1) + (rp ? rp.chart : "") + mp.dom +
      part(2) + mp.mood + mp.cal +
      part(3) + mp.cand + mp.short + mp.watch +
      part(4) + (rp ? rp.posTab + rp.segTab + rp.days : "");
    mp.bind();
    if (rp) regimeHover();
    pulseBind();
    app.querySelectorAll("[data-jump]").forEach(function (b) {
      b.addEventListener("click", function () { var el = document.getElementById(b.dataset.jump); if (el) el.scrollIntoView({behavior: "smooth", block: "start"}); });
    });
    if (S.jump) {
      var el = document.getElementById(S.jump); S.jump = null;
      if (el) setTimeout(function () { el.scrollIntoView({block: "start"}); }, 0);
    }
  }
  // every reading of the market side by side: what it says now, over which time frame, and which way it votes
  var MKDIR = 0;                              // the market page's verdict: +1 up, -1 down, 0 mixed (marks the candidates)
  function marketBoard(rp) {
    var sys = D.systems || [], rg = regime(), m = rg.metrics || {}, dm = rg.dominance || {}, why = rp ? rp.why : null;
    var rows = [], votes = [];
    function arrow(v) { return v > 0 ? '<b class="pos">صعودی ▲</b>' : v < 0 ? '<b class="neg">نزولی ▼</b>' : '<b class="warn">بی‌جهت</b>'; }
    function add(name, tf, now, v, note) {
      if (v !== undefined) votes.push({name: name, v: v});
      rows.push("<tr><td><b>" + name + "</b></td><td><small>" + tf + "</small></td><td>" + now + "</td><td>" + (v === undefined ? '<small class="muted">زمینه</small>' : arrow(v)) +
        "</td><td><small>" + (note || "") + "</small></td></tr>");
    }
    if (why) {
      var lab = why.cur.label;
      add("رژیم قیمت (سبد ۱۰ کوین بزرگ)", "کندل ۴ ساعته", '<span class="badge ' + REGCLS[lab] + '">' + REGFA[lab] + "</span> از " + dur(why.h) + " پیش",
        lab === "trend_up" ? 1 : lab === "trend_down" ? -1 : 0, "۲۴ ساعت " + pct(m.move_24h, 2) + (regEnd(rg) ? " تا " + esc(regEndTxt(rg)) : "") + " · جهت‌داری " + (m.efficiency == null ? "—" : fa(Math.round(m.efficiency * 100)) + "٪"));
    }
    var pn = (D.pulse || {}).now;
    if (pn) add("نبض بازار (سبد ۱۰ کوین بزرگ)", "۱، ۴ و ۲۴ ساعت", "۱س " + pct(pn[1], 2) + " · ۴س " + pct(pn[2], 2) + " · ۲۴س " + pct(pn[3], 2), undefined,
      D.pulse.state > 0 ? '<b class="neg">هشدار برای شورت‌ها</b> (فقط نمایشی)' : D.pulse.state < 0 ? '<b class="neg">هشدار برای لانگ‌ها</b> (فقط نمایشی)' : "بدون هشدار");
    var seen = {};
    sys.forEach(function (s) {
      var r = s.regime || {}; if (!r.name) return;
      var key = r.name + "|" + r.bias; if (seen[key]) { seen[key].push(sysShort(s.name)); return; }
      seen[key] = [sysShort(s.name)];
      var v = r.bias === "short" ? -1 : r.bias === "long" ? 1 : (r.trend || 0);
      rows.push(null);                          // filled below once we know which systems share it
      var i = rows.length - 1;
      votes.push({name: "رژیم سیستم", v: v});
      seen[key].row = function () {
        rows[i] = "<tr><td><b>رژیم " + esc(seen[key].join(" و ")) + "</b></td><td><small>دامیننس و کندل روزانه</small></td><td>" + esc(REG[r.name] || r.name) +
          " · " + esc(BIAS[r.bias] || r.bias || "") + "</td><td>" + arrow(v) + "</td><td><small>همین است که سیستم با آن سیگنال می‌دهد" +
          (r.bias === "both" && r.counter_risk ? "؛ خلاف جهت با " + fa(Math.round(r.counter_risk * 100)) + "٪ ریسک" : "") + "</small></td></tr>";
      };
    });
    Object.keys(seen).forEach(function (k) { seen[k].row(); });
    var mj = (sys[0] || {}).majors || {};
    if (mj.btc != null || mj.eth != null) {
      var sum = (mj.btc || 0) + (mj.eth || 0);
      add("BTC و ETH", "هفتگی، روزانه، ۴ ساعته", "BTC <span class=\"ltr\">" + (mj.btc == null ? "—" : mj.btc) + "</span> · ETH <span class=\"ltr\">" + (mj.eth == null ? "—" : mj.eth) + "</span> از ۱۰",
        sum >= 4 ? 1 : sum <= -4 ? -1 : 0, mj.btc_weak ? "BTC ضعیف است" : "");
    }
    if (dm.t2_chg != null || dm.usdt_d_chg != null) {
      var t2 = dm.t2_chg || 0, ud = dm.usdt_d_chg || 0;
      var fv = (t2 >= 0.5 ? 1 : t2 <= -0.5 ? -1 : 0) + (ud >= 0.05 ? -1 : ud <= -0.05 ? 1 : 0);
      add("جریان پول (TOTAL2 و USDT.D)", "۲۴ ساعت", "TOTAL2 " + pct(dm.t2_chg, 2) + " · USDT.D " + (dm.usdt_d_chg == null ? "—" : '<span class="ltr">' + (ud > 0 ? "+" : "") + ud.toFixed(2) + "</span>"),
        fv > 0 ? 1 : fv < 0 ? -1 : 0, "بالا رفتن USDT.D یعنی پول از کوین‌ها به تتر می‌رود");
    }
    var fng = MK.fng || [], f = fng.length ? fng[fng.length - 1] : null;
    if (f) add("ترس و طمع", "روزانه", fa(f[1]) + " از ۱۰۰", undefined,
      f[1] < 25 ? "ترس شدید: معمولاً نزدیک کف‌های کوتاه‌مدت است؛ شورت دیرهنگام پرخطر است" : f[1] > 75 ? "طمع شدید: معمولاً نزدیک سقف‌های کوتاه‌مدت است؛ لانگ دیرهنگام پرخطر است" : "نه ترس شدید نه طمع شدید");
    var bd = deriv("BTC");
    if (bd && bd.funding != null) add("فاندینگ BTC", "هر ۸ ساعت", fpct(bd.funding * 100, 4), undefined,
      bd.funding > 0.0003 ? "لانگ‌ها شلوغ‌اند: ریزش ناگهانی محتمل‌تر" : bd.funding < -0.0001 ? "شورت‌ها شلوغ‌اند: جهش ناگهانی (شورت‌اسکوییز) محتمل‌تر" : "عادی");
    var now = Date.now(), next = (MK.calendar || []).filter(function (e) { return e.ts > now && e.w >= 3; })[0];
    if (next) add("رویداد اقتصادی پراثر بعدی", "", esc((CCY[next.country] || [""])[0] + " " + next.title) + ' <small data-until="' + next.ts + '">' + esc(untilTxt(next.ts)) + "</small>", undefined,
      next.ts - now < 3 * 3600000 ? '<span class="warn">کمتر از ۳ ساعت: استاپ‌ها ممکن است با نوسان بخورند</span>' : "");
    if (!rows.length) return "";
    // what they say together
    var up = votes.filter(function (x) { return x.v > 0; }).length, dn = votes.filter(function (x) { return x.v < 0; }).length, n = votes.length;
    var cls0 = "warn", head, lines = [];
    MKDIR = dn && !up && dn >= n - 1 ? -1 : up && !dn && up >= n - 1 ? 1 : 0;
    if (dn && !up && dn >= n - 1) { cls0 = "neg"; head = fa(dn) + " از " + fa(n) + " نشانه نزولی‌اند و هیچ‌کدام صعودی نیست"; lines.push("شورت در جهت بازار است؛ لانگ خلاف جهت است."); }
    else if (up && !dn && up >= n - 1) { cls0 = "pos"; head = fa(up) + " از " + fa(n) + " نشانه صعودی‌اند و هیچ‌کدام نزولی نیست"; lines.push("لانگ در جهت بازار است؛ شورت خلاف جهت است."); }
    else head = "نشانه‌ها هم‌جهت نیستند: " + fa(up) + " صعودی، " + fa(dn) + " نزولی، " + fa(n - up - dn) + " بی‌جهت";
    if (why) {
      var big = votes.filter(function (x) { return x.name === "رژیم سیستم"; }).map(function (x) { return x.v; });
      var bigDir = big.length && big.every(function (v) { return v === big[0]; }) ? big[0] : 0;
      if (why.cur.label === "range" && bigDir) lines.push("روند بزرگ‌تر (رژیم سیستم‌ها) " + (bigDir > 0 ? "صعودی" : "نزولی") + " است ولی قیمت در ۴ ساعته جهت ندارد. این دو با هم تناقض ندارند: سیستم‌ها روی دامیننس و کندل روزانه کندتر عوض می‌شوند و رژیم قیمت زودتر نشان می‌دهد که بازار از حرکت ایستاده. در رنج، ورودهای روندی جای حرکت ندارند.");
      else if (why.cur.label !== "range" && bigDir && (why.cur.label === "trend_up" ? 1 : -1) !== bigDir) lines.push("قیمت در ۴ ساعته خلاف رژیم سیستم‌ها حرکت می‌کند: یا اصلاح کوتاهی درون روند بزرگ‌تر است یا شروع تغییر روند. تا هم‌جهت شوند احتیاط کنید.");
      lines.push("از شروع رژیم فعلی: " + outcomeLine(why.c) + ".");
      var by = {};
      regSegs().forEach(function (sg) { var c = outcomeCount(trades().filter(function (t) { return inSeg(t, sg); })); if (!c.n) return; var o = by[sg.label] = by[sg.label] || {r: 0, u: 0, n: 0}; o.r += c.r; o.u += c.u; o.n += c.n; });
      var ks = Object.keys(by).sort(function (a, b) { return by[b].r - by[a].r; });
      if (ks.length > 1) lines.push("در ۷ روز اخیر بهترین نتیجه‌ی ما در «" + REGFA[ks[0]] + "» بوده (" + fa(by[ks[0]].n) + " پوزیشن، " + RU(by[ks[0]].r, by[ks[0]].u) + ") و ضعیف‌ترین در «" +
        REGFA[ks[ks.length - 1]] + "» (" + fa(by[ks[ks.length - 1]].n) + " پوزیشن، " + RU(by[ks[ks.length - 1]].r, by[ks[ks.length - 1]].u) + ").");
    }
    if (next && next.ts - now < 3 * 3600000) lines.push('<span class="warn">تا انتشار ' + esc(next.title) + " کمتر از ۳ ساعت مانده.</span>");
    return '<section><h2>جمع‌بندی بازار <small>همه‌ی نشانه‌های دو صفحه‌ی قبلی کنار هم</small></h2>' +
      '<div class="mkverdict v-' + cls0 + '"><b>' + head + "</b>" + lines.map(function (x) { return "<p>" + x + "</p>"; }).join("") + "</div>" +
      '<div class="scroll"><table class="mkboard"><thead><tr><th>نشانه</th><th>بازه‌ی زمانی</th><th>الان</th><th>جهت</th><th>یعنی</th></tr></thead><tbody>' + rows.join("") + "</tbody></table></div></section>";
  }
  function marketParts() {
    var sys = D.systems || [];
    var cards = sys.map(function (s) {
      var r = s.regime || {}, m = s.majors || {};
      var dir = function (x) { return x == null ? "—" : x > 0 ? '<b class="pos">صعودی ▲</b>' : x < 0 ? '<b class="neg">نزولی ▼</b>' : "خنثی"; };
      return '<section style="margin:0"><h2>' + esc(s.name) + " <small>" + esc(s.variant) + "</small></h2>" +
        '<div class="regime">' + esc(REG[r.name] || r.name || "—") + '</div><div class="muted">' + esc(BIAS[r.bias] || r.bias || "") +
        (r.risk != null ? " · ضریب ریسک " + r.risk : "") + (r.counter_risk ? " · خلاف جهت " + Math.round(r.counter_risk * 100) + "٪ ریسک" : "") + "</div>" +
        (r.usdt_d != null ? '<div style="margin-top:8px" class="grid g3"><div class="stat"><span class="l">USDT.D</span>' + dir(-r.usdt_d) + '<small> (برعکس)</small></div><div class="stat"><span class="l">BTC.D</span>' + dir(r.btc_d) +
          '</div><div class="stat"><span class="l">TOTAL2</span>' + dir(r.total2) + "</div></div>" : "") +
        '<h3>امتیاز بازارهای اصلی</h3>' + gauge("BTC", m.btc, 10) + gauge("ETH", m.eth, 10) + gauge("ETH/BTC", m.ethbtc, 10) +
        marketTake(s) +
        '<p class="muted">آخرین بررسی ' + esc(ago(s.evaluated_at)) + " · قیف " + esc(ago(s.funnel_ts ? Number(s.funnel_ts) : null)) + "</p></section>";
    }).join("");
    var dom = D.dominance || [];
    var domHtml = dom.length ? '<section><h2>دامیننس</h2><div class="grid g3">' +
      [["USDT.D", 1, "%"], ["BTC.D", 2, "%"], ["TOTAL2", 3, "B$"]].map(function (x) {
        var last = dom[dom.length - 1][x[1]], dayAgo = valueAt(dom, D.now - 86400000, x[1]), weekAgo = valueAt(dom, D.now - 7 * 86400000, x[1]);
        var ch = function (a) { return a ? pct((last - a) / a * 100, 2) : "—"; };
        return '<div class="stat"><span class="l">' + x[0] + '</span><span class="v ltr">' + last.toFixed(x[2] === "%" ? 2 : 0) + " " + x[2] + "</span>" +
          '<small>۲۴ ساعت ' + ch(dayAgo) + " · ۷ روز " + ch(weekAgo) + '</small><div class="chartbox small" id="dom-' + x[1] + '"></div></div>';
      }).join("") + "</div></section>" : "";
    var items = [];
    sys.forEach(function (s) { (s.candidates || []).forEach(function (c) { items.push([s, c]); }); });
    items.sort(function (a, b) { return (b[1].score || 0) - (a[1].score || 0); });
    var cf = S.cand.s;
    var rows = items.filter(function (x) { return !cf || x[0].name === cf; }).slice(0, 30).map(function (x) {
      var s = x[0], c = x[1], p = c.plan || {};
      var id = "cand-" + sysShort(s.name) + "-" + c.base;
      var clickable = p.entry != null;
      return "<tr" + (clickable ? ' class="click" data-cand="' + esc(id) + '"' : "") + "><td>" + esc(sysShort(s.name)) + "</td><td><b>" + esc(c.base) + "</b></td><td>" + side(c.side) +
        (MKDIR ? (c.side === MKDIR ? ' <small class="pos" title="هم‌جهت با جمع‌بندی بازار">✓ هم‌جهت</small>' : ' <small class="warn" title="خلاف جهت جمع‌بندی بازار">خلاف</small>') : "") +
        '</td><td><span class="ltr">' + (c.score || 0).toFixed(0) + "</span> " + scoreBar(c.score) + "</td><td>" + esc(c.grade || "—") + '</td><td class="num">' + px(c.price) +
        '</td><td class="num">' + px(p.entry) + '</td><td class="num">' + px(p.sl) + '</td><td class="num">' + px(p.tp1) + "</td>" + derivCells(c) + "<td>" +
        (c.news && c.news.points ? '<span class="chip ' + (c.news.points > 0 ? "pos" : "neg") + '" title="' + esc((c.news.top || []).map(function (x) { return faT(x[0]); }).join(" | ")) + '">اخبار ' +
          (c.news.points > 0 ? "+" : "") + c.news.points + "</span>" : "") +
        (c.why || []).map(function (w) { return '<span class="chip">' + esc(w) + "</span>"; }).join("") + "</td></tr>";
    }).join("");
    var names = uniqList(sys.map(function (s) { return s.name; }));
    var s0 = sys[0] || {};
    var sl = (s0.shortlist || []).map(function (x) {
      var parts = Object.keys(x.parts || {}).map(function (k) { return k + " " + (x.parts[k] || 0).toFixed(0); }).join(" · ");
      return '<div class="stat"><b>' + esc(x.base) + "</b> " + side(x.side) + ' <span class="ltr">' + (x.score || 0).toFixed(1) + "</span><br><small>" + esc((x.labels || []).join("، ")) +
        "</small><br><small class=\"ltr\">" + esc(parts) + "</small>" +
        (x.pairs && x.pairs.corr_btc != null ? '<br><small>همبستگی با BTC <span class="ltr">' + x.pairs.corr_btc.toFixed(2) + "</span> · بتا <span class=\"ltr\">" + (x.pairs.beta || 0).toFixed(2) + "</span></small>" : "") + "</div>";
    }).join("");
    var wl = Object.keys(s0.watchlist || {}).map(function (b) {
      var v = s0.watchlist[b];
      return '<span class="chip">' + esc(b) + " <small>" + esc(Array.isArray(v) ? v.join(", ") : v) + "</small></span>";
    }).join("");
    var bw = Object.keys(s0.breakout_watch || {}).map(function (b) {
      var v = s0.breakout_watch[b] || {};
      return '<span class="chip">' + esc(b) + " <small>" + esc((v.labels || []).join(", ")) + (v.level ? ' @ <span class="ltr">' + px(v.level) + "</span>" : "") + "</small></span>";
    }).join("");
    var P = {cards: '<div class="grid g2">' + cards + "</div>", mood: moodSection(), cal: calendarSection(), dom: domHtml};
    P.cand = '<section><h2>نزدیک‌ترین کاندیدها به سیگنال <small>کلیک = دیدن پلن روی نمودار</small></h2><div class="filters"><div class="seg" id="cf">' +
      [["", "همه"]].concat(names.map(function (n) { return [n, n]; })).map(function (o) { return '<button data-v="' + esc(o[0]) + '" class="' + (cf === o[0] ? "on" : "") + '">' + esc(o[1]) + "</button>"; }).join("") +
      '</div><small>سیگنال = رده‌ی A یا B بدون مانع؛ Watch = ستاپ نزدیک که فقط دنبال می‌شود</small></div>' +
      (rows ? '<div class="scroll"><table><thead><tr><th>سیستم</th><th>کوین</th><th>جهت</th><th>امتیاز</th><th>رده</th><th>قیمت</th><th>ورود</th><th>استاپ</th><th>TP1</th><th>فاندینگ</th><th>اوپن‌اینترست ۲۴س</th><th>لانگ/شورت</th><th>چرا سیگنال نشد</th></tr></thead><tbody>' + rows + "</tbody></table></div>" : '<div class="empty">در آخرین بررسی کاندیدی نبود.</div>') + "</section>";
    P.short = '<section><h2>شورت‌لیست <small>' + fa((s0.shortlist || []).length) + " کوین · قیف " + esc(ago(s0.funnel_ts ? Number(s0.funnel_ts) : null)) + '</small></h2><div class="grid g4">' + (sl || '<div class="muted">—</div>') + "</div></section>";
    P.watch = '<section><h2>واچ‌لیست <small>' + fa(Object.keys(s0.watchlist || {}).length) + "</small></h2>" + (wl || "—") +
      (bw ? '<h3>منتظر شکست</h3>' + bw : "") + ((s0.hot_categories || []).length ? '<h3>دسته‌های داغ</h3>' + s0.hot_categories.map(function (h) { return '<span class="chip">' + esc(h) + "</span>"; }).join("") : "") + "</section>";
    P.bind = function () {
    var cfEl = document.getElementById("cf");
    if (cfEl) cfEl.addEventListener("click", function (e) { var b = e.target.closest("button"); if (b) { S.cand.s = b.dataset.v; var y = window.scrollY; render(); window.scrollTo(0, y); } });
    [1, 2, 3].forEach(function (i) {
      var el = document.getElementById("dom-" + i); if (!el) return;
      var c = makeChart(el, {rightPriceScale: {visible: false}, leftPriceScale: {visible: false}, timeScale: {visible: false}, handleScroll: false, handleScale: false,
        grid: {vertLines: {visible: false}, horzLines: {visible: false}}});
      if (!c) return;
      var color = i === 1 ? cssv("--warn") : i === 2 ? cssv("--acc") : cssv("--acc2");
      var s = c.addAreaSeries({lineColor: color, topColor: color + "55", bottomColor: color + "00", lineWidth: 2, priceLineVisible: false, lastValueVisible: false});
      s.setData(uniq(dom.map(function (r) { return {time: tsec(r[0]), value: r[i]}; })));
      c.timeScale().fitContent();
    });
    };
    return P;
  }
  function valueAt(rows, ms, i) {
    var v = null;
    for (var k = 0; k < rows.length; k++) { if (rows[k][0] <= ms) v = rows[k][i]; else break; }
    return v;
  }

  // ------------------------------------------------------------------ news / strategy / method
  function strategy() { app.innerHTML = D.strategy_html || ""; }
  function method() { app.innerHTML = "<section><h2>روش محاسبه</h2>" + (D.method_html || "") + "</section>"; }

  // ------------------------------------------------------------------ live data
  function refresh() {                      // version.json is tiny: data.json only when the build changed
    fetch("version.json?t=" + Date.now(), {cache: "no-store"}).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
      .then(function (v) {
        if (v && v.app && D.app && v.app !== D.app) { location.reload(); return; }   // the panel itself was updated
        if (v && v.now === D.now) { status(); return; }
        return fetch("data.json?t=" + Date.now(), {cache: "no-store"}).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
          if (!j || j.now === D.now) { status(); return; }
          var prev = snapshot(D), sel = findSel(S.sel), was = sel ? JSON.stringify([sel.status, sel.sl_now, (sel.events || []).length]) : "";
          D = j; MK = D.market || {}; candleCache = {};
          notifyChanges(prev, j);
          if (S.tab !== "chart") { render(); return; }
          status(); fillList(); renderBottom();             // the chart keeps its zoom; only a changed trade is redrawn
          var t = findSel(S.sel);
          if (t && JSON.stringify([t.status, t.sl_now, (t.events || []).length]) !== was) tradeView(t);
        });
      }).catch(function () { status(); });
  }
  function liveBases() {
    var set = {};
    openTrades().forEach(function (t) { set[t.base] = 1; });
    var sel = findSel(S.sel); if (sel && S.tab === "chart") set[sel.base] = 1;
    if (S.tab === "chart" && S.xtab === "pend") pendingTrades().forEach(function (t) { set[t.base] = 1; });
    return Object.keys(set);
  }
  function fetchTicker(base) {               // -> {price, x: 24h numbers}
    var gate = "https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=" + encodeURIComponent(base) + "_USDT";
    function n(v) { v = parseFloat(v); return isFinite(v) ? v : null; }
    return fetch(gate).then(function (r) { if (!r.ok) throw 0; return r.json(); }).then(function (j) {
      var x = j && j[0]; if (!x) throw 0;
      return {price: n(x.last), x: {change: n(x.change_percentage), high: n(x.high_24h), low: n(x.low_24h), vol_usd: n(x.volume_24h_quote),
        funding: n(x.funding_rate), funding_next: n(x.funding_rate_indicative), mark: n(x.mark_price)}};
    }).catch(function () {
      return fetch("https://www.okx.com/api/v5/market/ticker?instId=" + encodeURIComponent(base) + "-USDT-SWAP").then(function (r) { return r.json(); })
        .then(function (j) {
          var x = j && j.data && j.data[0]; if (!x) return null;
          var last = n(x.last), o = n(x.open24h);
          return {price: last, x: {change: o ? (last / o - 1) * 100 : null, high: n(x.high24h), low: n(x.low24h), vol_usd: n(x.volCcy24h) != null && last ? n(x.volCcy24h) * last : null}};
        });
    });
  }
  var liveFails = 0, liveSkip = 0, liveN = 0;
  function liveTick() {                      // every 5 s on the chart and summary tabs, every 15 s elsewhere
    if (document.hidden) return;
    if (S.tab !== "chart" && S.tab !== "sum" && liveN++ % 3) return;
    if (liveSkip > 0) { liveSkip--; return; }     // the exchanges were unreachable: try again less often
    var bases = liveBases();
    if (!bases.length) return;
    Promise.all(bases.map(function (b) {
      return fetchTicker(b).then(function (q) {
        if (q && q.price && isFinite(q.price)) S.live[b] = {price: q.price, x: q.x, ts: Date.now(), prev: S.live[b] ? S.live[b].price : null};
        return q;
      }).catch(function () { return null; });
    })).then(function (res) {
      var ok = res.some(function (p) { return p != null; });
      S.liveOk = ok;
      if (ok) liveFails = 0; else { liveFails++; liveSkip = Math.min(8, liveFails * 2); }
      status();
      updateLive();
    });
  }
  function freshTick() {                     // the chart's candles from the exchange, once a minute
    if (document.hidden || S.tab !== "chart" || !current) return;
    var base = current.base, tf = current.tf;
    freshCandles(base, tf).then(function (fr) {
      if (!fr || !current || current.base !== base || current.tf !== tf) return;
      current.apply(mergeRows(current.rows, fr));
    });
  }
  function updateLive() {
    if (S.tab === "chart") {
      var t = findSel(S.sel);
      if (t && current && current.base === t.base && S.live[t.base]) {
        var L = S.live[t.base], p = L.price, b = current.last, now = tsec(Date.now());
        var start = Math.floor(now / current.step) * current.step;
        if (b) {
          var bar = start > b.time ? {time: start, open: b.close, high: Math.max(b.close, p), low: Math.min(b.close, p), close: p}
            : {time: b.time, open: b.open, high: Math.max(b.high, p), low: Math.min(b.low, p), close: p};
          try { current.series.update(bar); current.last = bar; current.ohlc(null); } catch (e) {}
        }
        var el = document.getElementById("tv-px");
        if (el) {
          el.textContent = px(p);
          el.classList.remove("up", "down");
          if (L.prev != null && L.prev !== p) { void el.offsetWidth; el.classList.add(p > L.prev ? "up" : "down"); }
        }
        var x = liveX(t.base);
        [["tv-chg", pct(x.change, 2)], ["tv-hi", '<span class="ltr">' + px(x.high) + "</span>"], ["tv-lo", '<span class="ltr">' + px(x.low) + "</span>"],
         ["tv-vol", musd(x.vol_usd)], ["tv-fund", fpct(x.funding != null ? x.funding * 100 : null, 4)], ["tv-pnl", headPnl(t)]].forEach(function (k) {
          var e2 = document.getElementById(k[0]); if (e2) e2.innerHTML = k[1];
        });
      }
      document.querySelectorAll(".titem").forEach(function (it) {
        var tr = byId(it.getAttribute("data-open"));
        if (tr && tr.open && tr.filled_at) { var r = it.querySelector(".r"); if (r) r.innerHTML = RU(openR(tr), pnlUsd(tr)); }
      });
      document.querySelectorAll("#xtable tr[data-open]").forEach(function (row) {
        var tr = byId(row.getAttribute("data-open")); if (!tr) return;
        var xp = row.querySelector(".xp"), xu = row.querySelector(".xu");
        if (xp) xp.textContent = px(livePrice(tr));
        if (xu && tr.open && tr.filled_at) xu.innerHTML = pnlCell(tr);
        var xo = row.querySelector(".xo"), ov = outUsd(tr);
        if (xo && tr.open && ov != null) { xo.innerHTML = usd(ov, false); xo.setAttribute("data-v", ov.toFixed(4)); }
        var xc = row.querySelector(".xpc"), pv = pnlPct(tr);
        if (xc && tr.open && pv != null) { xc.innerHTML = pctTxt(tr, true); xc.setAttribute("data-v", pv.toFixed(3)); }
      });
      var xos = document.getElementById("xosum");
      if (xos) xos.innerHTML = usd(sumOf(openTrades(), function (t) { return outUsd(t); }), false);
      var xs = document.getElementById("xsum");
      if (xs) xs.innerHTML = usd(sumOf(openTrades(), function (t) { return floatUsd(t); }));
    } else if (S.tab === "sum") {
      sumKpis();
    } else if (S.tab === "home") {          // only the live numbers: the charts and toggles stay as they are
      ACC.forEach(function (x) {
        var l = accLive(x[0]), v = document.getElementById("eqv-" + x[0]), r = document.getElementById("eqr-" + x[0]);
        if (v) v.textContent = "$" + l.equity.toFixed(2);
        if (r) r.innerHTML = accLine(x[0], l);
      });
      var mp = document.getElementById("minipos");
      if (mp) { mp.innerHTML = miniPositions(openTrades().sort(function (a, b) { return b.created - a.created; })); decorateTips(mp); }
    }
  }

  // ------------------------------------------------------------------ market regime (2026-11-16, Ali: "one overall reason when results turn,
  // e.g. the market changed regime, so the last N positions went like this")
  var REGFA = {trend_down: "روند نزولی", trend_up: "روند صعودی", range: "رنج (بی‌جهت)"};
  var REGCLS = {trend_down: "neg", trend_up: "pos", range: "warn"};
  var REGEV = {drop: "ریزش تند", jump_up: "جهش تند", whipsaw: "نوسان تند دوطرفه"};
  var REGWHY = {
    trend_down: "بازار یک‌طرفه پایین می‌رود. شورت‌های روندی سیستم بهترین نتیجه را می‌گیرند و لانگ‌ها خلاف جهت‌اند.",
    trend_up: "بازار یک‌طرفه بالا می‌رود. لانگ‌های روندی بهترین نتیجه را می‌گیرند و شورت‌ها خلاف جهت‌اند.",
    range: "بازار جهت ندارد. سیستم ما روندی است، پس ورودها جا برای حرکت ندارند: بیشتر رنج می‌خورند یا به استاپ می‌رسند. امتیاز ستاپ‌ها کمتر به A/B می‌رسد و بیشتر سیگنال‌ها Watch می‌شوند. معمولاً با شروع روند بعدی نتیجه‌ها برمی‌گردد."
  };
  function regime() { return D.regime || {}; }
  function regSegs() { return regime().segments || []; }
  function inSeg(t, s) { return t.filled_at && t.filled_at >= s.from && t.filled_at < s.to; }
  // how a trade went: stop / still ranging / in profit
  function tradeOutcome(t) {
    if (!t.filled_at) return null;
    var r = t.open ? openR(t) : t.net_r;
    if (r == null) return null;
    if (!t.open && t.status === "sl" && r < 0) return "sl";
    if (r >= 0.5 || isPartial(t)) return "win";
    if (!t.open && r <= -0.5) return "sl";
    return "flat";
  }
  var OUTFA = {sl: ["استاپ", "neg"], flat: ["رنج / بی‌نتیجه", "warn"], win: ["سود", "pos"]};
  function outcomeCount(list) {
    var c = {sl: 0, flat: 0, win: 0, n: 0, r: 0, u: 0};
    list.forEach(function (t) {
      var o = tradeOutcome(t); if (!o) return;
      c[o]++; c.n++;
      c.r += (t.open ? openR(t) : t.net_r) || 0;
      c.u += pnlUsd(t) || 0;
    });
    return c;
  }
  function outcomeLine(c) {
    if (!c.n) return "هنوز پوزیشنی پر نشده";
    return fa(c.n) + " پوزیشن: " + [["sl", c.sl], ["flat", c.flat], ["win", c.win]].filter(function (x) { return x[1]; })
      .map(function (x) { return '<b class="' + OUTFA[x[0]][1] + '">' + fa(x[1]) + " " + OUTFA[x[0]][0] + "</b>"; }).join("، ") +
      " · جمع " + RU(c.r, c.u);
  }
  // the headline sentence: what changed and what it did to the last positions
  function regimeReason() {
    var segs = regSegs(); if (!segs.length) return null;
    var cur = segs[segs.length - 1], prev = segs.length > 1 ? segs[segs.length - 2] : null;
    var since = trades().filter(function (t) { return inSeg(t, {from: cur.from, to: Infinity}); });
    var c = outcomeCount(since), h = (Date.now() - cur.from) / 3600000;
    var txt = "بازار " + (prev ? "از " + dur(h) + " پیش از «" + REGFA[prev.label] + "» به «<b>" + REGFA[cur.label] + "</b>» رفته است."
      : "الان در «<b>" + REGFA[cur.label] + "</b>» است.") +
      " از آن موقع " + outcomeLine(c) + ".";
    // positions opened before the change that the new regime closed with a stop
    var hit = trades().filter(function (t) { return t.filled_at && t.filled_at < cur.from && t.status === "sl" && t.closed_at >= cur.from; });
    if (hit.length) txt += " " + fa(hit.length) + " پوزیشن هم که از رژیم قبل باز مانده بود در این رژیم استاپ خورد (" +
      hit.map(function (t) { return esc(t.base); }).filter(function (b, i, a) { return a.indexOf(b) === i; }).join("، ") + "): " + RU(sumOf(hit, function (t) { return t.net_r; }), sumOf(hit, pnlUsd)) + ".";
    return {cur: cur, prev: prev, since: since, c: c, h: h, html: txt};
  }
  var REGSHORT = {trend_down: "نزولی", trend_up: "صعودی", range: "رنج"};
  var BARFA = {drop: "ریزش تند", jump_up: "جهش تند", trend_down: "روند نزولی", trend_up: "روند صعودی", whipsaw: "نوسان دوطرفه",
    range_quiet: "رنج آرام", range: "رنج"};
  // 2026-11-16 (Ali: "make the regime chart much nicer"): the basket line coloured by regime over a soft area,
  // a regime ribbon under it with the move of each regime, round grid steps and a hover read-out
  function regimeChart(rg) {
    var bars = rg.bars || []; if (bars.length < 2) return "";
    var BAR = 4 * 3600000, W = 960, Hh = 320, L = 10, Rr = 64, T = 16, B = 66, PB = Hh - B;
    var x0 = bars[0][0], x1 = bars[bars.length - 1][0] + BAR;
    var pts = [[x0, 0]].concat(bars.map(function (b) { return [b[0] + BAR, b[1]]; }));
    var vals = pts.map(function (p) { return p[1]; }), mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
    var step = [0.5, 1, 2, 2.5, 5, 10, 20].filter(function (st) { return (mx - mn) / st <= 5; })[0] || 25;
    var lo = Math.floor((mn - step * 0.25) / step) * step, hi = Math.ceil((mx + step * 0.25) / step) * step;
    function X(t) { return L + (t - x0) / (x1 - x0) * (W - L - Rr); }
    function Y(v) { return T + (hi - v) / (hi - lo) * (PB - T); }
    function f1(v) { return v.toFixed(1); }
    var segs = (rg.segments || []).filter(function (s) { return s.to > x0; });
    var g = '<defs>' + ["neg", "pos", "warn"].map(function (c) {
      return '<linearGradient id="rga-' + c + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--' + c + ');stop-opacity:.2"/>' +
        '<stop offset="1" style="stop-color:var(--' + c + ');stop-opacity:0"/></linearGradient>';
    }).join("") + "</defs>";
    // soft background per regime
    segs.forEach(function (s) {
      var a = Math.max(X(s.from), L), b = Math.min(X(s.to), W - Rr); if (b <= a) return;
      g += '<rect x="' + f1(a) + '" y="' + T + '" width="' + f1(b - a) + '" height="' + (PB - T) + '" class="rbg ' + REGCLS[s.label] + '"/>';
    });
    // grid: round steps, the zero line stronger
    var lastY = Y(pts[pts.length - 1][1]);
    for (var v = lo; v <= hi + 1e-9; v += step) {
      var y = f1(Y(v)), z = Math.abs(v) < 1e-9, near = Math.abs(Y(v) - lastY) < 16;
      g += '<line x1="' + L + '" x2="' + (W - Rr) + '" y1="' + y + '" y2="' + y + '" class="' + (z ? "zl" : "gl") + '"/>' +
        (near ? "" : '<text x="' + (W - Rr + 8) + '" y="' + (Y(v) + 4).toFixed(1) + '" class="ax">' + (v > 0 ? "+" : "") + (step < 1 ? v.toFixed(1) : Math.round(v)) + "%</text>");
    }
    // days: a faint line at Tehran midnight and the date under the ribbon
    for (var t = Math.ceil((x0 + 12600000) / 86400000) * 86400000 - 12600000; t <= x1; t += 86400000) {
      g += '<line x1="' + f1(X(t)) + '" x2="' + f1(X(t)) + '" y1="' + T + '" y2="' + PB + '" class="vl"/>';
      if (t + 43200000 <= x1) g += '<text x="' + f1(X(t + 43200000)) + '" y="' + (Hh - 10) + '" class="ax" text-anchor="middle">' + esc(jfmt(t, "date")) + "</text>";
    }
    // the line and its area, each piece in its regime's colour
    function inSegPts(s) {
      var out = [];
      pts.forEach(function (p, i) { if (p[0] > s.from && p[0] <= s.to) { if (!out.length && i) out.push(pts[i - 1]); out.push(p); } });
      return out;
    }
    var pieces = segs.map(function (s) { return {c: REGCLS[s.label], p: inSegPts(s)}; }).filter(function (x) { return x.p.length > 1; });
    if (!pieces.length) pieces = [{c: "", p: pts}];
    pieces.forEach(function (x) {
      var d = x.p.map(function (p, i) { return (i ? "L" : "M") + f1(X(p[0])) + " " + f1(Y(p[1])); }).join(" ");
      if (x.c) g += '<path d="' + d + " L" + f1(X(x.p[x.p.length - 1][0])) + " " + PB + " L" + f1(X(x.p[0][0])) + " " + PB + ' Z" fill="url(#rga-' + x.c + ')"/>';
      g += '<path d="' + d + '" class="rl ' + x.c + '"/>';
    });
    // sharp moves
    (rg.events || []).forEach(function (e) {
      var b = bars.filter(function (x) { return x[0] === e.ts; })[0]; if (!b) return;
      var cx = f1(X(e.ts + BAR)), cy = f1(Y(b[1]));
      g += '<circle cx="' + cx + '" cy="' + cy + '" r="9" class="reh ' + e.kind + '"/><circle cx="' + cx + '" cy="' + cy + '" r="4.5" class="re ' + e.kind + '">' +
        "<title>" + esc(REGEV[e.kind] + " · " + jfmt(e.ts) + " · " + e.move + "%") + "</title></circle>";
    });
    // the last value
    var last = pts[pts.length - 1], ly = Y(last[1]), lc = last[1] >= 0 ? "pos" : "neg";
    g += '<circle cx="' + f1(X(last[0])) + '" cy="' + f1(ly) + '" r="4" class="rlast"/>' +
      '<rect x="' + (W - Rr + 4) + '" y="' + f1(ly - 10) + '" width="' + (Rr - 6) + '" height="20" rx="6" class="rpill ' + lc + '"/>' +
      '<text x="' + (W - Rr / 2 + 1) + '" y="' + f1(ly + 4) + '" class="rpt" text-anchor="middle">' + (last[1] > 0 ? "+" : "") + last[1].toFixed(1) + "%</text>";
    // the regime ribbon
    var ry = PB + 10;
    segs.forEach(function (s) {
      var a = Math.max(X(s.from), L) + 1, b = Math.min(X(s.to), W - Rr) - 1; if (b <= a) return;
      g += '<rect x="' + f1(a) + '" y="' + ry + '" width="' + f1(b - a) + '" height="22" rx="6" class="rrib ' + REGCLS[s.label] + '"><title>' +
        esc(REGFA[s.label] + " · " + jfmt(s.from) + " تا " + jfmt(s.to) + " · بازار " + s.move + "%") + "</title></rect>";
      var lab = REGSHORT[s.label] + " \u2066" + (s.move > 0 ? "+" : "") + s.move.toFixed(1) + "%\u2069";
      if (b - a > 92) g += '<text x="' + f1((a + b) / 2) + '" y="' + (ry + 15) + '" class="rribt" text-anchor="middle" direction="rtl">' + esc(lab) + "</text>";
      else if (b - a > 44) g += '<text x="' + f1((a + b) / 2) + '" y="' + (ry + 15) + '" class="rribt" text-anchor="middle">' + esc(REGSHORT[s.label]) + "</text>";
    });
    // hover read-out
    g += '<line class="rx" x1="0" x2="0" y1="' + T + '" y2="' + PB + '" style="display:none"/><circle class="rxd" r="5" cx="0" cy="0" style="display:none"/>' +
      '<rect class="rov" x="' + L + '" y="' + T + '" width="' + (W - L - Rr) + '" height="' + (PB - T) + '"/>';
    regimeChart.geo = {x0: x0, x1: x1, W: W, L: L, Rr: Rr, X: X, Y: Y, bars: bars, BAR: BAR};
    return '<div class="regwrap"><svg class="regchart" viewBox="0 0 ' + W + " " + Hh + '" direction="ltr">' + g + '</svg><div class="regtip" style="display:none"></div></div>' +
      '<div class="legend reglegend">' + ["trend_down", "range", "trend_up"].map(function (k) { return '<span><i class="rrib ' + REGCLS[k] + '"></i>' + REGFA[k] + "</span>"; }).join("") +
      Object.keys(REGEV).map(function (k) { return '<span><i class="re ' + k + '"></i>' + REGEV[k] + "</span>"; }).join("") + "</div>";
  }
  function regimeHover() {
    var wrap = app.querySelector(".regwrap"), geo = regimeChart.geo; if (!wrap || !geo) return;
    var svg = wrap.querySelector("svg"), tip = wrap.querySelector(".regtip"), ln = svg.querySelector(".rx"), dot = svg.querySelector(".rxd");
    function hide() { tip.style.display = ln.style.display = dot.style.display = "none"; }
    function move(ev) {
      var r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) / r.width * geo.W;
      var t = geo.x0 + (sx - geo.L) / (geo.W - geo.L - geo.Rr) * (geo.x1 - geo.x0), best = null;
      geo.bars.forEach(function (b) { if (!best || Math.abs(b[0] + geo.BAR - t) < Math.abs(best[0] + geo.BAR - t)) best = b; });
      if (!best) return hide();
      var cx = geo.X(best[0] + geo.BAR), cy = geo.Y(best[1]);
      ln.setAttribute("x1", cx); ln.setAttribute("x2", cx); dot.setAttribute("cx", cx); dot.setAttribute("cy", cy);
      ln.style.display = dot.style.display = "";
      var i = geo.bars.indexOf(best), mv = best[1] - (i ? geo.bars[i - 1][1] : 0);
      tip.innerHTML = "<b>" + esc(jfmt(best[0])) + "</b><br>سبد از شروع " + pct(best[1], 2) + "<br>این کندل " + pct(mv, 2) +
        ' · اندازه <span class="ltr">' + best[2].toFixed(2) + "%</span>" + (best[3] ? '<br><span class="muted">' + (BARFA[best[3]] || best[3]) + "</span>" : "");
      tip.style.display = "";
      var px = cx / geo.W * r.width, w = tip.offsetWidth;
      tip.style.left = Math.max(4, Math.min(r.width - w - 4, px + (px > r.width / 2 ? -w - 14 : 14))) + "px";
      tip.style.top = Math.max(4, cy / svg.viewBox.baseVal.height * r.height - 30) + "px";
    }
    svg.addEventListener("mousemove", move);
    svg.addEventListener("touchmove", function (e) { if (e.touches[0]) move(e.touches[0]); }, {passive: true});
    svg.addEventListener("mouseleave", hide);
  }
  // 2026-11-18 (Ali): the regime's 24 h move ends at the last closed 4H candle, not now (the pulse's does)
  function regEnd(rg) { var b = (rg.bars || [])[(rg.bars || []).length - 1]; return b ? b[0] + 4 * 3600000 : null; }
  function regEndTxt(rg) { var e = regEnd(rg); return jfmt(e, Date.now() - e > 12 * 3600000 ? "dt" : "time"); }
  function regimeParts() {
    var rg = regime(), segs = regSegs();
    if (!segs.length) return null;
    var why = regimeReason(), cur = why.cur, m = rg.metrics || {}, dm = rg.dominance || {};
    var paused = (D.systems || []).filter(function (s) { return s.paused_until && s.paused_until > Date.now(); });
    var head = '<section><h2>رژیم بازار <small>سبد ' + fa((rg.basket || []).length) + " کوین بزرگ روی کندل ۴ ساعته</small></h2>" +
      '<div class="regnow ' + REGCLS[cur.label] + '"><div class="regl"><span class="l">رژیم الان</span><b>' + REGFA[cur.label] + "</b><small>از " + esc(jfmt(cur.from)) + " · " + dur(why.h) + "</small></div>" +
      '<div class="regwhy"><p>' + why.html + "</p><p class=\"muted\">" + REGWHY[cur.label] + "</p>" +
      (why.prev ? '<p class="muted">رژیم قبلی، ' + REGFA[why.prev.label] + " (" + esc(jfmt(why.prev.from)) + " تا " + esc(jfmt(why.prev.to)) + "، بازار " + pct(why.prev.move, 1) + "): " +
        outcomeLine(outcomeCount(trades().filter(function (t) { return inSeg(t, why.prev); }))) + ".</p>" : "") +
      (paused.length ? '<p class="warn">' + paused.map(function (s) { return esc(s.name) + " بعد از ضررهای پشت‌سرهم تا " + esc(jfmt(s.paused_until)) + " سیگنال نمی‌دهد"; }).join(" · ") + ".</p>" : "") +
      "</div></div>" +
      '<div class="grid g4">' +
      statCard("حرکت ۲۴ ساعت بازار", pct(m.move_24h, 2), "جهت‌داری " + (m.efficiency == null ? "—" : fa(Math.round(m.efficiency * 100)) + "٪") +
        (regEnd(rg) ? " · تا کندل بسته‌شده‌ی " + esc(regEndTxt(rg)) : "")) +
      statCard("اندازه‌ی کندل ۴ ساعته", '<span class="ltr">' + (m.bar_size_24h == null ? "—" : m.bar_size_24h.toFixed(2) + "%") + "</span>",
        "روزهای قبل " + (m.bar_size_before == null ? "—" : m.bar_size_before.toFixed(2) + "%")) +
      statCard("آلت‌ها (TOTAL2)", dm.t2 ? '<span class="ltr">$' + (dm.t2 / 1000).toFixed(2) + "T</span>" : "—", "۲۴ ساعت " + pct(dm.t2_chg, 2)) +
      statCard("دامیننس BTC", dm.btc_d ? '<span class="ltr">' + dm.btc_d.toFixed(2) + "%</span>" : "—", "۲۴ ساعت " + (dm.btc_d_chg == null ? "—" : '<span class="ltr">' + (dm.btc_d_chg > 0 ? "+" : "") + dm.btc_d_chg.toFixed(2) + "</span>")) +
      "</div></section>";
    var chart = '<section><h2>رژیم‌های ۷ روز اخیر <small>خط: حرکت سبد ۱۰ کوین بزرگ از شروع بازه · نوار پایین: رژیم و حرکت بازار در آن</small></h2>' + regimeChart(rg) + "</section>";
    // every regime with the trades opened in it
    var rows = segs.slice().reverse().map(function (s) {
      var list = trades().filter(function (t) { return inSeg(t, s); }), c = outcomeCount(list);
      var h = (Math.min(s.to, Date.now()) - s.from) / 3600000;
      return '<tr><td><span class="badge ' + REGCLS[s.label] + '">' + REGFA[s.label] + "</span></td>" + tsCell(s.from) + tsCell(s.to > Date.now() ? null : s.to) +
        '<td data-v="' + h + '">' + dur(h) + "</td><td>" + pct(s.move, 1) + '</td><td class="ltr">' + s.bar_size.toFixed(2) + "%</td><td>" + fa(c.n) + "</td><td>" +
        fa(c.sl) + "</td><td>" + fa(c.flat) + "</td><td>" + fa(c.win) + "</td><td>" + RU(c.n ? c.r : null, c.n ? c.u : null) + "</td></tr>";
    }).join("");
    var all = outcomeCount(trades().filter(function (t) { return segs.some(function (s) { return inSeg(t, s); }); }));
    var segTab = '<section><h2>معامله‌ها در هر رژیم <small>پوزیشن‌هایی که در آن رژیم پر شدند؛ نتیجه‌ی بسته یا وضعیت الان</small></h2><div class="scroll"><table><thead><tr>' +
      "<th>رژیم</th><th>از</th><th>تا</th><th>مدت</th><th>حرکت بازار</th><th>اندازه‌ی کندل</th><th>پوزیشن</th><th>استاپ</th><th>رنج</th><th>سود</th><th>نتیجه</th></tr></thead><tbody>" + rows + "</tbody>" +
      totRow([{span: 6, v: "جمع"}, fa(all.n), fa(all.sl), fa(all.flat), fa(all.win), RU(all.r, all.u)]) + "</table></div></section>";
    // the positions of the current regime
    var list = why.since.filter(function (t) { return t.filled_at; }).sort(function (a, b) { return b.filled_at - a.filled_at; });
    var posTab = '<section><h2>پوزیشن‌های رژیم فعلی <small>' + fa(list.length) + " پوزیشن از " + esc(jfmt(cur.from)) + "</small></h2>" + (list.length ? '<div class="scroll"><table><thead><tr>' +
      "<th>کوین</th><th>جهت</th><th>نوع</th><th>سیستم</th><th>اهرم</th><th>ورود</th><th>وضعیت</th><th>نتیجه</th><th>سود٪</th></tr></thead><tbody>" +
      list.map(function (t) {
        var o = tradeOutcome(t), r = t.open ? openR(t) : t.net_r;
        return '<tr class="click" data-open="' + esc(t.id) + '"><td><b>' + esc(t.base) + "</b></td><td>" + side(t.side) + "</td><td>" + kind(t) + "</td><td>" + esc(sysShort(t.system)) + "</td>" +
          levCell(t) + tsCell(t.filled_at) + '<td><span class="badge ' + (o ? OUTFA[o][1] : "gray") + '">' + (o ? OUTFA[o][0] : "—") + (t.open ? " · باز" : "") + "</span></td>" +
          '<td data-v="' + (r == null ? "" : r) + '">' + RU(r, pnlUsd(t)) + splitNote(t) + "</td>" + pctCell(t) + "</tr>";
      }).join("") + "</tbody>" + totRow([{span: 7, v: "جمع " + fa(list.length) + " پوزیشن"}, RU(why.c.r, why.c.u), ""]) + "</table></div>" : '<div class="empty">در این رژیم هنوز پوزیشنی پر نشده.</div>') + "</section>";
    return {head: head, chart: chart, posTab: posTab, segTab: segTab, days: regimeDays(), why: why};
  }
  // the last days side by side: what the market did and what it did to the accounts
  function regimeDays() {
    var segs = regSegs(), bars = regime().bars || [], eq = (D.accounts.all || {}).eq_curve || [];
    var days = {}, order = [];
    function key(ms) { return jfmt(ms, "date"); }
    function day(ms) { var k = key(ms); if (!days[k]) { days[k] = {ts: ms, fam: {}, move: 0, n: 0, ok: 0, w: 0, u: 0}; order.push(k); } return days[k]; }
    bars.forEach(function (b) { var d = day(b[0]); var f = {drop: "trend_down", jump_up: "trend_up", range_quiet: "range", whipsaw: "range"}[b[3]] || b[3]; if (f) d.fam[f] = (d.fam[f] || 0) + 1; });
    for (var i = 1; i < bars.length; i++) day(bars[i][0]).move += bars[i][1] - bars[i - 1][1];
    trades().forEach(function (t) {
      if (t.created && days[key(t.created)]) { var d = days[key(t.created)]; if (t.approved) d.ok++; else d.w++; }
      if (isDone(t) && days[key(doneTs(t))]) days[key(doneTs(t))].u += doneUsd(t, true) || 0;
    });
    function eqAt(ms) { var v = null; eq.forEach(function (p) { if (p[0] <= ms) v = p[1]; }); return v; }
    var rows = order.slice().reverse().map(function (k) {
      var d = days[k], fam = Object.keys(d.fam).sort(function (a, b) { return d.fam[b] - d.fam[a]; })[0];
      var dayStart = Math.floor((d.ts + 12600000) / 86400000) * 86400000 - 12600000;      // Tehran midnight (UTC+3:30)
      var e0 = eqAt(dayStart), e1 = eqAt(Math.min(dayStart + 86400000, Date.now()));
      var ch = e0 != null && e1 != null ? e1 - e0 : null;
      return '<tr><td data-v="' + d.ts + '">' + esc(k) + '</td><td><span class="badge ' + (REGCLS[fam] || "gray") + '">' + (REGFA[fam] || "—") + "</span></td><td>" + pct(d.move, 1) +
        "</td><td>" + fa(d.ok) + "</td><td>" + fa(d.w) + "</td><td>" + usd(d.u) + '</td><td data-v="' + (ch == null ? "" : ch) + '">' + usd(ch) + "</td></tr>";
    }).join("");
    var tot = order.reduce(function (s, k) { var d = days[k]; s.ok += d.ok; s.w += d.w; s.u += d.u; return s; }, {ok: 0, w: 0, u: 0});
    return '<section><h2>روز به روز <small>حساب «همه با هم»</small></h2><div class="scroll"><table><thead><tr><th>روز</th><th>رژیم غالب</th><th>حرکت بازار</th><th>سیگنال تأییدشده</th><th>سیگنال Watch</th><th>سود/زیان قطعی</th><th>تغییر موجودی</th></tr></thead><tbody>' +
      rows + "</tbody>" + totRow([{span: 3, v: "جمع"}, fa(tot.ok), fa(tot.w), usd(tot.u), ""]) + "</table></div></section>";
  }

  // ------------------------------------------------------------------ help tooltips (Ali, 2026-11-15)
  var TIPS = {
    "نبض بازار": "میانگین حرکت ۱۰ کوین بزرگ (BTC، ETH، SOL، …) در ۱ ساعت، ۴ ساعت و ۲۴ ساعت گذشته که بات هر ۵ دقیقه ثبت می‌کند. هشدار وقتی است که ۱ ساعته و ۴ ساعته هر دو از آستانه رد شوند: رو به بالا برای شورت‌ها و رو به پایین برای لانگ‌ها.\nاثر فرضی دو قاعده را حساب می‌کند بی‌آنکه کاری بکند: «نصف ریسک در ورود» (معامله‌ای که در حال هشدار باز شد با نصف ریسک) و «بستن با هشدار» (معامله‌ی قبل از TP1 در اولین هشدار بسته می‌شد، با قیمت همان لحظه).",
    "رژیم بازار": "حال بازار از روی سبد ۱۰ کوین بزرگ روی کندل ۴ ساعته: روند نزولی (افت دست‌کم ۴٪ در ۲۴ ساعت، بیشترش یک‌طرفه، یا ۶٪ در سه روز که هنوز ادامه دارد)، روند صعودی (برعکس) یا رنج (هیچ‌کدام). رژیمی که کمتر از ۸ ساعت طول بکشد حساب نمی‌شود.\nجمله‌ی بالا می‌گوید بازار کی عوض شد و پوزیشن‌هایی که بعدش باز شدند چه شدند. استاپ: با ضرر بسته شده. رنج: هنوز کمتر از ۰٫۵R حرکت کرده. سود: ۰٫۵R به بالا یا رد شده از TP1.\n«حرکت ۲۴ ساعت بازار» در این بخش تا آخرین کندل ۴ ساعته‌ی بسته‌شده است (تا چهار ساعت عقب‌تر از الان)؛ ۲۴ ساعتِ تا همین الان در «نبض بازار» است.",
    "رژیم‌های ۷ روز اخیر": "خط سفید حرکت سبد بازار از ۷ روز پیش است. رنگ پس‌زمینه رژیم آن زمان است. نقطه‌ها حرکت‌های تند هستند: ریزش یا جهش ۴٪ در ۸ ساعت، یا کندلی دست‌کم ۱٫۸ برابر معمول که سر جایش برگشته (استاپ هر دو طرف را می‌زند).",
    "پوزیشن‌های رژیم فعلی": "پوزیشن‌هایی که بعد از شروع رژیم فعلی پر شدند، با وضعیت الان.",
    "معامله‌ها در هر رژیم": "هر رژیم، چقدر طول کشید، بازار چقدر حرکت کرد و پوزیشن‌هایی که در آن پر شدند چه نتیجه‌ای گرفتند. اینجا معلوم می‌شود سیستم در کدام حال بازار کار می‌کند.",
    "روز به روز": "هر روز (به وقت تهران): رژیم غالب، حرکت بازار، تعداد سیگنال‌ها، سود و زیان قطعی و تغییر موجودی حساب «همه با هم» با پوزیشن‌های باز.",
    "حساب‌های شبیه‌سازی‌شده": "سه حساب ۱۰۰ دلاری که از ۱۵ آبان با همه‌ی سیگنال‌ها معامله کرده‌اند.\nتأییدشده: فقط سیگنال‌های A و B که به تلگرام رفتند.\nWatch: ستاپ‌هایی که ارسال نشدند ولی دنبال شدند.\nهمه با هم: هر دو نوع در یک حساب.\nریسک هر معامله درصدی از موجودی همان لحظه است، پس سود و ضرر روی هم انباشته می‌شود.",
    "منحنی موجودی": "موجودی حساب انتخاب‌شده در طول زمان، ساعت به ساعت: سود و زیان قطعی به‌علاوه‌ی پوزیشن‌های باز با قیمت همان ساعت. پس سود روی پوزیشن باز همان روزی دیده می‌شود که به دست آمده. بالای خط‌چین «شروع» سبز و زیر آن قرمز است. هر نقطه‌ی کوچک روی منحنی یک معامله‌ی بسته است: سبز سود و قرمز ضرر.\nنقطه‌ی آخر سود و زیان پوزیشن‌های باز را با قیمت زنده هم حساب می‌کند.\nموس را روی نمودار ببرید تا موجودی همان لحظه را بالای نمودار ببینید. «هر سه با هم» سه حساب را کنار هم نشان می‌دهد.",
    "منحنی سود تجمعی": "جمع سود هر گروه از معاملات به ترتیب زمان بسته شدن. حالت دلار سود حساب ۱۰۰ دلاری خود همان گروه را نشان می‌دهد و حالت R نتیجه را بر حسب ریسک، که سیستم‌ها را منصفانه‌تر مقایسه می‌کند. خط‌چین‌ها Watch هستند. روی هر مورد راهنمای زیر نمودار بزنید تا خاموش یا روشن شود.",
    "خلاصه‌ی سیستم‌ها": "هر سیستم دو نیمه دارد:\nتأییدشده: سیگنال‌های A و B که به تلگرام رفتند، با سود دلاری‌شان در حساب ۱۰۰ دلاری تأییدشده.\nWatch: ستاپ‌هایی که ارسال نشدند ولی دنبال شدند، با سودشان در حساب ۱۰۰ دلاری Watch.\nبالای کارت جمع هر دو در حساب «همه با هم» آمده است.",
    "پخش نتیجه‌ها": "چند درصد معامله‌های هر گروه در هر بازه‌ی R تمام شده‌اند. سیستم خوب ضررهایش نزدیک ‎-1R می‌ماند و بخشی از معامله‌هایش به ۲R و بیشتر می‌رسد.",
    "نتیجه‌ی روزانه": "جمع سود یا زیان هر روز برای هر گروه. رنگ پررنگ‌تر یعنی روز بزرگ‌تر. عدد کوچک زیر آن جمع R و تعداد معامله‌هاست.",
    "خلاصه‌ی خبرها": "همه‌ی خبرهایی که ربات از صرافی‌ها و سایت‌های خبری خوانده است، به جز موارد نامربوط مثل سهام توکنی‌شده.\nستاره‌ها از ۱ تا ۵ اهمیت خبر را نشان می‌دهند: قدرت نوع خبر (مثلاً دلیست یا لیستینگ)، اینکه اعلان رسمی صرافی است یا رسانه، و اینکه قیمت کوین بعد از خبر واقعاً حرکت کرد یا نه (۳٪ یک ستاره و ۸٪ دو ستاره).",
    "خبرهای مثبت و منفی هر روز": "میله‌ی سبز رو به بالا تعداد خبرهای مثبت و میله‌ی قرمز رو به پایین تعداد خبرهای منفی هر روز است.",
    "اثر واقعی هر نوع خبر": "برای هر نوع خبر، میانگین حرکت قیمت کوین ۱، ۴ و ۲۴ ساعت بعد از خبر. عدد مثبت یعنی قیمت در همان جهتی رفت که خبر انتظار داشت.\n«درست از آب درآمد» درصد خبرهایی است که بعد از ۲۴ ساعت در جهت انتظار حرکت کردند. نزدیک ۵۰٪ یعنی آن نوع خبر عملاً اثری ندارد.",
    "کوین‌های پرخبر": "کوین‌هایی که در این مدت بیشترین خبر را داشته‌اند. عدد کنار هر کوین تعداد خبر است.",
    "همه‌ی خبرها": "جدیدترین خبرها بالا هستند. نوار رنگی کنار هر خبر جهت آن است: سبز مثبت، قرمز منفی و خاکستری خنثی.\nزیر هر خبر کوین‌دار، حرکت قیمت آن کوین ۱، ۴ و ۲۴ ساعت بعد از خبر آمده است (اگر هنوز آن زمان نرسیده باشد، خالی است).",
    "پوزیشن‌های باز": "معامله‌هایی که وارد شده‌اند و هنوز بسته نشده‌اند.\nR الان یعنی سود یا ضرر فعلی بر حسب واحد ریسک: ‎+1R یعنی به اندازه‌ی فاصله‌ی ورود تا استاپ اول سود کرده‌ایم.\nروی هر ردیف بزنید تا روی نمودار ببینید.",
    "وضعیت بازار": "رژیمی که هر سیستم الان تشخیص داده و جهتی که اجازه‌ی معامله دارد. امتیاز BTC، ETH و ETH/BTC از ‎-10 تا ‎+10 است. منفی یعنی ضعیف.",
    "آخرین اجراها": "آخرین باری که هر بخش ربات اجرا شده است. اگر بررسی سیگنال یا مدیریت پوزیشن‌ها قرمز شد، ربات عقب افتاده است. جزئیات در تب «سلامت سیستم» است.",
    "ریسک باز الان": "اگر همین الان همه‌ی پوزیشن‌ها به استاپ فعلی‌شان بخورند چقدر ضرر یا سود قطعی می‌کنیم.\nاستاپی که به نقطه‌ی ورود یا بالاتر رفته، ریسک را صفر یا مثبت می‌کند.\nتخمین «اگر BTC ۵٪ بریزد» از بتای هر کوین با BTC (کندل‌های ۱ ساعته‌ی هفته‌ی گذشته، یا ۴ ساعته‌ی سه هفته‌ی گذشته) ساخته می‌شود. وقتی همه‌ی پوزیشن‌ها با BTC حرکت می‌کنند، در واقع یک شرط بزرگ روی BTC دارید.",
    "هشدار رویداد اقتصادی": "رویدادهای مهم آمریکا مثل FOMC، CPI و آمار اشتغال باعث حرکت‌های تند و غیرعادی می‌شوند. استاپ‌ها در این ساعت‌ها راحت‌تر می‌خورند.",
    "پوزیشن‌های باز (": "روی هر مورد بزنید تا نمودار و جزئیاتش را ببینید.",
    "سفارش در انتظار": "سیگنال‌هایی که سفارش لیمیت یا تأییدشان هنوز پر نشده است.",
    "کاندیدهای نزدیک به سیگنال": "کوین‌هایی که امتیازشان به سیگنال نزدیک است ولی هنوز یکی از شرط‌ها را ندارند. پلن احتمالی‌شان روی نمودار نشان داده می‌شود.",
    "معاملات بسته": "معامله‌های تمام‌شده، جدیدترین بالا.",
    "رویدادها": "هر اتفاقی که برای این معامله افتاده، به ترتیب زمان: صدور سیگنال، ورود، TPها، جابه‌جایی استاپ و خروج.",
    "جزئیات": "شرایطی که سیستم هنگام صدور سیگنال دیده است: رژیم، فاز، سیکل، کندل ماشه و سطحی که استاپ پشت آن است.",
    "امتیاز بخش‌ها": "امتیاز تکنیکال ۱۰۰ نمره‌ای از این بخش‌ها جمع شده است. نوار بلندتر یعنی آن بخش قوی‌تر بوده است.",
    "دروازه‌ها": "شرط‌هایی که همه باید سبز باشند تا سیگنال ارسال شود. اگر یکی قرمز باشد، ستاپ فقط Watch می‌ماند.",
    "تأییدها و برچسب‌ها": "نشانه‌هایی که سیستم دیده است، مثل فشردگی (SQUEEZE)، افزایش اوپن‌اینترست و حجم غیرعادی.",
    "یادداشت‌های سیستم": "مواردی که سیستم کنار این معامله ثبت کرده است، مثلاً نبود تأیید ورود یا ضعف BTC.",
    "چرا هنوز سیگنال نیست": "شرط‌هایی که این کاندید هنوز ندارد. وقتی برطرف شوند، ممکن است سیگنال شود.",
    "دیتای مشتقات": "از API عمومی Gate، هر چند دقیقه یک بار.\nفاندینگ: کارمزدی که هر چند ساعت بین لانگ و شورت جابه‌جا می‌شود. مثبت یعنی لانگ‌ها پول می‌دهند و بازار لانگ‌تر است.\nاوپن‌اینترست: کل قراردادهای باز. بالا رفتنش یعنی پول تازه وارد شده است.\nنسبت حساب‌ها: تعداد حساب‌های لانگ به شورت.\nلیکوئید: پوزیشن‌هایی که در ۲۴ ساعت گذشته به زور بسته شده‌اند.\n«شلوغ» یعنی بیشتر بازار هم‌جهت ما است. این حالت خطر حرکت تند خلاف جهت را بالا می‌برد. این یک قاعده‌ی سرانگشتی است، نه دستور.",
    "ماشین‌حساب حجم": "با موجودی واقعی، درصد ریسک و اهرم خودتان حساب می‌کند چقدر وارد شوید که اگر استاپ خورد، دقیقاً همان مقدار ریسک را از دست بدهید.\nقیمت لیکوئید تقریبی است (مارجین ایزوله و نرخ نگهداری ۰٫۵٪). اگر لیکوئید قبل از استاپ باشد، اهرم را کم کنید.\nعددها فقط در مرورگر خودتان ذخیره می‌شوند.",
    "این سیگنال را گرفتید؟": "اگر این معامله را واقعاً گرفتید یا نگرفتید علامت بزنید. در تب «تحلیل عملکرد»، نتیجه‌ی معامله‌هایی که خودتان گرفته‌اید جدا نشان داده می‌شود.",
    "MAE و MFE این معامله": "MFE: بیشترین سودی که معامله در طول عمرش داشت، بر حسب R.\nMAE: بیشترین فاصله‌ای که قیمت خلاف ما رفت.\nاگر MFE بزرگ بوده ولی معامله با ضرر بسته شده، سود قبل از TP پس داده شده است.",
    "تاریخچه‌ی معاملات": "همه‌ی معامله‌های بسته و سیگنال‌های بدون ورود. روی سرستون‌ها بزنید تا مرتب شود و روی هر ردیف بزنید تا روی نمودار ببینید.\nR خالص یعنی بعد از کارمزد و هزینه‌ها.",
    "منحنی R تجمعی": "جمع R هر گروه از معاملات به ترتیب زمان بسته شدن. R بر حسب ریسک است، پس سیستم‌ها با ریسک متفاوت را منصفانه مقایسه می‌کند.",
    "جدول مقایسه": "هر ستون یک گروه است. سبز بهترین و قرمز بدترین عدد آن ردیف است. با تعداد کم معامله، تفاوت‌ها هنوز قابل‌اعتماد نیستند.",
    "سیستم ۱ در برابر": "مقایسه‌ی رودررو روی همه‌ی معامله‌های هر سیستم. کادر سبز برنده‌ی همان معیار است.",
    "امتیاز بازارهای اصلی": "امتیاز BTC، ETH و ETH/BTC از ‎-10 تا ‎+10، از تحلیل هفتگی، روزانه و ۴ ساعته.",
    "دامیننس": "USDT.D: سهم تتر از بازار. بالا رفتنش یعنی پول از کریپتو بیرون می‌رود.\nBTC.D: سهم بیت‌کوین. بالا رفتنش یعنی آلت‌ها عقب می‌مانند.\nTOTAL2: ارزش کل بازار بدون BTC.",
    "حال‌وهوای بازار": "شاخص ترس و طمع از ۰ (ترس شدید) تا ۱۰۰ (طمع شدید) است. معمولاً ترس شدید نزدیک کف‌ها و طمع شدید نزدیک سقف‌هاست، ولی این زمان‌بندی دقیق نمی‌دهد.\nفاندینگ و اوپن‌اینترست BTC و ETH نشان می‌دهند بازار مشتقات چقدر داغ است.",
    "تقویم اقتصادی": "رویدادهای مهم و متوسط آمریکا در این هفته، به وقت تهران. رویدادهای «مهم» معمولاً بازار کریپتو را هم تکان می‌دهند.",
    "نزدیک‌ترین کاندیدها به سیگنال": "کوین‌هایی که در آخرین بررسی بیشترین امتیاز را داشتند. ستون «چرا سیگنال نشد» شرط‌هایی را نشان می‌دهد که کم است.\nنوار امتیاز سه خط دارد: Watch از ۵۵، B از ۶۵ و A از ۷۵.",
    "شورت‌لیست": "کوین‌هایی که قیف (دامیننس، بازارهای اصلی، اسکن و جفت‌ها) برای بررسی دقیق انتخاب کرده است.",
    "واچ‌لیست": "کوین‌هایی با برچسب‌های جالب (فشردگی، حجم غیرعادی، اوپن‌اینترست) که زیر نظرند ولی هنوز ستاپ ندارند.",
    "منتظر شکست": "کوین‌هایی که اگر از سطح مشخص‌شده عبور کنند، ممکن است ستاپ بدهند.",
    "دسته‌های داغ": "دسته‌هایی از کوین‌ها که این روزها قوی‌تر از بازار هستند.",
    "هشدارهای خبری": "خبرهای مهمی که برای کوین‌ها پیدا شده است. تعداد ستاره اهمیت خبر را نشان می‌دهد.",
    "مسیر مشترک هر دو سیستم": "مرحله‌هایی که هر کوین طی می‌کند تا سیگنال شود، از نگاه کلی بازار تا ورود.",
    "رژیم بازار": "سیستم اول تشخیص می‌دهد بازار در چه حالی است و بر اساس آن اجازه‌ی لانگ یا شورت می‌دهد.",
    "وزن بخش‌های امتیاز": "سهم هر بخش از امتیاز ۱۰۰ نمره‌ای.",
    "مدیریت معامله": "قاعده‌های ورود، استاپ، تارگت‌ها و اندازه‌ی ریسک.",
    "فیلترهای حذف": "ستاپ‌هایی که بررسی ضررهای گذشته نشان داد معمولاً بد هستند و کنار گذاشته می‌شوند.",
    "روش محاسبه": "این بخش توضیح می‌دهد عددهای پنل چطور حساب می‌شوند.",
    "آمار پایداری": "انتظار ریاضی: میانگین R هر معامله. اگر مثبت باشد، سیستم در بلندمدت سود می‌دهد.\nSQN: کیفیت سیستم با توجه به نوسان نتیجه‌ها. زیر ۱ ضعیف، ۱ تا ۲ متوسط و بالای ۲ خوب است. با کمتر از ۳۰ معامله قابل‌اعتماد نیست.\nبیشترین ضرر پشت‌سرهم نشان می‌دهد چقدر صبر و موجودی لازم است.\nنسبت میانگین برد به میانگین باخت: وقتی نرخ برد پایین است، این عدد باید بالا باشد.",
    "کجا سود می‌دهیم": "نتیجه‌ی معامله‌های بسته را گروه‌به‌گروه نشان می‌دهد. بالای جدول گروه‌بندی را عوض کنید.\nگروهی که چند معامله دارد و میانگین R آن مدام منفی است، جای فکر کردن دارد.\nگروهی با کمتر از ۵ معامله کم‌رنگ نشان داده می‌شود، چون هنوز قابل‌اعتماد نیست.",
    "MAE و MFE": "هر نقطه یک معامله‌ی بسته است. محور افقی بیشترین فاصله‌ای است که قیمت خلاف ما رفت (MAE) و محور عمودی بیشترین سودی که در طول معامله داشتیم (MFE)، هر دو بر حسب R.\nنقطه‌های قرمز بالای خط ۱R: سود داشتیم و از دست رفت.\nنقطه‌های سبز با MAE نزدیک ۱: معامله تا نزدیک استاپ رفت و بعد سود داد.\nاین فقط اندازه‌گیری است. هر تغییری در استراتژی، اول باید با بک‌تست سنجیده شود.",
    "امتیاز در برابر نتیجه": "هر نقطه یک معامله است: محور افقی امتیاز سیستم و محور عمودی نتیجه بر حسب R. خط‌های عمودی مرزهای Watch (۵۵)، B (۶۵) و A (۷۵) هستند.\nاگر نقطه‌های سمت راست به‌طور معنادار بالاتر از سمت چپ باشند، امتیاز واقعاً پیش‌بینی می‌کند. با گذشت زمان معلوم می‌شود مرزها جای درستی هستند یا نه.",
    "معامله‌های شما": "فقط معامله‌هایی که در صفحه‌ی نمودار با «گرفتم» علامت زده‌اید. کنارش نتیجه‌ی سیگنال‌های تأییدشده‌ای را می‌بینید که نگرفتید.",
    "سلامت اجراها": "هر اجرای ربات روی سرور: کی شروع شد، چقدر طول کشید و آیا بخشی خطا داد.\nاجرای قیف (هر ساعت) چند دقیقه طول می‌کشد و در این مدت اجرای بعدی صبر می‌کند. این عادی است.",
    "سیگنال‌های ردشده": "سیگنال‌های A و B که ربات ساخت ولی به تلگرام نفرستاد، چون:\n• سقف ۵ پوزیشنِ در ریسک پر بود (پوزیشن‌هایی که TP1 خورده‌اند حساب نمی‌شوند و امتیاز ۸۵ به بالا از سقف رد می‌شود)\n• یک پوزیشن هم‌جهت و هم‌بسته‌ی دیگر باز بود\n• ربات بعد از ضررهای پشت‌سرهم مکث کرده بود\n• آن کوین تازه استاپ خورده بود.\nاگر همان کوین چند بار پشت هم رد شود، یک بار شمرده می‌شود.",
    "صرافی‌ها": "تعداد دفعه‌هایی که صرافی اصلی (Gate) به یک درخواست جواب نداده و ربات سراغ صرافی پشتیبان رفته است. زیر آن آخرین پیام خطای هر صرافی آمده است تا معلوم باشد کدام درخواست و چرا.\nچند خطای پراکنده عادی است، ولی اگر زیاد شد دیتا کند یا ناقص می‌شود.",
    "پشتیبان": "دیتای ربات روی سرور است و هر چند دقیقه یک نسخه روی گیت‌هاب (شاخه‌ی data) ذخیره می‌شود. اگر این عدد خیلی قدیمی شد، اتصال سرور به گیت‌هاب مشکل دارد."
  };
  var LTIPS = {
    "حجم ۲۴ ساعت": "ارزش دلاری معامله‌های این قرارداد در ۲۴ ساعت گذشته.",
    "بیشترین ۲۴ ساعت": "بالاترین قیمت ۲۴ ساعت گذشته.",
    "کمترین ۲۴ ساعت": "پایین‌ترین قیمت ۲۴ ساعت گذشته.",
    "اوپن‌اینترست": "ارزش کل قراردادهای باز این کوین و تغییر آن در ۲۴ ساعت.",
    "سود/زیان": "سود یا زیان دلاری در حساب «همه با هم»، کنارش بر حسب R. برای پوزیشن باز با قیمت زنده حساب می‌شود.",
    "سود/زیان بسته": "جمع سود و زیان دلاری معامله‌های بسته‌شده‌ی این حساب.",
    "سود/زیان قطعی": "سود و زیانی که قطعی شده، مثل صرافی: معامله‌های بسته به‌علاوه‌ی بخشی که در TP1 و TP2 بسته شده. بقیه‌ی پوزیشن‌های باز در «سود/زیان باز» است. این عدد با جمع تاریخچه و تحلیل عملکرد یکی است.",
    "نرخ برد": "درصد معامله‌های بسته با نتیجه‌ی مثبت. نرخ برد پایین با سودهای بزرگ می‌تواند سودده باشد.",
    "بیشترین افت": "بزرگ‌ترین افت موجودی از بالاترین نقطه‌اش.",
    "ضریب سود": "جمع سودها تقسیم بر جمع ضررها. بالای ۱ یعنی سودده، بالای ۱٫۵ خوب.",
    "کارمزد و هزینه": "کارمزد معامله و لغزش قیمت تخمینی که از همه‌ی نتیجه‌ها کم شده است.",
    "باز / در انتظار": "تعداد پوزیشن‌های باز و سفارش‌هایی که هنوز پر نشده‌اند.",
    "R الان": "سود یا ضرر فعلی بر حسب ریسک. ‎+1R یعنی به اندازه‌ی یک واحد ریسک در سودیم.",
    "R خام": "نتیجه قبل از کارمزد و هزینه‌ها.",
    "R خالص": "نتیجه بعد از کارمزد و هزینه‌ها.",
    "در حساب خودش": "سود یا ضرر دلاری این معامله در حساب نوع خودش (تأییدشده یا Watch).",
    "سود/زیان (همه)": "سود یا ضرر دلاری این معامله در حساب «همه با هم».",
    "موجودی بعد": "موجودی حساب نوع خودش بعد از بسته شدن این معامله.",
    "ریسک (حساب همه)": "مبلغی که اگر استاپ اول بخورد از دست می‌رود.",
    "حجم پوزیشن": "ارزش کل پوزیشن (حجم × قیمت ورود).",
    "دلار ورودی": "پولی که برای این پوزیشن گذاشته شد: مارجین = حجم ÷ اهرم، در حساب نوع خودش (تأییدشده یا Watch).",
    "دلار خروجی": "پولی که با بسته شدن برمی‌گردد: مارجین + سود/زیان، بعد از کارمزد. برای پوزیشن باز یا بخشی بسته: اگر همین الان بسته شود.",
    "درصد ریسک / فاصله‌ی استاپ": "درصد موجودی که در خطر است، و فاصله‌ی استاپ از ورود به درصد.",
    "استاپ الان": "استاپ فعلی. بعد از TP1 به نقطه‌ی ورود می‌رود.",
    "ورود": "قیمت ورود معامله.",
    "قیمت الان": "آخرین قیمت. اگر قیمت زنده از صرافی برسد، هر ۱۵ ثانیه به‌روز می‌شود.",
    "امتیاز": "امتیاز تکنیکال از ۱۰۰. Watch از ۵۵، B از ۶۵ و A از ۷۵.",
    "چرا سیگنال نشد": "شرط‌هایی که برای سیگنال شدن کم است.",
    "فاندینگ": "نرخ فاندینگ فعلی. مثبت یعنی لانگ‌ها به شورت‌ها پول می‌دهند.",
    "اوپن‌اینترست ۲۴س": "تغییر کل قراردادهای باز در ۲۴ ساعت. بالا رفتن همراه با حرکت قیمت یعنی پول تازه پشت حرکت است.",
    "لانگ/شورت": "نسبت تعداد حساب‌های لانگ به شورت. بالای ۱ یعنی حساب‌های لانگ بیشترند.",
    "تعداد": "تعداد معامله‌های بسته‌ی دارای ورود در این گروه.",
    "میانگین R": "میانگین نتیجه‌ی هر معامله بر حسب R، بعد از هزینه‌ها.",
    "جمع R": "جمع نتیجه‌ی همه‌ی معامله‌های این گروه.",
    "اهرم": "اهرم ایزوله‌ی پیشنهادی. طوری انتخاب شده که قیمت لیکوئید دست‌کم ۲٫۵ برابر فاصله‌ی استاپ دور باشد (حداکثر ۱۰x). اهرم سود دلاری را عوض نمی‌کند، فقط مارجین لازم را کم می‌کند.",
    "سود٪": "سود یا زیان نسبت به مارجین، مثل عددی که صرافی نشان می‌دهد: درصد حرکت قیمت × اهرم، بعد از کارمزد. برای معامله‌ی بخشی بسته، سود قطعی‌شده هم حساب است.",
    "مدت اجرا": "از شروع تا پایان یک اجرای ربات. اجراهای زیر ۶۰ ثانیه یعنی ربات واقعاً هر دقیقه بازار را می‌بیند."
  };
  var TIP_KEYS = Object.keys(TIPS).sort(function (a, b) { return b.length - a.length; });
  function qi(text) { return '<span class="qi" tabindex="0" role="button" aria-label="راهنما" data-tip="' + esc(text) + '">؟</span>'; }
  function firstText(el) {
    for (var n = el.firstChild; n; n = n.nextSibling) if (n.nodeType === 3 && n.textContent.trim()) return n.textContent.trim();
    return el.textContent.trim();
  }
  function decorateTips(root) {
    (root || app).querySelectorAll("h2, h3, h4").forEach(function (h) {
      if (h.querySelector(".qi")) return;
      var t = firstText(h);
      for (var i = 0; i < TIP_KEYS.length; i++) if (t.indexOf(TIP_KEYS[i]) === 0) { h.insertAdjacentHTML("beforeend", qi(TIPS[TIP_KEYS[i]])); return; }
    });
    (root || app).querySelectorAll("th, .l, .lv .l, .kv .l").forEach(function (el) {
      if (el.querySelector(".qi")) return;
      var t = el.textContent.trim();
      if (LTIPS[t]) el.insertAdjacentHTML("beforeend", qi(LTIPS[t]));
    });
    makeSortable(root);
  }
  var tipEl = document.getElementById("tip");
  function showTip(el) {
    tipEl.innerHTML = esc(el.getAttribute("data-tip")).replace(/\n/g, "<br>");
    tipEl.hidden = false;
    tipEl.style.maxWidth = Math.min(340, window.innerWidth - 16) + "px";
    var r = el.getBoundingClientRect();
    var left = Math.min(window.innerWidth - tipEl.offsetWidth - 8, Math.max(8, r.left + r.width / 2 - tipEl.offsetWidth / 2));
    var top = r.bottom + 6;
    if (top + tipEl.offsetHeight > window.innerHeight - 8) top = Math.max(8, r.top - tipEl.offsetHeight - 6);
    tipEl.style.left = left + "px"; tipEl.style.top = top + "px";
  }
  document.addEventListener("mouseover", function (e) { var q = e.target.closest && e.target.closest(".qi"); if (q) showTip(q); });
  document.addEventListener("mouseout", function (e) { if (e.target.closest && e.target.closest(".qi")) tipEl.hidden = true; });
  document.addEventListener("click", function (e) {
    var q = e.target.closest && e.target.closest(".qi");
    if (q) { e.preventDefault(); e.stopPropagation(); showTip(q); } else tipEl.hidden = true;
  }, true);
  document.addEventListener("keydown", function (e) {
    var q = e.target.closest && e.target.closest(".qi");
    if (q && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); showTip(q); }
    if (e.key === "Escape") tipEl.hidden = true;
  });
  window.addEventListener("scroll", function () { tipEl.hidden = true; }, {passive: true});

  // ------------------------------------------------------------------ shared numbers
  var MK = D.market || {};
  function deriv(base) { return (MK.deriv || {})[base] || null; }
  function fpct(x, d) { return x == null ? "—" : '<span class="ltr ' + cls(x) + '">' + (x > 0 ? "+" : "") + x.toFixed(d == null ? 3 : d) + "%</span>"; }
  function musd(x) {
    if (x == null) return "—";
    var a = Math.abs(x), s = a >= 1e9 ? (a / 1e9).toFixed(2) + "B" : a >= 1e6 ? (a / 1e6).toFixed(1) + "M" : a >= 1e3 ? (a / 1e3).toFixed(0) + "K" : a.toFixed(0);
    return '<span class="ltr">$' + s + "</span>";
  }
  function crowd(sideV, d) {                  // rule of thumb: most of the market already on our side
    if (!d) return null;
    var f = d.funding, l = d.lsr_account;
    if (sideV > 0 && ((f != null && f >= 0.0005) || (l != null && l >= 2.5))) return "بازار از سمت لانگ‌ها شلوغ است";
    if (sideV < 0 && ((f != null && f <= -0.0005) || (l != null && l <= 0.6))) return "بازار از سمت شورت‌ها شلوغ است";
    return null;
  }
  function derivBox(base, sideV) {
    var d = deriv(base);
    if (!d) return "";
    var c = crowd(sideV, d);
    return '<h3>دیتای مشتقات</h3>' + (c ? '<p class="warn">⚠ ' + esc(c) + "</p>" : "") + '<div class="levels">' +
      lvh("فاندینگ", fpct(d.funding != null ? d.funding * 100 : null, 4)) +
      lvh("اوپن‌اینترست ۲۴س", fpct(d.oi_change, 1) + (d.oi_usd ? " <small>" + musd(d.oi_usd) + "</small>" : "")) +
      lvh("لانگ/شورت", d.lsr_account != null ? '<span class="ltr">' + d.lsr_account.toFixed(2) + "</span>" : "—") +
      lvh("لیکوئید ۲۴س", d.liq_long_usd != null ? "لانگ " + musd(d.liq_long_usd) + " · شورت " + musd(d.liq_short_usd) : "—") +
      (d.change != null ? lvh("تغییر ۲۴ ساعت", fpct(d.change, 2)) : "") + "</div>";
  }
  function derivCells(c) {                    // funding / OI / long-short columns of the candidates table
    var d = deriv(c.base);
    if (!d) return "<td>—</td><td>—</td><td>—</td>";
    var w = crowd(c.side, d);
    return "<td>" + fpct(d.funding != null ? d.funding * 100 : null, 4) + (w ? ' <b class="warn" title="' + esc(w) + '">⚠</b>' : "") + "</td><td>" + fpct(d.oi_change, 1) +
      '</td><td class="ltr">' + (d.lsr_account != null ? d.lsr_account.toFixed(2) : "—") + "</td>";
  }
  function lvh(label, html) { return '<div class="lv"><span class="l">' + label + '</span><span class="v">' + html + "</span></div>"; }

  // ------------------------------------------------------------------ chart: hash by coin, calculator, marks
  function latestForBase(base) {
    var all = trades().filter(function (t) { return t.base === base; }).sort(function (a, b) {
      return (b.open ? 1 : 0) - (a.open ? 1 : 0) || b.created - a.created;
    });
    if (all[0]) return all[0].id;
    var c = candidates().filter(function (x) { return x.base === base; })[0];
    return c ? c.id : null;
  }
  var CALC = {bal: 1000, risk: 1, lev: 5};
  try { var cs0 = JSON.parse(localStorage.getItem("calc") || "null"); if (cs0) CALC = Object.assign(CALC, cs0); } catch (e) {}
  function calcBox(t) {
    if (!t.entry || !t.sl || t.entry === t.sl) return "";
    return '<h3>ماشین‌حساب حجم</h3><div class="calc" data-calc="' + esc(t.id) + '">' +
      '<label>موجودی ($)<input type="number" min="1" step="any" data-k="bal" value="' + CALC.bal + '"></label>' +
      '<label>ریسک (٪)<input type="number" min="0.05" max="20" step="0.05" data-k="risk" value="' + CALC.risk + '"></label>' +
      '<label>اهرم<input type="number" min="1" max="125" step="1" data-k="lev" value="' + CALC.lev + '"></label>' +
      '<div class="calc-out"></div></div>';
  }
  function calcRender(box, t) {
    var dist = Math.abs(t.entry - t.sl) / t.entry, riskUsd = CALC.bal * CALC.risk / 100;
    var notional = riskUsd / dist, margin = notional / CALC.lev, qty = notional / t.entry;
    var long = t.side > 0, mmr = 0.005;
    var liq = long ? t.entry * (1 - 1 / CALC.lev + mmr) : t.entry * (1 + 1 / CALC.lev - mmr);
    var bad = long ? liq >= t.sl : liq <= t.sl;
    box.querySelector(".calc-out").innerHTML =
      lvh("ضرر اگر استاپ بخورد", usd(-riskUsd)) + lvh("حجم پوزیشن", usd(notional, false)) +
      lvh("مارجین لازم", usd(margin, false) + (margin > CALC.bal ? ' <b class="neg">بیشتر از موجودی</b>' : "")) +
      lvh("تعداد", '<span class="ltr">' + (qty >= 100 ? qty.toFixed(0) : qty.toPrecision(4)) + "</span>") +
      lvh("قیمت لیکوئید تقریبی", '<span class="ltr ' + (bad ? "neg" : "") + '">' + px(liq) + "</span>" +
        (bad ? ' <b class="neg">قبل از استاپ! اهرم را کم کنید</b>' : "")) +
      (t.tp1 ? lvh("سود در TP1", usd(Math.abs(t.tp1 - t.entry) / t.entry * notional)) : "");
  }
  function wireCalc(t) {
    var box = app.querySelector(".calc");
    if (!box) return;
    calcRender(box, t);
    box.addEventListener("input", function (e) {
      var k = e.target.dataset.k, v = parseFloat(e.target.value);
      if (!k || !(v > 0)) return;
      CALC[k] = v;
      try { localStorage.setItem("calc", JSON.stringify(CALC)); } catch (er) {}
      calcRender(box, t);
    });
  }
  S.marks = D.marks || {};
  S.apiOk = null;
  function markBox(t) {
    if (t.cand || !D.panel_api) return "";
    var m = S.marks[t.id];
    var st = m ? (m.taken ? "گرفتید" : "نگرفتید") : "هنوز علامت نزده‌اید";
    return '<h3>این سیگنال را گرفتید؟</h3><div class="marks" data-mark="' + esc(t.id) + '">' +
      '<button class="btn ' + (m && m.taken ? "on" : "") + '" data-v="1">گرفتم</button>' +
      '<button class="btn ' + (m && !m.taken ? "on" : "") + '" data-v="0">نگرفتم</button>' +
      (m ? '<button class="btn ghost" data-v="">پاک کردن</button>' : "") +
      ' <small class="muted" id="mark-st">' + st + (S.apiOk === false ? " · بخش ثبت روی سرور فعال نیست" : "") + "</small></div>";
  }
  function wireMarks(t) {
    var box = app.querySelector(".marks");
    if (!box) return;
    box.addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      var v = b.dataset.v, taken = v === "" ? null : v === "1";
      document.getElementById("mark-st").textContent = "در حال ثبت…";
      fetch("api/mark", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({id: t.id, taken: taken})})
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (m) { S.marks = m; S.apiOk = true; tradeView(t); })
        .catch(function () { S.apiOk = false; document.getElementById("mark-st").textContent = "ثبت نشد: بخش ثبت روی سرور هنوز فعال نیست"; });
    });
  }
  function loadMarks() {
    if (!D.panel_api) return;
    fetch("api/marks", {cache: "no-store"}).then(function (r) { if (!r.ok) throw 0; return r.json(); })
      .then(function (m) { S.marks = m; S.apiOk = true; }).catch(function () { S.apiOk = false; });
  }
  function excBox(t) {
    if (t.mfe_r == null) return "";
    return '<h3>MAE و MFE این معامله</h3><div class="levels">' +
      lvh("بیشترین سود در طول معامله (MFE)", R(t.mfe_r)) + lvh("بیشترین حرکت خلاف (MAE)", R(-t.mae_r)) +
      lvh("کندل‌های استفاده‌شده", '<span class="ltr">' + esc(t.exc_tf) + "</span>") + "</div>";
  }

  // ------------------------------------------------------------------ home: risk and events
  function openRiskSection() {
    var open = openTrades();
    if (!open.length) return "";
    var atStop = 0, longR = 0, shortR = 0, btc5 = 0, nb = 0;
    open.forEach(function (t) {
      var risk = Math.abs(t.entry - t.sl);
      if (!risk || t.risk_usd == null) return;
      var stopNow = t.sl_now != null ? t.sl_now : t.sl;
      var rStop = (t.gross_r || 0) + (t.open_frac == null ? 1 : t.open_frac) * t.side * (stopNow - t.entry) / risk;
      var usdStop = t.risk_usd * rStop;
      atStop += usdStop;
      if (t.side > 0) longR += Math.min(0, usdStop); else shortR += Math.min(0, usdStop);
      var b = (D.beta || {})[t.base];
      if (b && t.size_usd) { btc5 += t.size_usd * (t.open_frac == null ? 1 : t.open_frac) * t.side * b.beta * -0.05; nb++; }
    });
    var eq = accLive("all").equity;
    return '<section><h2>ریسک باز الان <small>حساب «همه با هم»</small></h2><div class="grid g4">' +
      '<div class="stat"><span class="l">اگر همه‌ی استاپ‌ها بخورند</span><span class="v">' + usd(atStop) + "</span><small>" + pct(atStop / eq * 100, 2) + " از موجودی</small></div>" +
      '<div class="stat"><span class="l">ریسک لانگ‌ها</span><span class="v">' + usd(longR) + "</span><small>" + plural(open.filter(function (t) { return t.side > 0; }).length, "پوزیشن") + "</small></div>" +
      '<div class="stat"><span class="l">ریسک شورت‌ها</span><span class="v">' + usd(shortR) + "</span><small>" + plural(open.filter(function (t) { return t.side < 0; }).length, "پوزیشن") + "</small></div>" +
      '<div class="stat"><span class="l">اگر BTC ۵٪ بریزد (تخمین)</span><span class="v">' + (nb ? usd(btc5) : "—") + "</span><small>از بتای هر کوین با BTC برای " + plural(nb, "پوزیشن") + "</small></div>" +
      "</div></section>";
  }
  function upcomingEvents(hours) {
    var now = Date.now();
    return ((MK.calendar) || []).filter(function (e) { return (e.w == null ? e.impact === "High" : e.w >= 4) && e.ts > now - 30 * 60000 && e.ts < now + hours * 3600000; });
  }
  function eventBanner() {
    var ev = upcomingEvents(2);
    if (!ev.length) return "";
    return '<div class="banner"><h4>هشدار رویداد اقتصادی</h4>' + ev.map(function (e) {
      return "<div><b>" + esc(e.title) + "</b> · " + esc(jfmt(e.ts, "dt")) + " (" + (e.ts > Date.now() ? "تا " + esc(untilTxt(e.ts)) : "همین الان") + ")</div>";
    }).join("") + "</div>";
  }
  function untilTxt(ms) {
    var m = Math.round((ms - Date.now()) / 60000);
    if (m >= 48 * 60) return fa(Math.round(m / 1440)) + " روز دیگر";
    if (m >= 60) return fa(Math.floor(m / 60)) + " ساعت" + (m % 60 ? " و " + fa(m % 60) + " دقیقه" : "") + "ی دیگر";
    return fa(Math.max(m, 0)) + " دقیقه‌ی دیگر";
  }

  // ------------------------------------------------------------------ performance tab
  S.perf = {k: "", s: "", dim: "score"};
  var WD = ["یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه"];
  var HOUR_FMT = null, WD_FMT = null;
  function tehranHour(ms) {
    if (!HOUR_FMT) try { HOUR_FMT = new Intl.DateTimeFormat("en-GB", {timeZone: "Asia/Tehran", hour: "2-digit", hour12: false}); } catch (e) { HOUR_FMT = null; }
    return HOUR_FMT ? parseInt(HOUR_FMT.format(new Date(ms)), 10) % 24 : new Date(ms).getHours();
  }
  function tehranDay(ms) {
    if (!WD_FMT) try { WD_FMT = new Intl.DateTimeFormat("en-US", {timeZone: "Asia/Tehran", weekday: "short"}); } catch (e) { WD_FMT = null; }
    var d = WD_FMT ? ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(WD_FMT.format(new Date(ms))) : new Date(ms).getDay();
    return d < 0 ? 0 : d;
  }
  var DIMS = [
    ["score", "امتیاز", function (t) { var b = Math.floor((t.score || 0) / 5) * 5; return [[b, fa(b) + " تا " + fa(b + 5)]]; }],
    ["grade", "رده", function (t) { return [[t.grade || "—", t.grade || "—"]]; }],
    ["regime", "رژیم", function (t) { var r = (t.detail || {}).regime; return [[r || "?", REG[r] || r || "نامشخص"]]; }],
    ["side", "جهت", function (t) { return [[t.side, t.side > 0 ? "لانگ" : "شورت"]]; }],
    ["label", "برچسب", function (t) {
      var l = (((t.detail || {}).eval || {}).labels || []);
      return l.length ? l.map(function (x) { return [x, x]; }) : [["-", "بدون برچسب"]];
    }],
    ["hour", "ساعت (تهران)", function (t) { var h = Math.floor(tehranHour(t.created) / 3) * 3; return [[h, fa(h) + " تا " + fa(h + 3)]]; }],
    ["day", "روز هفته", function (t) { var d = tehranDay(t.created); return [[(d + 1) % 7, WD[d]]]; }],
    ["system", "سیستم", function (t) { return [[t.system, t.system]]; }],
    ["kind", "تأییدشده / Watch", function (t) { return [[t.approved ? 0 : 1, t.approved ? "تأییدشده" : "Watch"]]; }]
  ];
  function perfTrades() {
    var f = S.perf;
    return doneTrades().filter(function (t) {
      return (!f.k || (f.k === "ok") === t.approved) && (!f.s || t.system === f.s);
    });
  }
  function groupStats(list) {
    var n = list.length, wins = list.filter(function (t) { return t.net_r > 0; });
    var sum = list.reduce(function (s, t) { return s + t.net_r; }, 0);
    var gw = wins.reduce(function (s, t) { return s + t.net_r; }, 0);
    var gl = -list.filter(function (t) { return t.net_r <= 0; }).reduce(function (s, t) { return s + t.net_r; }, 0);
    return {n: n, win: n ? wins.length / n * 100 : null, avg: n ? sum / n : null, sum: sum, pf: gl > 0 ? gw / gl : null,
      usd: sumOf(list, function (t) { return doneUsd(t); })};
  }
  function stability(list) {
    var n = list.length;
    if (!n) return null;
    var rs = list.map(function (t) { return t.net_r; });
    var avg = rs.reduce(function (a, b) { return a + b; }, 0) / n;
    var sd = n > 1 ? Math.sqrt(rs.reduce(function (a, b) { return a + (b - avg) * (b - avg); }, 0) / (n - 1)) : null;
    var maxL = 0, cur = 0, streak = 0, peak = 0, eq = 0, dd = 0;
    rs.forEach(function (r) {
      cur = r <= 0 ? cur + 1 : 0; maxL = Math.max(maxL, cur);
      eq += r; peak = Math.max(peak, eq); dd = Math.max(dd, peak - eq);
    });
    for (var i = n - 1; i >= 0; i--) { var w = rs[i] > 0; if (i === n - 1) streak = w ? 1 : -1; else if (w === streak > 0) streak += w ? 1 : -1; else break; }
    var wins = rs.filter(function (r) { return r > 0; }), losses = rs.filter(function (r) { return r <= 0; });
    var aw = wins.length ? wins.reduce(function (a, b) { return a + b; }, 0) / wins.length : null;
    var al = losses.length ? losses.reduce(function (a, b) { return a + b; }, 0) / losses.length : null;
    return {n: n, avg: avg, sd: sd, sqn: sd > 0.05 ? avg / sd * Math.sqrt(n) : null, maxL: maxL, streak: streak, dd: dd, sum: eq,
      ret_dd: dd > 0 ? eq / dd : null, aw: aw, al: al, payoff: aw != null && al ? aw / -al : null};
  }
  function svgScatter(pts, o) {
    var W = 640, H = 310, L = 46, Rm = 12, T = 24, B = 34;
    var xs = pts.map(function (p) { return p.x; }).concat(o.xr || []), ys = pts.map(function (p) { return p.y; }).concat(o.yr || []);
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    if (x1 === x0) x1 = x0 + 1; if (y1 === y0) y1 = y0 + 1;
    var px0 = (x1 - x0) * 0.05, py0 = (y1 - y0) * 0.08; x0 -= px0; x1 += px0; y0 -= py0; y1 += py0;
    function X(v) { return L + (v - x0) / (x1 - x0) * (W - L - Rm); }
    function Y(v) { return T + (1 - (v - y0) / (y1 - y0)) * (H - T - B); }
    function ticks(a, b) { var s = Math.pow(10, Math.floor(Math.log10((b - a) / 4 || 1))); var st = [1, 2, 5, 10].map(function (m) { return m * s; }).filter(function (m) { return (b - a) / m <= 6; })[0] || s * 10; var out = []; for (var v = Math.ceil(a / st) * st; v <= b; v += st) out.push(+v.toFixed(6)); return out; }
    var g = "";
    ticks(y0, y1).forEach(function (v) { g += '<line x1="' + L + '" x2="' + (W - Rm) + '" y1="' + Y(v) + '" y2="' + Y(v) + '" class="gl"/><text x="' + (L - 6) + '" y="' + (Y(v) + 4) + '" class="ax" text-anchor="end">' + v + "</text>"; });
    ticks(x0, x1).forEach(function (v) { g += '<line y1="' + T + '" y2="' + (H - B) + '" x1="' + X(v) + '" x2="' + X(v) + '" class="gl"/><text y="' + (H - B + 16) + '" x="' + X(v) + '" class="ax" text-anchor="middle">' + v + "</text>"; });
    (o.vlines || []).forEach(function (v) { if (v[0] >= x0 && v[0] <= x1) g += '<line y1="' + T + '" y2="' + (H - B) + '" x1="' + X(v[0]) + '" x2="' + X(v[0]) + '" class="ref"/><text x="' + (X(v[0]) + 3) + '" y="' + (T + 10) + '" class="ax">' + v[1] + "</text>"; });
    (o.hlines || []).forEach(function (v) { if (v[0] >= y0 && v[0] <= y1) g += '<line x1="' + L + '" x2="' + (W - Rm) + '" y1="' + Y(v[0]) + '" y2="' + Y(v[0]) + '" class="ref"/><text x="' + (W - Rm - 4) + '" y="' + (Y(v[0]) - 4) + '" class="ax" text-anchor="end">' + v[1] + "</text>"; });
    pts.forEach(function (p) {
      g += '<circle cx="' + X(p.x).toFixed(1) + '" cy="' + Y(p.y).toFixed(1) + '" r="5.5" class="pt ' + p.c + '" data-open="' + esc(p.id) + '"><title>' + esc(p.tip) + "</title></circle>";
    });
    g += '<text x="' + ((L + W - Rm) / 2) + '" y="' + (H - 4) + '" class="ax" text-anchor="middle">' + esc(o.xl) + "</text>";
    return '<svg class="scatter" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" direction="ltr"><text x="4" y="13" class="ax">' + esc(o.yl) + "</text>" + g + "</svg>";
  }
  function perf() {
    var f = S.perf;
    var names = uniqList(trades().map(function (t) { return t.system; }));
    function seg(g, opts) {
      return '<div class="seg" data-g="' + g + '">' + opts.map(function (o) { return '<button data-v="' + esc(o[0]) + '" class="' + (f[g] === o[0] ? "on" : "") + '">' + esc(o[1]) + "</button>"; }).join("") + "</div>";
    }
    var list = perfTrades(), st = stability(list);
    var stab = st ? '<div class="grid g4">' +
      statCard("انتظار ریاضی (میانگین R)", R(st.avg), plural(st.n, "معامله")) +
      statCard("SQN", st.sqn == null ? "—" : '<span class="ltr ' + cls(st.sqn) + '">' + st.sqn.toFixed(2) + "</span>", st.n < 30 ? "کمتر از ۳۰ معامله: هنوز قابل‌اعتماد نیست" : "زیر ۱ ضعیف · بالای ۲ خوب") +
      statCard("بیشترین ضرر پشت‌سرهم", fa(st.maxL), "رشته‌ی فعلی: " + (st.streak > 0 ? fa(st.streak) + " برد" : fa(-st.streak) + " باخت")) +
      statCard("بیشترین افت (R)", '<span class="ltr">' + st.dd.toFixed(2) + "R</span>", "سود به افت: " + (st.ret_dd == null ? "—" : st.ret_dd.toFixed(2))) +
      statCard("میانگین برد / باخت", R(st.aw) + " / " + R(st.al), "نسبت: " + (st.payoff == null ? "—" : st.payoff.toFixed(2))) +
      statCard("انحراف نتیجه‌ها", st.sd == null ? "—" : '<span class="ltr">' + st.sd.toFixed(2) + "R</span>", "هرچه کمتر، قابل‌پیش‌بینی‌تر") +
      statCard("جمع نتیجه", R(st.sum), usd(sumOf(list, function (t) { return doneUsd(t); })) + " در حساب خود هر معامله") + "</div>" : '<div class="empty">هنوز معامله‌ی بسته‌ای در این فیلتر نیست.</div>';
    var dim = DIMS.filter(function (d) { return d[0] === f.dim; })[0] || DIMS[0];
    var groups = {};
    list.forEach(function (t) { dim[2](t).forEach(function (kv) { var k = String(kv[0]); (groups[k] = groups[k] || {key: kv[0], label: kv[1], list: []}).list.push(t); }); });
    var gl = Object.keys(groups).map(function (k) { var g = groups[k]; g.s = groupStats(g.list); return g; })
      .sort(function (a, b) { return typeof a.key === "number" && typeof b.key === "number" ? a.key - b.key : String(a.label).localeCompare(String(b.label), "fa"); });
    var maxAbs = Math.max.apply(null, gl.map(function (g) { return Math.abs(g.s.avg || 0); }).concat([0.5]));
    var brows = gl.map(function (g) {
      var a = g.s.avg || 0, w = Math.abs(a) / maxAbs * 50;
      var bar = '<span class="dbar"><i style="' + (a >= 0 ? "left:50%;width:" + w + "%;background:var(--pos)" : "left:" + (50 - w) + "%;width:" + w + "%;background:var(--neg)") + '"></i></span>';
      return '<tr class="' + (g.s.n < 5 ? "thin" : "") + '"><td><b>' + esc(g.label) + "</b></td><td>" + fa(g.s.n) + "</td><td>" + (g.s.win == null ? "—" : fa(Math.round(g.s.win)) + "٪") +
        "</td><td>" + R(g.s.avg) + " " + bar + "</td><td>" + R(g.s.sum) + "</td><td>" + usd(g.s.usd) + '</td><td class="ltr">' + (g.s.pf == null ? "—" : g.s.pf.toFixed(2)) + "</td></tr>";
    }).join("");
    var exc = list.filter(function (t) { return t.mfe_r != null; });
    var excPts = exc.map(function (t) {
      return {x: t.mae_r, y: t.mfe_r, c: t.net_r > 0 ? "win" : "loss", id: t.id,
        tip: t.base + " · " + (t.net_r > 0 ? "+" : "") + t.net_r.toFixed(2) + "R · MAE " + t.mae_r.toFixed(2) + " · MFE " + t.mfe_r.toFixed(2)};
    });
    var gaveBack = exc.filter(function (t) { return t.net_r <= 0 && t.mfe_r >= 1; }).length;
    var nearStop = exc.filter(function (t) { return t.net_r > 0 && t.mae_r >= 0.8; }).length;
    var allClosed = doneTrades().filter(function (t) { return !f.s || t.system === f.s; });
    var scPts = allClosed.map(function (t) {
      return {x: t.score || 0, y: t.net_r, c: t.approved ? "ok" : "wt", id: t.id, tip: t.base + " · امتیاز " + (t.score || 0).toFixed(1) + " · " + (t.net_r > 0 ? "+" : "") + t.net_r.toFixed(2) + "R"};
    });
    var mine = perfMine();
    app.innerHTML = '<section><h2>تحلیل عملکرد</h2><div class="filters">' +
      seg("k", [["", "همه"], ["ok", "تأییدشده"], ["w", "Watch"]]) +
      seg("s", [["", "همه‌ی سیستم‌ها"]].concat(names.map(function (n) { return [n, n]; }))) +
      "</div></section>" +
      '<section><h2>آمار پایداری <small>معامله‌های نتیجه‌دار (بسته، یا رد شده از TP1 با سود قطعی تا الان)، بعد از هزینه‌ها</small></h2>' + stab + "</section>" +
      '<section><h2>کجا سود می‌دهیم <small>گروه‌بندی:</small></h2><div class="filters">' + seg("dim", DIMS.map(function (d) { return [d[0], d[1]]; })) + "</div>" +
      (brows ? '<div class="scroll"><table class="brk"><thead><tr><th>' + esc(dim[1]) + "</th><th>تعداد</th><th>نرخ برد</th><th>میانگین R</th><th>جمع R</th><th>سود/زیان</th><th>ضریب سود</th></tr></thead><tbody>" + brows + "</tbody>" + (function () {
        var a = groupStats(list);
        return totRow(["همه" + (f.dim === "label" ? ' <small class="muted">(هر معامله یک بار)</small>' : ""), fa(a.n), a.win == null ? "—" : fa(Math.round(a.win)) + "٪", R(a.avg), R(a.sum), usd(a.usd),
          '<span class="ltr">' + (a.pf == null ? "—" : a.pf.toFixed(2)) + "</span>"]);
      })() + "</table></div>" +
        '<p class="muted">ردیف‌های کم‌رنگ کمتر از ۵ معامله دارند و هنوز قابل‌اعتماد نیستند.' + (f.dim === "label" ? " هر معامله ممکن است چند برچسب داشته باشد، پس جمع ردیف‌ها از کل بیشتر است." : "") + "</p>" : '<div class="empty">—</div>') + "</section>" +
      '<div class="grid g2"><section><h2>MAE و MFE <small>' + plural(exc.length, "معامله") + "</small></h2>" +
      (excPts.length ? svgScatter(excPts, {xl: "MAE: بیشترین حرکت خلاف (R)", yl: "MFE (R)", xr: [0, 1], yr: [0, 1], hlines: [[1, "1R"], [2, "TP1 (2R)"]], vlines: [[1, "استاپ"]]}) +
        '<p class="muted">' + fa(gaveBack) + " معامله‌ی ضررده حداقل ۱R سود داشته‌اند و پس داده‌اند · " + fa(nearStop) + " معامله‌ی سودده تا ۸۰٪ راه استاپ رفته‌اند. روی هر نقطه بزنید تا معامله را ببینید.</p>"
        : '<div class="empty">برای این معامله‌ها کندل کافی ذخیره نشده است.</div>') + "</section>" +
      '<section><h2>امتیاز در برابر نتیجه <small>آبی: تأییدشده · زرد: Watch</small></h2>' +
      (scPts.length ? svgScatter(scPts, {xl: "امتیاز سیستم", yl: "نتیجه (R)", vlines: [[55, "Watch"], [65, "B"], [75, "A"]], hlines: [[0, "0"]]}) : '<div class="empty">—</div>') + "</section></div>" +
      mine;
    app.querySelectorAll(".seg").forEach(function (sg) {
      sg.addEventListener("click", function (e) {
        var b = e.target.closest("button"); if (!b) return;
        f[sg.dataset.g] = b.dataset.v; perf(); decorateTips();
      });
    });
  }
  function statCard(label, v, sub) { return '<div class="stat"><span class="l">' + label + '</span><span class="v">' + v + "</span>" + (sub ? "<small>" + sub + "</small>" : "") + "</div>"; }
  function perfMine() {
    if (!D.panel_api) return "";
    var marked = doneTrades().filter(function (t) { return S.marks[t.id]; });
    var took = marked.filter(function (t) { return S.marks[t.id].taken; }), skipped = marked.filter(function (t) { return !S.marks[t.id].taken; });
    var openTook = openTrades().filter(function (t) { return S.marks[t.id] && S.marks[t.id].taken; });
    function row(label, l) { var s = groupStats(l); return "<tr><td>" + label + "</td><td>" + fa(s.n) + "</td><td>" + (s.win == null ? "—" : fa(Math.round(s.win)) + "٪") + "</td><td>" + R(s.avg) + "</td><td>" + R(s.sum) + "</td><td>" + usd(s.usd) + "</td></tr>"; }
    return '<section><h2>معامله‌های شما <small>بر اساس «گرفتم / نگرفتم» در صفحه‌ی نمودار</small></h2>' +
      (marked.length || openTook.length ? '<div class="scroll"><table><thead><tr><th></th><th>تعداد</th><th>نرخ برد</th><th>میانگین R</th><th>جمع R</th><th>سود/زیان</th></tr></thead><tbody>' +
        row("گرفتید", took) + row("نگرفتید", skipped) + "</tbody>" + totRow((function () { var a = groupStats(marked); return ["جمع", fa(a.n), a.win == null ? "—" : fa(Math.round(a.win)) + "٪", R(a.avg), R(a.sum), usd(a.usd)]; })()) + "</table></div>" + (openTook.length ? '<p class="muted">' + plural(openTook.length, "پوزیشن") + " که گرفته‌اید هنوز باز است.</p>" : "")
        : '<div class="empty">هنوز هیچ معامله‌ای را علامت نزده‌اید. در صفحه‌ی نمودار، زیر هر سیگنال «گرفتم» یا «نگرفتم» را بزنید.' + (S.apiOk === false ? " (بخش ثبت روی سرور هنوز فعال نیست.)" : "") + "</div>") + "</section>";
  }

  // ------------------------------------------------------------------ system health tab
  function health() {
    var h = D.health || {}, runs = (h.runs || []).slice().reverse();
    var now = Date.now(), day = runs.filter(function (r) { return r.ts > now - 86400000; });
    var durs = day.map(function (r) { return r.dur || 0; }).sort(function (a, b) { return a - b; });
    var med = durs.length ? durs[Math.floor(durs.length / 2)] : null, mx = durs.length ? durs[durs.length - 1] : null;
    var failed = day.filter(function (r) { return (r.failed || []).length; }).length;
    var ex = {};
    day.forEach(function (r) { Object.keys(r.ex || {}).forEach(function (k) { ex[k] = (ex[k] || 0) + r.ex[k]; }); });
    var exErr = {};                                // newest error message per exchange
    day.forEach(function (r) { Object.keys(r.ex_err || {}).forEach(function (k) { if (!exErr[k]) exErr[k] = [r.ts, r.ex_err[k]]; }); });
    var last = runs[0];
    var backupAge = h.last_backup ? (now - h.last_backup) / 60000 : null;
    var backupBad = h.storage === "git_backup" && (backupAge == null || backupAge > (h.backup_min || 15) * 3);
    var rows = runs.slice(0, 30).map(function (r) {
      var w = Math.min(100, (r.dur || 0) / 120 * 100);
      return '<tr><td data-v="' + r.ts + '">' + esc(jfmt(r.ts, "dt")) + '</td><td><span class="ltr">' + (r.dur || 0).toFixed(0) + 's</span> <span class="dur"><i style="width:' + w + "%;background:var(" + ((r.dur || 0) > 60 ? "--warn" : "--acc") + ')"></i></span></td><td>' +
        esc((r.tasks || []).map(function (k) { return TASKS[k] || k; }).join("، ")) + "</td><td>" + ((r.failed || []).length ? '<b class="neg">' + esc(r.failed.map(function (k) { return TASKS[k] || k; }).join("، ")) + "</b>" : '<span class="pos">✓</span>') +
        "</td><td>" + (Object.keys(r.ex || {}).length ? esc(Object.keys(r.ex).map(function (k) { return k + " ×" + r.ex[k]; }).join("، ")) : "—") + "</td></tr>";
    }).join("");
    app.innerHTML = '<section><h2>سلامت اجراها <small>۲۴ ساعت گذشته</small></h2><div class="grid g4">' +
      statCard("آخرین اجرا", esc(ago(last ? last.ts : null)), last ? "مدت: " + fa(Math.round(last.dur || 0)) + " ثانیه" : "هنوز ثبت نشده") +
      statCard("تعداد اجرا", fa(day.length), "میانه‌ی مدت " + (med == null ? "—" : fa(Math.round(med)) + " ثانیه") + " · بیشترین " + (mx == null ? "—" : fa(Math.round(mx)) + " ثانیه")) +
      statCard("اجراهای خطادار", '<span class="' + (failed ? "neg" : "pos") + '">' + fa(failed) + "</span>", failed ? "بخشی از اجرا خطا داده؛ جدول پایین را ببینید" : "همه‌ی اجراها کامل بودند") +
      statCard("پشتیبان", '<span class="' + (backupBad ? "neg" : "") + '">' + esc(h.last_backup ? ago(h.last_backup) : "—") + "</span>", "حالت ذخیره: " + esc(h.storage || "—") + (h.backup_min ? " · هر " + fa(h.backup_min) + " دقیقه" : "")) +
      "</div></section>" +
      '<section><h2>صرافی‌ها <small>خطاها در ۲۴ ساعت گذشته</small></h2>' + (Object.keys(ex).length ? Object.keys(ex).map(function (k) { return '<span class="chip">' + esc(k) + " <b>" + fa(ex[k]) + "</b> خطا</span>"; }).join("") +
        '<p class="muted small">هر خطا یعنی یک درخواست به آن صرافی جواب نداد و ربات همان را از صرافی بعدی (OKX، Bitget و ...) گرفت. فقط اگر همه‌ی صرافی‌ها جواب ندهند، چیزی از دست می‌رود.</p>' +
        Object.keys(exErr).map(function (k) { return '<div class="exerr"><b>' + esc(k) + '</b> <small class="muted">آخرین خطا ' + esc(ago(exErr[k][0])) + '</small><code>' + esc(exErr[k][1]) + "</code></div>"; }).join("")
        : '<span class="pos">هیچ خطایی ثبت نشده است.</span>') + "</section>" +
      skipsSection() +
      '<section><h2>اجراهای اخیر</h2><div class="chartbox small" id="dur-chart"></div>' +
      (rows ? '<div class="scroll"><table><thead><tr><th>شروع</th><th>مدت اجرا</th><th>بخش‌ها</th><th>خطا</th><th>خطای صرافی</th></tr></thead><tbody>' + rows + "</tbody></table></div>" : '<div class="empty">گزارش اجرا هنوز ثبت نشده است. بعد از اولین اجرای ربات با کد جدید پر می‌شود.</div>') + "</section>";
    var el = document.getElementById("dur-chart");
    var c = runs.length ? makeChart(el, {rightPriceScale: {scaleMargins: {top: 0.1, bottom: 0}}}) : null;
    if (!c) { if (el) el.remove(); return; }
    var s = c.addHistogramSeries({priceFormat: {type: "custom", formatter: function (v) { return v.toFixed(0) + "s"; }}, priceLineVisible: false});
    s.setData(uniq(runs.slice().reverse().map(function (r) { return {time: tsec(r.ts), value: r.dur || 0, color: (r.failed || []).length ? cssv("--neg") : (r.dur || 0) > 60 ? cssv("--warn") : cssv("--acc")}; })));
    c.timeScale().fitContent();
  }

  // ------------------------------------------------------------------ market: mood and calendar
  function moodSection() {
    var fng = MK.fng || [], btc = deriv("BTC"), eth = deriv("ETH");
    if (!fng.length && !btc && !eth) return "";
    var f = fng.length ? fng[fng.length - 1] : null;
    var FNG = {"Extreme Fear": "ترس شدید", "Fear": "ترس", "Neutral": "خنثی", "Greed": "طمع", "Extreme Greed": "طمع شدید"};
    var fcol = f ? (f[1] < 25 ? "--neg" : f[1] < 45 ? "--warn" : f[1] < 56 ? "--muted" : f[1] < 75 ? "--pos" : "--pos") : "--muted";
    function coin(name, d) {
      if (!d) return "";
      return '<div class="stat"><span class="l">' + name + "</span>" +
        "<div>فاندینگ " + fpct(d.funding != null ? d.funding * 100 : null, 4) + "</div>" +
        "<div>اوپن‌اینترست " + musd(d.oi_usd) + " " + fpct(d.oi_change, 1) + "</div>" +
        "<div>لانگ/شورت " + (d.lsr_account != null ? '<span class="ltr">' + d.lsr_account.toFixed(2) + "</span>" : "—") + "</div>" +
        "<div>لیکوئید ۲۴س: لانگ " + musd(d.liq_long_usd) + " · شورت " + musd(d.liq_short_usd) + "</div></div>";
    }
    return '<section><h2>حال‌وهوای بازار</h2><div class="grid g3">' +
      (f ? '<div class="stat"><span class="l">ترس و طمع</span><div class="fng"><b style="color:var(' + fcol + ')">' + fa(f[1]) + "</b> " + esc(FNG[f[2]] || f[2]) +
        '</div><div class="fngbar"><i style="right:' + f[1] + '%"></i></div>' + sparkSvg(fng.map(function (x) { return x[1]; }), 0, 100) +
        "<small>" + (fng.length > 7 ? "هفته‌ی پیش: " + fa(fng[fng.length - 8][1]) : "") + "</small></div>" : "") +
      coin("BTC", btc) + coin("ETH", eth) + "</div></section>";
  }
  function sparkSvg(vals, lo, hi) {
    if (vals.length < 2) return "";
    var W = 200, H = 40;
    var pts = vals.map(function (v, i) { return (i / (vals.length - 1) * W).toFixed(1) + "," + (H - (v - lo) / (hi - lo) * H).toFixed(1); }).join(" ");
    return '<svg class="spark" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none"><polyline points="' + pts + '"/></svg>';
  }
  // ------------------------------------------------------------------ summary (2026-11-15, Ali: "one page with the most important
  // things from every page and what is new, so I can decide from it")
  var SEEN_KEY = "seen_at";
  var seenAt = (function () { try { return Number(localStorage.getItem(SEEN_KEY)) || 0; } catch (e) { return 0; } })();
  var EVFA = {created: "سیگنال جدید", filled: "ورود انجام شد", tp1: "TP1 خورد", tp2: "TP2 خورد", tp3: "بسته با سود (خروج دنباله‌رو)",
    sl: "استاپ خورد", breakeven: "سر به سر بسته شد", expired: "منقضی شد", cancelled: "لغو شد"};
  var EVCLS = {tp1: "pos", tp2: "pos", tp3: "pos", sl: "neg", breakeven: "muted", expired: "muted", cancelled: "muted", created: "acc", filled: ""};
  function sumFeed(hours) {
    var since = Date.now() - hours * 3600000, out = [];
    trades().forEach(function (t) {
      if (t.created >= since) out.push({ts: t.created, k: "created", t: t});
      (t.events || []).forEach(function (e) { if (e[1] >= since && EVFA[e[0]]) out.push({ts: e[1], k: e[0], t: t, px: e[2]}); });
    });
    ((D.news_view || {}).items || []).forEach(function (n) {
      if ((n.stars || 0) >= 3 && (n.pub || n.ts) >= since) out.push({ts: n.pub || n.ts, k: "news", n: n});
    });
    return out.sort(function (a, b) { return b.ts - a.ts; });
  }
  function feedLine(e) {
    var isNew = e.ts > seenAt && seenAt ? ' <span class="badge new">جدید</span>' : "";
    if (e.k === "news") {
      var n = e.n;
      return '<li><span class="ft">' + esc(jfmt(e.ts, "time")) + '</span><span class="stars">' + "★★★★★".slice(0, n.stars) + "</span> " +
        (n.coins || []).slice(0, 3).map(function (c) { return "<b>" + esc(c) + "</b>"; }).join(" ") + " " + trT(n.title) + isNew + "</li>";
    }
    var t = e.t, r = e.k === "tp1" || e.k === "tp2" || e.k === "tp3" || e.k === "sl" || e.k === "breakeven" ? (e.k === "tp1" || e.k === "tp2" ? "" : " " + RU(t.net_r, tPnl(t))) : "";
    return '<li class="click" data-open="' + esc(t.id) + '"><span class="ft">' + esc(jfmt(e.ts, "time")) + '</span><b class="' + (EVCLS[e.k] || "") + '">' + EVFA[e.k] + "</b> · <b>" + esc(t.base) + "</b> " +
      side(t.side) + " " + kind(t) + ' <small class="muted">' + esc(sysShort(t.system)) + (e.k === "created" ? " · " + esc(t.grade) + " " + fa(Math.round(t.score || 0)) : "") + "</small>" + r + isNew + "</li>";
  }
  function sumAlerts() {
    var out = [], now = Date.now();
    // 1) system health
    var s0 = (D.systems || [])[0] || {}, run = (s0.last_runs || {})["run-1h"] || (s0.last_runs || {})["run-15m"];
    if (!run || now - run > 5 * 60000) out.push(["neg", "ربات " + esc(ago(run)) + " بررسی کرده؛ اجرا عقب افتاده است.", "sys"]);
    (D.systems || []).forEach(function (s) { if (s.paused_until) out.push(["warn", esc(s.name) + ": بعد از ضررهای پشت‌سرهم تا " + esc(jfmt(s.paused_until)) + " سیگنال نمی‌دهد.", "mkt"]); });
    // 1b) the market changed regime in the last day: say it once with what it did to the positions
    var why = regimeReason();
    if (why && why.prev && why.h < 24) out.push([REGCLS[why.cur.label] === "pos" ? "pos" : "warn", "رژیم بازار: " + why.html, "reg"]);
    // 2) economic events soon
    upcomingEvents(3).forEach(function (e) {
      out.push(["warn", "<b>" + esc(e.title) + "</b> (" + esc((CCY[e.country] || ["", e.country])[1]) + ") " + (e.ts > now ? '<span data-until="' + e.ts + '">' + esc(untilTxt(e.ts)) + "</span>" : "همین الان") +
        "؛ ورود تازه را کوچک‌تر بگیرید یا صبر کنید.", "cal"]);
    });
    // 3) open positions close to their stop or target
    openTrades().forEach(function (t) {
      var r = openR(t);
      if (r == null) return;
      var curr = r - (t.gross_r || 0);                       // the part still open, in R
      if (t.status === "active" && curr <= -0.7) out.push(["neg", "<b>" + esc(t.base) + "</b> " + side(t.side) + " نزدیک استاپ است (" + R(r) + ").", "chart/" + t.id]);
      else if (t.status === "active" && r >= 1.6) out.push(["pos", "<b>" + esc(t.base) + "</b> " + side(t.side) + " نزدیک TP1 است (" + R(r) + ").", "chart/" + t.id]);
    });
    // 3b) good signals the book refused in the last 24 h (full cap and the rest)
    skips(1).forEach(function (s) {
      out.push(["warn", "سیگنال <b>" + esc(s.base) + "</b> " + side(s.side) + " رده " + esc(s.grade) + " ارسال نشد: " + esc((SKIPR[s.reason] || [s.reason])[0]) + ".", "sys"]);
    });
    // 4) the AI reviewers disagree with an open signal
    var ai = D.ai || {};
    (ai.open || []).forEach(function (r) {
      if (r.consensus != null && r.consensus <= 30 && r.approved) out.push(["warn", "داوران هوش مصنوعی به <b>" + esc(r.base) + "</b> " + side(r.side) + " فقط " + aiPill(r.consensus) + " داده‌اند (مشورتی).", "ai"]);
    });
    // 5) strong news on coins we hold or might trade
    var held = {}; openTrades().concat(pendingTrades()).forEach(function (t) { held[t.base] = 1; });
    ((D.news_view || {}).items || []).forEach(function (n) {
      if ((n.pub || n.ts) < now - 6 * 3600000) return;
      var mine = (n.coins || []).filter(function (c) { return held[c]; });
      if (mine.length || n.stars >= 4) out.push([n.level < 0 ? "neg" : "acc", (mine.length ? "خبر درباره‌ی پوزیشن شما: " : "خبر مهم: ") + "<b>" + esc((n.coins || []).slice(0, 3).join(" ")) + "</b> " + trT(n.title), "news"]);
    });
    return out;
  }
  function sumDirection(s) {
    var r = s.regime || {}, m = s.majors || {};
    var trend = r.bias === "short" ? -1 : r.bias === "long" ? 1 : (r.trend || 0);
    var maj = (m.btc || 0) + (m.eth || 0), dir = trend || (maj <= -4 ? -1 : maj >= 4 ? 1 : 0);
    var best = (s.candidates || []).filter(function (c) { return dir && c.side === dir; })[0];
    return '<div class="sdir"><span class="l">' + esc(s.name) + '</span><b class="' + (dir > 0 ? "pos" : dir < 0 ? "neg" : "muted") + '">' +
      (dir > 0 ? "صعودی ▲ · لانگ در جهت روند" : dir < 0 ? "نزولی ▼ · شورت در جهت روند" : "بدون جهت روشن") + "</b>" +
      '<small class="muted">' + esc(REG[r.name] || r.name || "—") + " · " + esc(BIAS[r.bias] || "") +
      (best ? " · بهترین کاندید: " + esc(best.base) + " " + fa(Math.round(best.score)) : "") + "</small></div>";
  }
  function dayChange(key) {                   // the account's equity now against 24 h ago (closed trades only before now)
    var a = D.accounts[key], c = a.eq_curve && a.eq_curve.length ? a.eq_curve : a.curve || [], cut = Date.now() - 86400000, before = a.start;
    c.forEach(function (p) { if (p[0] <= cut) before = p[1]; });
    return accLive(key).equity - before;
  }
  function summaryTab() {
    var now = Date.now(), open = openTrades(), pend = pendingTrades();
    var openUsd = sumOf(open, function (t) { return floatUsd(t); });
    var atRisk = open.filter(function (t) { return t.status === "active"; }).length;
    var feed = sumFeed(24), alerts = sumAlerts(), newN = feed.filter(function (e) { return seenAt && e.ts > seenAt; }).length;
    var fng = (MK.fng || []).slice(-1)[0], btc = deriv("BTC");
    var kpis = '<div class="grid g4">' +
      ACC.map(function (x) {
        return '<div class="stat"><span class="l">' + esc(x[1]) + '</span><span class="v ltr" id="sumv-' + x[0] + '"></span><small id="sums-' + x[0] + '"></small></div>';
      }).join("") +
      statCard("پوزیشن‌های باز", fa(open.length) + ' <small class="muted">· ' + fa(pend.length) + " سفارش در انتظار</small>", 'سود/زیان باز <span id="sumopen">' + usd(openUsd) + "</span> · " + fa(atRisk) + " تا هنوز در ریسک") +
      "</div>";
    var rw = regimeReason();
    var mood = '<div class="grid g4 sumrow">' + (D.systems || []).map(sumDirection).join("") +
      (rw ? '<a class="sdir" href="#reg"><span class="l">رژیم قیمت (۴ ساعته)</span><b class="' + REGCLS[rw.cur.label] + '">' + REGFA[rw.cur.label] + "</b>" +
        '<small class="muted">از ' + dur(rw.h) + " پیش" + (rw.prev ? "، قبلش " + REGFA[rw.prev.label] : "") + " · " + fa(rw.c.n) + " پوزیشن در این رژیم</small></a>" : "") +
      (fng || btc ? '<div class="sdir"><span class="l">حال بازار</span><b>' + (fng ? "ترس و طمع " + fa(fng[1]) : "") + "</b>" +
        '<small class="muted">' + (btc && btc.change != null ? "BTC ۲۴ ساعت " + pct(btc.change, 1) : "") + (btc && btc.funding != null ? " · فاندینگ " + fpct(btc.funding * 100, 4) : "") + "</small></div>" : "") + "</div>";
    var alertHtml = alerts.length ? '<ul class="alerts">' + alerts.slice(0, 10).map(function (a) {
      return '<li class="' + a[0] + '"><a href="#' + esc(a[2]) + '">' + a[1] + "</a></li>";
    }).join("") + "</ul>" : '<p class="pos">چیزی نیاز به اقدام فوری ندارد.</p>';
    var posHtml = open.concat(pend).length ? '<div class="scroll"><table data-defsort="6:desc"><thead><tr><th>کوین</th><th>جهت</th><th>نوع</th><th>اهرم</th><th>وضعیت</th><th>سود/زیان</th><th>سود٪</th><th>داوران AI</th></tr></thead><tbody>' +
      open.concat(pend).map(function (t) {
        var a = aiRow(t.id);
        return '<tr class="click" data-open="' + esc(t.id) + '"><td><b>' + esc(t.base) + "</b> <small class=\"muted\">" + esc(sysShort(t.system)) + "</small></td><td>" + side(t.side) + "</td><td>" + kind(t) +
          "</td>" + levCell(t) + "<td>" + esc(t.status_fa) + "</td><td>" + (t.filled_at ? RU(openR(t), pnlUsd(t)) + splitNote(t) : "—") + "</td>" + pctCell(t) + '<td data-v="' + (a && a.consensus != null ? a.consensus : "") + '">' + (a ? aiPill(a.consensus) : "—") + "</td></tr>";
      }).join("") + "</tbody>" + totRow([{span: 5, v: "جمع " + fa(open.length) + " پوزیشن + " + fa(pend.length) + " سفارش"}, posTot(open), "", ""]) + "</table></div>" : '<div class="empty">الان پوزیشن یا سفارشی باز نیست.</div>';
    var revs = ((D.ai || {}).reviewers || []).filter(function (r) { return r.key !== "base" && r.n; }).sort(function (a, b) { return (b.trust || 0) - (a.trust || 0); });
    var aiLine = revs.length ? "معتبرترین داور: <b>" + esc(revs[0].name) + "</b> با اعتبار " + fa(revs[0].trust || 0) + " از ۱۰۰ (" + esc((AIV[revs[0].verdict] || AIV.few)[0]) + ")" : "داوران هنوز داده‌ی کافی ندارند.";
    var cal = (MK.calendar || []).filter(function (e) { return e.ts > now && e.ts < now + 48 * 3600000 && e.w >= 4; }).slice(0, 4);
    app.innerHTML = eventBanner() +
      '<section><h2>خلاصه ' + qi("مهم‌ترین چیزهای همه‌ی تب‌ها در یک صفحه.\nردیف اول: موجودی سه حساب ۱۰۰ دلاری و تغییرشان در ۲۴ ساعت گذشته، و تعداد پوزیشن‌های باز.\nردیف دوم: جهتی که هر سیستم الان در بازار می‌بیند و حال کلی بازار.\nبرچسب «جدید» یعنی بعد از آخرین باری که این صفحه را باز کردید اتفاق افتاده است.") + ' <small>' + (seenAt ? "آخرین بازدید شما " + esc(ago(seenAt)) + (newN ? " · " + fa(newN) + " اتفاق تازه" : " · اتفاق تازه‌ای نیست") : "مهم‌ترین‌های همه‌ی صفحه‌ها") + "</small></h2>" +
      kpis + mood + "</section>" +
      '<div class="grid g2"><section><h2>نیاز به توجه ' + qi("چیزهایی که شاید الان تصمیمی بخواهند: ربات عقب افتاده، رویداد اقتصادی پراثر نزدیک است، پوزیشنی نزدیک استاپ یا TP1 است، داوران هوش مصنوعی به سیگنالی نمره‌ی پایین داده‌اند، یا خبر مهمی درباره‌ی کوینی آمده که در آن پوزیشن دارید.\nروی هر مورد بزنید تا به تب مربوط بروید.") + ' <small>' + fa(alerts.length) + "</small></h2>" + alertHtml + "</section>" +
      "<section><h2>۲۴ ساعت گذشته " + qi("هر اتفاق ۲۴ ساعت گذشته به ترتیب زمان: سیگنال تازه، ورود، TP، استاپ و لغو، به‌علاوه‌ی خبرهای ۳ ستاره و بیشتر. روی هر معامله بزنید تا جزئیاتش باز شود.") + " <small>سیگنال‌ها، ورود و خروج‌ها، خبرهای ۳ ستاره به بالا</small></h2>" +
      (feed.length ? '<ul class="feed">' + feed.slice(0, 18).map(feedLine).join("") + "</ul>" : '<div class="empty">در ۲۴ ساعت گذشته اتفاقی نیفتاده است.</div>') + "</section></div>" +
      '<div class="grid g2"><section><h2>پوزیشن‌ها و سفارش‌ها ' + qi("همه‌ی پوزیشن‌های باز و سفارش‌های در انتظار هر دو سیستم، با سود و زیان زنده و جمع‌بندی نمره‌ی داوران هوش مصنوعی (شانس سود از ۰ تا ۱۰۰؛ فقط مشورتی).") + '</h2>' + posHtml + "</section>" +
      "<section><h2>پیش رو " + qi("رویدادهای اقتصادی پراثر ۴۸ ساعت آینده و این‌که فعلاً کدام داور هوش مصنوعی معتبرتر بوده است.") + "</h2>" +
      (cal.length ? "<h3>رویدادهای اقتصادی پراثر (۴۸ ساعت)</h3><ul class=\"feed\">" + cal.map(function (e) {
        return '<li><span class="ft">' + esc(jfmt(e.ts, "dt")) + "</span>" + (CCY[e.country] || [""])[0] + " <b>" + esc(e.title) + '</b> <small data-until="' + e.ts + '">' + esc(untilTxt(e.ts)) + "</small></li>";
      }).join("") + "</ul>" : "<p class=\"muted\">رویداد اقتصادی پراثری در ۴۸ ساعت آینده نیست.</p>") +
      "<h3>هوش مصنوعی</h3><p>" + aiLine + "</p></section></div>";
    try { localStorage.setItem(SEEN_KEY, String(now)); } catch (e) {}
    sumKpis();
  }
  // 2026-11-17 (Ali): each account card is the final number, booked + open together, and moves with live prices
  function sumKpis() {
    ACC.forEach(function (x) {
      var v = document.getElementById("sumv-" + x[0]), sb = document.getElementById("sums-" + x[0]);
      if (!v) return;
      var a = D.accounts[x[0]], l = accLive(x[0]);
      v.textContent = "$" + l.equity.toFixed(2);
      if (sb) sb.innerHTML = "قطعی " + usd(a.balance, false) + " + باز " + usd(l.unreal) + "<br>۲۴ ساعت " + usd(dayChange(x[0])) +
        " · از شروع " + pct((l.equity - a.start) / a.start * 100, 1);
    });
    var so = document.getElementById("sumopen");
    if (so) so.innerHTML = usd(sumOf(openTrades(), function (t) { return floatUsd(t); }));
  }

  // ------------------------------------------------------------------ AI reviewers (2026-11-15, Ali: several AIs that score
  // every signal, learn from every closed trade, and are graded themselves: how far can we rely on them?)
  var AIV = {few: ["داده کم", "gray", "هنوز کمتر از ۲۰ معامله‌ی نتیجه‌دار نمره داده؛ هیچ قضاوتی درباره‌اش ممکن نیست."],
    good: ["بهتر از حدس ساده", "ok", "نمره‌هایش به‌طور معنادار بهتر از «نرخ برد تا امروز» نتیجه را پیش‌بینی کرده است."],
    maybe: ["شاید بهتر", "watch", "بهتر از حدس ساده بوده، ولی هنوز آن‌قدر معامله نیست که مطمئن باشیم شانس نبوده."],
    same: ["هم‌اندازه‌ی حدس ساده", "gray", "فعلاً چیزی بیشتر از نرخ برد کلی نمی‌داند؛ به نمره‌اش تکیه نکنید."],
    bad: ["بدتر از حدس ساده", "short", "نمره‌هایش از حدس ساده بدتر بوده؛ به آن تکیه نکنید."]};
  var AISTAT = {pending: "در انتظار ورود", active: "باز", tp1: "TP1 خورده", tp2: "TP2 خورده"};
  function aiData() { return D.ai || {}; }
  function aiRow(id) {
    var a = aiData();
    return (a.open || []).concat(a.recent || []).filter(function (r) { return r.id === id; })[0] || null;
  }
  function aiName(k) {
    var r = (aiData().reviewers || []).filter(function (x) { return x.key === k; })[0];
    return r ? r.name : k;
  }
  function aiPill(p) {
    if (p == null) return '<span class="muted">—</span>';
    var c = p >= 60 ? "pos" : p <= 40 ? "neg" : "warn";
    return '<b class="aip ' + c + '">' + fa(Math.round(p)) + "</b>";
  }
  function aiBox(t) {
    var r = t && aiRow(t.id);
    if (!r || !r.scores) return "";
    var ks = Object.keys(r.scores).filter(function (k) { return k !== "base"; });
    return '<div class="aibox"><div class="l">نمره‌ی داوران هوش مصنوعی <small>(شانس سود، ۰ تا ۱۰۰)</small></div><div class="aiv">' +
      '<span>جمع‌بندی ' + aiPill(r.consensus) + "</span>" + ks.map(function (k) {
        return '<span title="' + esc(r.why && r.why[k] || "") + '">' + esc(aiName(k)) + " " + aiPill(r.scores[k]) + "</span>";
      }).join("") + '<span class="muted">مبنا ' + fa(Math.round(r.scores.base || 0)) + '</span></div><small class="muted">فقط مشورتی است؛ <a href="#ai">اعتبار هر داور ←</a></small></div>';
  }
  function aiTab() {
    var a = aiData(), revs = (a.reviewers || []).filter(function (r) { return r.key !== "base"; });
    if (!a.reviewers) { app.innerHTML = '<section><h2>داوران هوش مصنوعی</h2><p class="muted">هنوز داده‌ای نیست.</p></section>'; return; }
    var base = (a.reviewers || []).filter(function (r) { return r.key === "base"; })[0] || {};
    function num(x, d) { return x == null ? "—" : '<span class="ltr">' + Number(x).toFixed(d == null ? 2 : d) + "</span>"; }
    var cards = revs.map(function (r) {
      var v = AIV[r.verdict] || AIV.few, L = r.live || {};
      var status = r.kind === "مدل زبانی" ? (r.error === "no key" ? '<span class="warn">کلید API ندارد</span>' : r.error ? '<span class="neg" title="' + esc(r.why || "") + '">' + esc({"API failed": "API جواب نداد", "daily limit": "سقف روزانه‌ی درخواست پر شد", "no free model": "مدل رایگانی پیدا نشد"}[r.error] || r.error) + "</span>" +
          (r.why ? '<code class="aiwhy">' + esc(r.why) + "</code>" : "") :
        r.ran ? "آخرین اجرا " + esc(ago(r.ran)) : '<span class="muted">هنوز اجرا نشده</span>') : "هر دقیقه، روی سرور";
      return '<div class="aicard"><div class="aih"><b>' + esc(r.name) + '</b> <small class="muted">' + esc(r.kind) + (r.model ? ' · <span class="ltr">' + esc(r.model) + "</span>" : "") + '</small><span class="badge ' + v[1] + '">' + v[0] + "</span></div>" +
        '<div class="trust" title="اعتبار: چقدر بهتر از حدس ساده، چقدر مطمئن و با چند معامله"><i style="width:' + (r.trust || 0) + '%"></i></div>' +
        '<div class="muted small">اعتبار ' + fa(r.trust || 0) + " از ۱۰۰ · " + esc(v[2]) + "</div>" +
        '<div class="grid g2 aist">' +
        "<div>معامله‌ی نتیجه‌دار<span class=\"v\">" + fa(r.n || 0) + "</span><small>زنده: " + fa(L.n || 0) + " · کل نمره‌ها " + fa(r.scored || 0) + "</small></div>" +
        "<div>پیش‌بینی درست<span class=\"v\">" + (r.hit == null ? "—" : fa(Math.round(r.hit)) + "٪") + "</span><small>نمره‌ی ۵۰+ و سود، یا زیر ۵۰ و ضرر</small></div>" +
        "<div>خطای Brier<span class=\"v\">" + num(r.brier, 3) + "</span><small>مبنا " + num(r.brier_base, 3) + " (کمتر بهتر)</small></div>" +
        "<div>جداسازی برد از باخت (AUC)<span class=\"v\">" + num(r.auc, 2) + "</span><small>۰٫۵ یعنی شانسی، ۱ یعنی کامل</small></div>" +
        "<div>وقتی نمره‌ی ۶۰+ داد<span class=\"v\">" + (r.hi_r == null ? "—" : R(r.hi_r)) + "</span><small>میانگین نتیجه در " + fa(r.hi_n || 0) + " معامله</small></div>" +
        "<div>وقتی نمره‌ی ۴۰− داد<span class=\"v\">" + (r.lo_r == null ? "—" : R(r.lo_r)) + "</span><small>میانگین نتیجه در " + fa(r.lo_n || 0) + " معامله</small></div>" +
        "</div>" + (r.calib && r.calib.length ? '<table class="calib"><thead><tr><th>نمره</th><th>تعداد</th><th>میانگین نمره</th><th>برد واقعی</th></tr></thead><tbody>' +
          r.calib.map(function (c) { return "<tr><td>" + fa(Math.round(c[0] * 100)) + "–" + fa(Math.round(c[1] * 100)) + "</td><td>" + fa(c[2]) + "</td><td>" + fa(Math.round(c[3])) + "٪</td><td>" + fa(Math.round(c[4])) + "٪</td></tr>"; }).join("") +
          "</tbody></table>" : "") + '<div class="muted small">' + status + "</div></div>";
    }).join("");
    var keys = revs.map(function (r) { return r.key; });
    var head = "<tr><th>سیگنال</th><th>کوین</th><th>سیستم</th><th>وضعیت</th><th>جمع‌بندی</th>" + keys.map(function (k) { return "<th>" + esc(aiName(k)) + "</th>"; }).join("") + "<th>مبنا</th></tr>";
    function line(r, res) {
      return '<tr class="click" data-open="' + esc(r.id) + '"><td data-v="' + r.created + '">' + esc(jfmt(r.created)) + "</td><td><b>" + esc(r.base) + "</b> " + side(r.side) + "</td><td>" + esc(sysShort(r.system)) + " " +
        (r.approved ? '<span class="badge ok">تأیید</span>' : '<span class="badge watch">Watch</span>') + "</td><td>" +
        (res ? R(r.r) : esc(AISTAT[r.status] || r.status)) + "</td><td>" + aiPill(r.consensus) + "</td>" +
        keys.map(function (k) {
          var p = r.scores[k];
          var right = res && p != null ? ((p >= 50) === r.win ? ' <small class="pos">✓</small>' : ' <small class="neg">✗</small>') : "";
          return '<td title="' + esc(r.why && r.why[k] || "") + '">' + aiPill(p) + right + "</td>";
        }).join("") + "<td>" + fa(Math.round(r.scores.base || 0)) + "</td></tr>";
    }
    var open = (a.open || []), rec = (a.recent || []);
    var lessons = Object.keys(a.lessons || {}).map(function (k) {
      var l = a.lessons[k] || [];
      if (!l.length) return "";
      return "<div><h3>درس‌های " + esc(aiName(k)) + "</h3><ul class=\"lessons\">" + l.map(function (x) { return "<li>" + esc(x[2]) + ' <small class="muted">' + esc(ago(x[0])) + "</small></li>"; }).join("") + "</ul></div>";
    }).join("");
    app.innerHTML = '<section><h2>داوران هوش مصنوعی <small>مشورتی؛ چیزی را در معامله‌ها تغییر نمی‌دهد</small></h2>' +
      '<p class="muted">هر داور به هر سیگنال (تأییدشده و Watch) نمره‌ی ۰ تا ۱۰۰ می‌دهد: شانس اینکه با سود بسته شود. نمره همان لحظه ثبت و قفل می‌شود و بعد از بسته شدن معامله با نتیجه مقایسه می‌شود. ' +
      "معیار مقایسه «مبنا» است: همیشه نرخ برد تا امروز را می‌گوید (الان " + (a.base_rate == null ? "—" : fa(Math.round(a.base_rate)) + "٪") + " از " + fa(a.n_done || 0) + " معامله). " +
      "داوری ارزش دارد که از مبنا بهتر باشد. نمره‌ی معامله‌هایی که قبل از شروع داوران باز یا بسته شده بودند «گذشته‌نگر» است: فقط با معامله‌هایی که پیش از باز شدن آن بسته شده بودند. " +
      "معامله‌ای که از TP1 رد شده (استاپ روی ورود و سود گرفته شده) برد حساب می‌شود.</p>" +
      '<div class="grid g3">' + cards + "</div></section>" +
      '<section><h2>نمره‌ی سیگنال‌های باز و در انتظار</h2>' + (open.length ? '<div class="scroll"><table class="aitab"><thead>' + head + "</thead><tbody>" + open.map(function (r) { return line(r, false); }).join("") + "</tbody></table></div>" : '<p class="muted">الان سیگنال بازی بدون نتیجه نیست.</p>') + "</section>" +
      '<section><h2>معامله‌های نتیجه‌دار اخیر <small>✓ پیش‌بینی درست · ✗ غلط</small></h2>' + (rec.length ? '<div class="scroll"><table class="aitab"><thead>' + head.replace("<th>وضعیت</th>", "<th>نتیجه</th>") + "</thead><tbody>" + rec.map(function (r) { return line(r, true); }).join("") + "</tbody></table></div>" : "") + "</section>" +
      (lessons ? '<section><h2>آنچه مدل‌های زبانی از معامله‌ها یاد گرفته‌اند</h2><div class="grid g2">' + lessons + "</div></section>" : "") +
      '<section><h2>داوران چطور کار می‌کنند</h2><ul class="muted">' +
      "<li><b>همسایه‌ها:</b> ۷ معامله‌ی بسته‌ی شبیه‌تر (امتیاز، رده، بخش‌های امتیاز، رژیم، قدرت BTC/ETH، فاندینگ، فاصله‌ی استاپ...) را پیدا می‌کند و نرخ بردشان را می‌گوید.</li>" +
      "<li><b>رگرسیون لجستیک:</b> از همه‌ی معامله‌های بسته یاد می‌گیرد کدام ویژگی‌ها با سود همراه بوده‌اند؛ با هر معامله‌ی تازه دوباره آموزش می‌بیند.</li>" +
      "<li><b>Gemini و GPT:</b> سیگنال، معامله‌های مشابه گذشته و درس‌هایی را که خودشان بعد از هر معامله نوشته‌اند می‌خوانند و نمره می‌دهند. روی GitHub اجرا می‌شوند (این سرویس‌ها سرور ایران را نمی‌پذیرند)، هر ۱۵ دقیقه.</li>" +
      "<li><b>اعتبار:</b> ترکیبی از اینکه چقدر از مبنا بهتر است (امتیاز مهارت Brier)، چقدر مطمئنیم شانس نبوده، و با چند معامله سنجیده شده. با کمتر از ۶۰ معامله هیچ داوری اعتبار کامل نمی‌گیرد.</li></ul></section>";
  }

  // ------------------------------------------------------------------ economic calendar (2026-11-15, Ali: "much fuller, so I can decide from it")
  var CCY = {USD: ["🇺🇸", "آمریکا"], EUR: ["🇪🇺", "اروپا"], GBP: ["🇬🇧", "انگلیس"], JPY: ["🇯🇵", "ژاپن"], CNY: ["🇨🇳", "چین"],
    CAD: ["🇨🇦", "کانادا"], AUD: ["🇦🇺", "استرالیا"], NZD: ["🇳🇿", "نیوزیلند"], CHF: ["🇨🇭", "سوئیس"], ALL: ["🌐", "همه"]};
  // [label, what it is, how it usually moves crypto, sign: +1 = above forecast is good for crypto, -1 = bad, 0 = depends]
  var CALKIND = {
    fomc: ["فدرال رزرو", "جلسه، صورت‌جلسه یا سخنرانی رئیس فدرال رزرو. لحن آن مسیر نرخ بهره را نشان می‌دهد.", "لحن سخت‌گیر (هاوکیش) معمولاً کریپتو را پایین می‌برد و لحن نرم (داویش) بالا. نوسان شدید در دقایق اول عادی است.", 0],
    rate: ["نرخ بهره", "تصمیم بانک مرکزی درباره‌ی نرخ بهره.", "نرخ بالاتر از انتظار = پول گران‌تر = معمولاً منفی برای کریپتو؛ کاهش بیش از انتظار مثبت.", -1],
    cpi: ["تورم مصرف‌کننده (CPI)", "تغییر قیمت سبد مصرف خانوار؛ مهم‌ترین عدد تورم برای فدرال رزرو.", "بالاتر از پیش‌بینی = احتمال سخت‌گیری بیشتر = معمولاً ریزش کریپتو. پایین‌تر = معمولاً رشد.", -1],
    ppi: ["تورم تولیدکننده (PPI)", "تغییر قیمت در سطح تولید؛ نشانه‌ی زودهنگام تورم مصرف‌کننده.", "بالاتر از پیش‌بینی معمولاً منفی، پایین‌تر مثبت؛ اثرش از CPI کمتر است.", -1],
    pce: ["تورم PCE", "شاخص تورم مورد علاقه‌ی فدرال رزرو (و درآمد/هزینه‌ی خانوار).", "بالاتر از پیش‌بینی معمولاً منفی برای کریپتو.", -1],
    nfp: ["اشتغال غیرکشاورزی (NFP)", "تعداد شغل‌های جدید آمریکا در ماه؛ بزرگ‌ترین گزارش بازار کار.", "عدد خیلی قوی = فشار کمتر برای کاهش نرخ = معمولاً منفی کوتاه‌مدت. عدد خیلی ضعیف = امید کاهش نرخ، ولی ترس رکود.", -1],
    adp: ["اشتغال ADP", "برآورد بخش خصوصی از اشتغال، دو روز پیش از NFP.", "مثل NFP ولی کم‌اثرتر.", -1],
    claims: ["مدعیان بیکاری", "تعداد درخواست‌های هفتگی بیمه‌ی بیکاری.", "عدد بالاتر = بازار کار ضعیف‌تر = امید کاهش نرخ؛ اثرش معمولاً کم است.", 1],
    unemp: ["نرخ بیکاری", "درصد بیکاران از نیروی کار.", "بیکاری بالاتر از انتظار معمولاً امید کاهش نرخ را بالا می‌برد (مثبت ملایم)، ولی اگر خیلی بد باشد ترس رکود.", 1],
    jolts: ["فرصت‌های شغلی (JOLTS)", "تعداد شغل‌های خالی.", "عدد قوی‌تر کمی منفی، ضعیف‌تر کمی مثبت.", -1],
    wages: ["دستمزد", "رشد دستمزدها؛ منبع تورم آینده.", "رشد بیشتر از انتظار معمولاً منفی.", -1],
    gdp: ["تولید ناخالص (GDP)", "رشد اقتصاد.", "بستگی دارد: رشد قوی ریسک‌پذیری را بالا می‌برد ولی امید کاهش نرخ را کم می‌کند.", 0],
    retail: ["خرده‌فروشی", "هزینه‌ی مصرف‌کننده‌ها.", "عدد خیلی قوی گاهی منفی (تورم)، خیلی ضعیف نشانه‌ی کندی اقتصاد.", 0],
    pmi: ["شاخص مدیران خرید (PMI/ISM)", "حال صنعت و خدمات؛ بالای ۵۰ یعنی رشد.", "اثر معمولاً متوسط و دوطرفه است.", 0],
    sentiment: ["اعتماد/انتظارات", "نظرسنجی از مصرف‌کننده‌ها یا کسب‌وکارها (و انتظار تورمی).", "اثر معمولاً کم؛ انتظار تورم بالاتر منفی.", 0],
    durable: ["کالاهای بادوام/تولید", "سفارش‌ها و تولید صنعتی.", "اثر کم.", 0],
    housing: ["مسکن", "فروش خانه و ساخت‌وساز.", "اثر کم.", 0],
    trade: ["تراز تجاری", "صادرات منهای واردات.", "اثر کم.", 0],
    auction: ["حراج اوراق", "فروش اوراق قرضه‌ی دولت.", "تقاضای ضعیف = بازده بالاتر = گاهی منفی برای کریپتو.", 0],
    speech: ["سخنرانی", "سخنرانی مقام‌های بانک مرکزی.", "اگر از مسیر نرخ بهره چیز تازه‌ای بگویند نوسان می‌دهد.", 0],
    holiday: ["تعطیلی", "بازارهای آن کشور بسته‌اند.", "حجم معاملات کم می‌شود؛ حرکت‌های تند و فیک بیشتر است.", 0],
    other: ["سایر", "", "", 0]
  };
  S.cal = {r: "all", c: "major", i: "hm", open: {}};
  function numOf(x) {
    var m = /^(-?[\d.]+)\s*([KMBT%])?/i.exec(String(x || "").replace(/,/g, "").trim());
    if (!m) return null;
    var k = {K: 1e3, M: 1e6, B: 1e9, T: 1e12}[(m[2] || "").toUpperCase()] || 1;
    return Number(m[1]) * k;
  }
  function surprise(e) {                    // the actual against the forecast, read for crypto
    var a = numOf(e.actual), f = numOf(e.forecast), k = CALKIND[e.kind] || CALKIND.other;
    if (a == null || f == null) return "";
    if (a === f) return '<small class="muted">مطابق انتظار</small>';
    var good = k[3] ? (a > f ? k[3] : -k[3]) > 0 : null;
    return '<small class="' + (good == null ? "muted" : good ? "pos" : "neg") + '">' + (a > f ? "بالاتر" : "پایین‌تر") + " از پیش‌بینی" +
      (good == null ? "" : good ? " · معمولاً مثبت برای کریپتو" : " · معمولاً منفی برای کریپتو") + "</small>";
  }
  function wStars(w) { return '<span class="stars" title="اثر معمول روی کریپتو">' + "★★★★★".slice(0, w) + '<i>' + "★★★★★".slice(w) + "</i></span>"; }
  function calKeep(e) {                    // the country and importance filters
    var c = S.cal;
    if (c.c === "usd" && e.country !== "USD") return false;
    if (c.c === "major" && !{USD: 1, EUR: 1, GBP: 1, JPY: 1, CNY: 1}[e.country]) return false;
    if (c.i === "h" && e.impact !== "High") return false;
    if (c.i === "hm" && e.impact !== "High" && e.impact !== "Medium" && e.impact !== "Holiday") return false;
    return true;
  }
  function calFilter(all) {
    var now = Date.now(), c = S.cal;
    var dayEnd = function (ms) { var d = new Date(ms + 3.5 * 3600000); d.setUTCHours(24, 0, 0, 0); return d.getTime() - 3.5 * 3600000; };
    return all.filter(function (e) {
      if (!calKeep(e)) return false;
      if (c.r === "today") return e.ts >= now - 12 * 3600000 && e.ts < dayEnd(now);
      if (c.r === "24") return e.ts >= now - 3600000 && e.ts < now + 24 * 3600000;
      if (c.r === "up") return e.ts >= now - 3600000;
      return true;
    });
  }
  // ------------------------------------------------------------------ past releases (2026-11-16, Ali: "keep a week back, as an accordion, with the data and its effect on crypto")
  function btcChip(b) {                    // BTC's first hour after the release
    if (!b || b.h1 == null) return '<span class="muted">BTC: در انتظار</span>';
    return "بیت‌کوین ۱ ساعت بعد: " + pct(b.h1, 2);
  }
  function calExpect(e) {                  // +1 crypto was expected up, -1 down, 0 no reading, null no numbers
    var a = numOf(e.actual), f = numOf(e.forecast), k = CALKIND[e.kind] || CALKIND.other;
    if (a == null || f == null) return null;
    if (a === f || !k[3]) return 0;
    return (a > f ? k[3] : -k[3]) > 0 ? 1 : -1;
  }
  function btcVerdict(e) {
    var b = e.btc;
    if (!b || b.h1 == null) return '<span class="muted">واکنش BTC هنوز ثبت نشده (یک ساعت پس از انتشار ثبت می‌شود).</span>';
    var h1 = b.h1, ex = calExpect(e), quiet = Math.abs(h1) < 0.2;
    var dir = h1 > 0 ? "بالا رفت" : "پایین آمد";
    if (quiet) return "BTC در ساعت اول واکنش محسوسی نداشت (" + pct(h1, 2) + ")" + (b.swing_h1 >= 0.6 ? '، ولی دامنه‌ی نوسانش <span class="ltr">' + b.swing_h1.toFixed(2) + "%</span> بود: هر دو طرف را زد." : ".");
    if (ex === 1 || ex === -1) {
      var ok = (h1 > 0) === (ex > 0);
      return '<span class="' + (ok ? "pos" : "warn") + '">' + (ok ? "مطابق انتظار" : "خلاف انتظار") + "</span>: BTC در ساعت اول " + pct(h1, 2) + " " + dir +
        (ok ? "." : "؛ یعنی بازار به چیز دیگری (یا پیش‌خور شدن خبر) واکنش داد.");
    }
    return "BTC در ساعت اول " + pct(h1, 2) + " " + dir + ".";
  }
  function calDesc(e, same) {              // the explanation of a release: what it is, its data and BTC's move
    var k = CALKIND[e.kind] || CALKIND.other, b = e.btc || {}, out = [];
    if (k[1]) out.push("<b>" + esc(k[0]) + ":</b> " + esc(k[1]));
    if (e.actual || e.forecast || e.previous)
      out.push("<b>داده:</b> واقعی <b class=\"ltr\">" + esc(e.actual || "—") + "</b>، پیش‌بینی <span class=\"ltr\">" + esc(e.forecast || "—") +
        "</span>، قبلی <span class=\"ltr\">" + esc(e.previous || "—") + "</span> " + surprise(e));
    if (k[2]) out.push("<b>اثر معمول روی کریپتو:</b> " + esc(k[2]));
    if (e.ts < Date.now()) {
      out.push("<b>واکنش واقعی BTC:</b> ۱۵ دقیقه " + pct(b.m15, 2) + "، ۱ ساعت " + pct(b.h1, 2) + "، ۴ ساعت " + pct(b.h4, 2) +
        (b.swing_h1 != null ? ' · دامنه‌ی نوسان ساعت اول <span class="ltr">' + b.swing_h1.toFixed(2) + "%</span>" : "") +
        (b.p0 ? ' · قیمت لحظه‌ی انتشار <span class="ltr">$' + Math.round(b.p0).toLocaleString("en-US") + "</span>" : ""));
      out.push("<b>نتیجه:</b> " + btcVerdict(e));
      if (same && same.length) out.push('<small class="muted">هم‌زمان منتشر شد: ' + same.map(function (x) { return esc(x.country + " " + x.title); }).join("، ") +
        "؛ حرکت BTC حاصل همه‌ی این‌هاست.</small>");
    }
    return out.map(function (x) { return "<p>" + x + "</p>"; }).join("");
  }
  function calPast() {
    var now = Date.now(), all = (MK.calendar_past || []).filter(function (e) { return e.ts < now; });
    var list = all.filter(calKeep);
    var o = S.cal.past = S.cal.past || {};
    var days = [], byDay = {};
    list.forEach(function (e) {
      var d = jfmt(e.ts, "ymd");
      if (!byDay[d]) { byDay[d] = []; days.push(d); }
      byDay[d].push(e);
    });
    var moved = list.filter(function (e) { return e.btc && e.btc.h1 != null && Math.abs(e.btc.h1) >= 0.5 && e.w >= 3; }).length;
    var body = !list.length ? '<p class="muted">' + (all.length ? "با این فیلترها رویدادی در ۷ روز گذشته نیست." :
      "تاریخچه از امروز جمع می‌شود: منبع فقط این هفته و هفته‌ی بعد را می‌دهد، پس رویدادهای گذشته با هر بار ساخت پنل نگه داشته می‌شوند تا یک هفته.") + "</p>" :
      days.map(function (d) {
        var evs = byDay[d], hi = evs.filter(function (e) { return e.impact === "High"; }).length;
        return '<details class="calday" data-d="' + esc(d) + '"' + (o["d" + d] ? " open" : "") + "><summary><b>" +
          esc(new Intl.DateTimeFormat("fa-IR", {weekday: "long", timeZone: "Asia/Tehran"}).format(new Date(evs[0].ts))) + " " + esc(d) + "</b> " +
          '<small class="muted">' + fa(evs.length) + " رویداد" + (hi ? " · " + fa(hi) + " مهم" : "") + "</small></summary>" +
          evs.slice().reverse().map(function (e) {
            var c = CCY[e.country] || ["", e.country], key = e.ts + "|" + e.title + "|" + e.country;
            var same = list.filter(function (x) { return x.ts === e.ts && x !== e && x.w >= 2; });
            return '<details class="calev" data-k="' + esc(key) + '"' + (o[key] ? " open" : "") + "><summary>" +
              '<span class="num">' + esc(jfmt(e.ts, "time")) + "</span> " + c[0] + " <b>" + esc(e.title) + "</b> " + wStars(e.w || 0) +
              (e.actual ? ' <small class="muted">واقعی</small> <span class="ltr">' + esc(e.actual) + "</span>" +
                (e.forecast ? ' <small class="muted">پیش‌بینی</small> <span class="ltr">' + esc(e.forecast) + "</span>" : "") : "") +
              ' <span class="calbtc">' + (e.w >= 2 ? btcChip(e.btc) : "") + "</span></summary>" +
              '<div class="calexp">' + calDesc(e, same) + "</div></details>";
          }).join("") + "</details>";
      }).join("");
    return '<details class="calpast"' + (o.all ? " open" : "") + '><summary><b>رویدادهای ۷ روز گذشته</b> <small class="muted">' + fa(list.length) + " رویداد" +
      (moved ? " · " + fa(moved) + " مورد BTC را در ساعت اول ۰٫۵٪+ جابه‌جا کرد" : "") + " · برای باز شدن بزنید</small></summary>" +
      '<p class="muted">هر رویداد: داده‌ی منتشرشده در برابر پیش‌بینی، اثر معمولش روی کریپتو، و حرکت واقعی BTC در ۱۵ دقیقه، ۱ ساعت و ۴ ساعت بعد (از کندل‌های ۱۵ دقیقه‌ای گیت). ' +
      "فیلتر کشور و اهمیت پایین صفحه روی این فهرست هم اعمال می‌شود.</p>" + body + "</details>";
  }
  function calPastBind() {
    var o = S.cal.past = S.cal.past || {};
    app.querySelectorAll("details.calpast, details.calday, details.calev").forEach(function (d) {
      d.addEventListener("toggle", function (ev) {
        ev.stopPropagation();
        var k = d.classList.contains("calpast") ? "all" : d.classList.contains("calday") ? "d" + d.dataset.d : d.dataset.k;
        o[k] = d.open;
      });
    });
  }
  function calendarTab() {
    var all = MK.calendar || [], now = Date.now();
    if (!all.length) {
      app.innerHTML = '<section><h2>تقویم اقتصادی</h2><p class="muted">داده‌ی تقویم هنوز نرسیده است (هر ۳۰ دقیقه از faireconomy.media گرفته می‌شود).</p></section>';
      return;
    }
    var us = all.filter(function (e) { return e.country === "USD" && e.w >= 4; });
    var next = us.filter(function (e) { return e.ts > now; })[0];
    var today = all.filter(function (e) { return e.w >= 3 && Math.abs(e.ts - now) < 24 * 3600000 && e.ts > now - 12 * 3600000; });
    var weekHi = all.filter(function (e) { return e.impact === "High" && e.ts > now; });
    var cards = '<div class="grid g4">' +
      statCard("رویداد مهم بعدی آمریکا", next ? esc((CALKIND[next.kind] || CALKIND.other)[0]) : "—",
        next ? esc(next.title) + " · " + esc(jfmt(next.ts, "dt")) + ' · <b data-until="' + next.ts + '">' + esc(untilTxt(next.ts)) + "</b>" : "در دو هفته‌ی پیش رو نیست") +
      statCard("رویدادهای پراثر ۲۴ ساعت", fa(today.length), today.slice(0, 3).map(function (e) { return esc(e.country + " " + e.title); }).join(" · ") || "آرام") +
      statCard("رویدادهای «مهم» پیش رو", fa(weekHi.length), fa(weekHi.filter(function (e) { return e.country === "USD"; }).length) + " مورد از آمریکا") +
      statCard("پیشنهاد", next && next.ts - now < 3 * 3600000 ? '<span class="warn">احتیاط</span>' : '<span class="pos">عادی</span>',
        next && next.ts - now < 3 * 3600000 ? "تا انتشار " + esc(next.title) + " ورود تازه را کوچک‌تر بگیرید یا صبر کنید؛ استاپ‌ها ممکن است با نوسان بخورند." : "تا ۳ ساعت آینده رویداد پراثر آمریکایی نیست.") +
      "</div>";
    function seg(id, cur, opts) {
      return '<div class="seg" data-cal="' + id + '">' + opts.map(function (o) { return '<button data-v="' + o[0] + '" class="' + (cur === o[0] ? "on" : "") + '">' + o[1] + "</button>"; }).join("") + "</div>";
    }
    app.innerHTML = eventBanner() + '<section><h2>تقویم اقتصادی <small>این هفته و هفته‌ی بعد · ساعت تهران</small></h2>' + '<div id="calpast">' + calPast() + "</div>" + cards +
      '<div class="filters">' +
      seg("r", S.cal.r, [["today", "امروز"], ["24", "۲۴ ساعت آینده"], ["up", "پیش رو"], ["all", "همه (از ۲ روز پیش)"]]) +
      seg("c", S.cal.c, [["usd", "فقط آمریکا"], ["major", "اقتصادهای بزرگ"], ["all", "همه‌ی کشورها"]]) +
      seg("i", S.cal.i, [["h", "فقط مهم"], ["hm", "مهم و متوسط"], ["all", "همه"]]) +
      '</div><div id="calbody"></div><p class="muted">ستاره‌ها اثر معمول هر رویداد روی کریپتو است (از اهمیت، کشور و نوع آن)، نه پیش‌بینی جهت. ' +
      "روی هر ردیف بزنید تا توضیح آن باز شود. منبع: ForexFactory (faireconomy.media). عدد «واقعی» اگر منبع بدهد نشان داده می‌شود.</p></section>";
    app.querySelectorAll("[data-cal]").forEach(function (el) {
      el.onclick = function (e) {
        var b = e.target.closest("button"); if (!b) return;
        S.cal[el.dataset.cal] = b.dataset.v;
        el.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
        calBody();
        var p = document.getElementById("calpast"); if (p) { p.innerHTML = calPast(); calPastBind(); }
      };
    });
    calBody();
    calPastBind();
  }
  function calBody() {
    var el = document.getElementById("calbody"); if (!el) return;
    var list = calFilter(MK.calendar || []), now = Date.now(), day = null, rows = [];
    if (!list.length) { el.innerHTML = '<p class="muted">با این فیلترها رویدادی نیست.</p>'; return; }
    var nextTs = (list.filter(function (e) { return e.ts > now; })[0] || {}).ts;
    list.forEach(function (e, i) {
      var d = jfmt(e.ts, "ymd");
      if (d !== day) { day = d; rows.push('<tr class="day"><td colspan="8">' + esc(new Intl.DateTimeFormat("fa-IR", {weekday: "long", timeZone: "Asia/Tehran"}).format(new Date(e.ts))) + " " + esc(d) + "</td></tr>"); }
      var k = CALKIND[e.kind] || CALKIND.other, c = CCY[e.country] || ["", e.country], past = e.ts < now, key = e.ts + "|" + e.title + "|" + e.country;
      var imp = e.impact === "High" ? '<span class="badge short">مهم</span>' : e.impact === "Medium" ? '<span class="badge watch">متوسط</span>' :
        e.impact === "Holiday" ? '<span class="badge gray">تعطیل</span>' : '<span class="badge gray">کم</span>';
      rows.push('<tr class="calrow' + (past ? " thin" : "") + (e.ts === nextTs ? " nextev" : "") + '" data-k="' + esc(key) + '"><td class="num">' + esc(jfmt(e.ts, "time")) +
        (past ? "" : '<br><small data-until="' + e.ts + '">' + esc(untilTxt(e.ts)) + "</small>") + "</td><td>" + c[0] + " " + esc(c[1]) + "</td><td>" + imp + "</td><td>" + wStars(e.w || 0) +
        "</td><td><b>" + esc(e.title) + '</b><br><small class="muted">' + esc(k[0]) + "</small></td>" +
        '<td class="num">' + esc(e.forecast || "—") + '</td><td class="num">' + esc(e.previous || "—") + '</td><td class="num">' + (e.actual ? "<b>" + esc(e.actual) + "</b><br>" + surprise(e) : "—") + "</td></tr>");
      if (S.cal.open[key] && (k[1] || k[2] || past)) rows.push('<tr class="calx"><td colspan="8">' + calDesc(e) + "</td></tr>");
    });
    el.innerHTML = '<div class="scroll"><table class="caltab"><thead><tr><th>ساعت</th><th>کشور</th><th>اهمیت</th><th>اثر روی کریپتو</th><th>رویداد</th><th>پیش‌بینی</th><th>قبلی</th><th>واقعی</th></tr></thead><tbody>' +
      rows.join("") + "</tbody></table></div>";
    el.querySelectorAll("tr.calrow").forEach(function (tr) {
      tr.onclick = function () { var k = tr.dataset.k; S.cal.open[k] = !S.cal.open[k]; calBody(); };
    });
  }
  function calendarSection() {                // the market tab keeps a short list; the full one is its own tab
    var now = Date.now(), cal = (MK.calendar || []).filter(function (e) { return e.ts > now - 3600000 && e.w >= 3; }).slice(0, 6);
    if (!cal.length) return "";
    return '<section><h2>رویدادهای اقتصادی پیش رو <small><a href="#cal">تقویم کامل ←</a></small></h2><div class="scroll"><table><tbody>' +
      cal.map(function (e) {
        var c = CCY[e.country] || ["", e.country];
        return '<tr><td data-v="' + e.ts + '">' + esc(jfmt(e.ts, "dt")) + ' <small data-until="' + e.ts + '">' + esc(untilTxt(e.ts)) + "</small></td><td>" + c[0] + " " + esc(e.title) + "</td><td>" + wStars(e.w || 0) + "</td></tr>";
      }).join("") + "</tbody></table></div></section>";
  }
  setInterval(function () {                    // countdowns tick without a re-render
    document.querySelectorAll("[data-until]").forEach(function (el) {
      var t = Number(el.dataset.until);
      el.textContent = t > Date.now() ? untilTxt(t) : "منتشر شد";
    });
  }, 30000);

  // ------------------------------------------------------------------ notifications
  var NOTIF_KEY = "notify";
  function notifOn() { try { return localStorage.getItem(NOTIF_KEY) === "1"; } catch (e) { return false; } }
  var audioCtx = null;
  function beep(good) {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      var o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = good ? 880 : 440; o.type = "sine";
      g.gain.setValueAtTime(0.0001, audioCtx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.25, audioCtx.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.5);
      o.connect(g); g.connect(audioCtx.destination); o.start(); o.stop(audioCtx.currentTime + 0.55);
    } catch (e) {}
  }
  function bellUi() {
    var b = document.getElementById("bell");
    if (!b) return;
    var on = notifOn();
    b.textContent = on ? "🔔" : "🔕";
    b.title = on ? "اعلان‌ها روشن است (برای خاموش کردن بزنید)" : "روشن کردن اعلان و صدا برای سیگنال‌ها، TPها و استاپ‌ها";
  }
  var bellEl = document.getElementById("bell");
  if (bellEl) bellEl.addEventListener("click", function () {
    if (notifOn()) { try { localStorage.setItem(NOTIF_KEY, "0"); } catch (e) {} bellUi(); return; }
    function enable() { try { localStorage.setItem(NOTIF_KEY, "1"); } catch (e) {} beep(true); bellUi(); }
    if (window.Notification && Notification.permission === "default") Notification.requestPermission().then(enable, enable);
    else enable();
  });
  var EVN = {filled: "وارد شد", tp1: "TP1 خورد", tp2: "TP2 خورد", tp3: "خروج با سود", sl: "استاپ خورد", breakeven: "سر به سر بسته شد", expired: "منقضی شد", cancelled: "لغو شد"};
  function snapshot(d) { var m = {}; (d.trades || []).forEach(function (t) { m[t.id] = t.status + "|" + (t.filled_at ? 1 : 0); }); return m; }
  function notifyChanges(prev, next) {
    if (!notifOn()) return;
    var msgs = [];
    (next.trades || []).forEach(function (t) {
      var was = prev[t.id], now = t.status + "|" + (t.filled_at ? 1 : 0);
      if (!t.approved && !was) return;                  // new Watch setups are too many to ring for
      if (!was) msgs.push([t, "سیگنال تازه: " + (t.side > 0 ? "لانگ " : "شورت ") + t.base + " (" + t.system + ")", true]);
      else if (was !== now) {
        var last = (t.events || []).slice(-1)[0], k = last ? last[0] : t.status;
        msgs.push([t, t.base + ": " + (EVN[k] || t.status_fa) + (t.approved ? "" : " (Watch)"), !/sl/.test(k)]);
      }
    });
    msgs.slice(0, 5).forEach(function (m) {
      beep(m[2]);
      if (window.Notification && Notification.permission === "granted") {
        try { var n = new Notification("پنل سیگنال", {body: m[1], tag: m[0].id, lang: "fa", dir: "rtl"}); n.onclick = function () { window.focus(); openTrade(m[0].id); }; } catch (e) {}
      }
    });
  }

  // ------------------------------------------------------------------ start
  setTheme(theme());
  var h = (location.hash || "").replace(/^#/, "").split("/");
  var saved = null; try { saved = localStorage.getItem("tab3"); } catch (e) {}
  if (h[1]) S.sel = decodeURIComponent(h[1]);
  show(TABS[h[0]] || ALIAS[h[0]] ? h[0] : (saved && (TABS[saved] || ALIAS[saved]) ? saved : "sum"), false);
  window.addEventListener("hashchange", function () {   // in-page links such as #chart/BTC or #cal
    var p = (location.hash || "").replace(/^#/, "").split("/");
    if (!TABS[p[0]] && !ALIAS[p[0]]) return;
    if (p[1]) S.sel = decodeURIComponent(p[1]);
    show(p[0], false);
    if (!S.jump) window.scrollTo(0, 0);
  });
  loadMarks();
  bellUi();
  setInterval(refresh, 20000);
  setInterval(status, 30000);
  setInterval(liveTick, 5000);
  setInterval(freshTick, 60000);
  liveTick();
})();
