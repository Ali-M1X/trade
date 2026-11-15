(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("panel-data").textContent);
  var LWC = window.LightweightCharts;
  var app = document.getElementById("app");
  var S = {                                   // UI state (kept across data refreshes)
    tab: "home", sel: null, tf: null, hist: {k: "", s: "", side: "", res: "", q: "", sort: "closed", dir: "desc"},
    cand: {s: ""}, live: {}, liveOk: false, hiddenSeries: {}
  };
  var charts = [];                            // charts of the current tab (removed on re-render)
  var candleCache = {};

  // ------------------------------------------------------------------ helpers
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
  var TABS = {home: home, chart: chartTab, hist: history, cmp: compare, mkt: market, news: news, strat: strategy, how: method};
  function show(tab, push) {
    if (!TABS[tab]) tab = "home";
    S.tab = tab;
    document.querySelectorAll("#tabs button").forEach(function (b) { b.classList.toggle("on", b.dataset.t === tab); });
    try { localStorage.setItem("tab2", tab); } catch (e) {}
    if (push !== false) {
      var h = "#" + tab + (tab === "chart" && S.sel ? "/" + encodeURIComponent(S.sel) : "");
      if (location.hash !== h) history.replaceState(null, "", h);
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
    var p = document.getElementById("pos-count");
    if (p) p.textContent = fa(openTrades().length);
  }
  function openTrades() { return trades().filter(function (t) { return t.open && t.filled_at; }); }
  function pendingTrades() { return trades().filter(function (t) { return t.status === "pending"; }); }
  function closedTrades() { return trades().filter(function (t) { return !t.open; }); }
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
      var risk = key === "all" ? t.risk_usd : t.own_risk_usd, r = openR(t);
      if (risk != null && r != null) extra += risk * r;
    });
    return {equity: a.balance + extra, unreal: extra};
  }
  function accLine(key, l) {
    var a = D.accounts[key], ret = a.start ? (l.equity - a.start) / a.start * 100 : 0;
    return pct(ret, 2) + ' <small>از $' + a.start + " · موجودی بسته‌شده " + usd(a.balance, false) + " · باز " + usd(l.unreal) + "</small>";
  }
  function home() {
    var cards = ACC.map(function (x) {
      var a = D.accounts[x[0]], l = accLive(x[0]);
      return '<div class="acct" style="--c:var(' + x[3] + ')"><div class="h"><b>' + x[1] + "</b><small>" + x[2] + "</small></div>" +
        '<div class="big ltr" id="eqv-' + x[0] + '">$' + l.equity.toFixed(2) + "</div>" +
        '<div id="eqr-' + x[0] + '">' + accLine(x[0], l) + "</div>" +
        '<div class="kv"><div><span class="l">سود/زیان بسته</span>' + usd(a.pnl) + "</div>" +
        '<div><span class="l">نرخ برد</span>' + (a.closed ? fa(Math.round(a.wins / a.closed * 100)) + "٪ <small>(" + fa(a.wins) + " از " + fa(a.closed) + ")</small>" : "—") + "</div>" +
        '<div><span class="l">بیشترین افت</span><span class="ltr">' + (a.max_dd || 0).toFixed(1) + "%</span></div>" +
        '<div><span class="l">ضریب سود</span><span class="ltr">' + (a.pf == null ? "—" : a.pf.toFixed(2)) + "</span></div>" +
        '<div><span class="l">کارمزد و هزینه</span>' + usd(-(a.fees || 0)) + "</div>" +
        '<div><span class="l">باز / در انتظار</span>' + fa(a.open) + " / " + fa(a.pending) + "</div></div></div>";
    }).join("");
    var open = openTrades().sort(function (a, b) { return b.created - a.created; });
    var s1 = (D.systems || []);
    app.innerHTML =
      '<section><h2>حساب‌های شبیه‌سازی‌شده <small>شروع ' + esc(jfmt(D.start, "ymd")) + " با $" + D.balance + '</small></h2><div class="grid g3">' + cards + "</div></section>" +
      '<section><h2>منحنی موجودی <small>(با کلیک روی راهنما، هر خط را خاموش/روشن کنید)</small></h2><div class="chartbox" id="eq"></div><div class="legend" id="eq-leg"></div></section>' +
      '<div class="grid g2"><section><h2>پوزیشن‌های باز <small>' + fa(open.length) + "</small></h2><div id=\"minipos\">" + miniPositions(open) + "</div></section>" +
      "<section><h2>وضعیت بازار</h2>" + s1.map(regimeMini).join("") + "</section></div>" +
      "<section><h2>آخرین اجراها</h2>" + runs() + "</section>";
    equityChart();
  }
  function miniPositions(list) {
    if (!list.length) return '<div class="empty">الان پوزیشن بازی نیست.</div>';
    return '<div class="scroll"><table><thead><tr><th>کوین</th><th>جهت</th><th>سیستم</th><th>نوع</th><th>قیمت</th><th>R الان</th></tr></thead><tbody>' +
      list.map(function (t) {
        return '<tr class="click" data-open="' + esc(t.id) + '"><td><b>' + esc(t.base) + "</b></td><td>" + side(t.side) + "</td><td>" + esc(sysShort(t.system)) +
          "</td><td>" + kind(t) + '</td><td class="num">' + px(livePrice(t)) + "</td><td>" + R(openR(t)) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
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
  function equityChart() {
    var el = document.getElementById("eq"), leg = document.getElementById("eq-leg");
    var c = makeChart(el, {rightPriceScale: {scaleMargins: {top: 0.1, bottom: 0.1}}});
    if (!c) return;
    var any = false;
    ACC.forEach(function (x) {
      var a = D.accounts[x[0]];
      var pts = uniq((a.curve || []).map(function (p) { return {time: tsec(p[0]), value: p[1]}; }));
      var l = accLive(x[0]);
      if (pts.length) pts[pts.length - 1] = {time: pts[pts.length - 1].time, value: l.equity};
      if (pts.length > 2) any = true;
      var s = c.addLineSeries({color: cssv(x[3]), lineWidth: 2, lineType: 1, visible: !S.hiddenSeries[x[0]],
        priceFormat: {type: "custom", formatter: function (v) { return "$" + v.toFixed(2); }}, lastValueVisible: true, priceLineVisible: false});
      s.setData(pts);
      if (x[0] === "approved") s.createPriceLine({price: D.balance, color: cssv("--muted"), lineStyle: 2, lineWidth: 1, axisLabelVisible: false, title: "شروع"});
      var span = document.createElement("span");
      span.innerHTML = '<i style="background:' + cssv(x[3]) + '"></i>' + x[1] + ": $" + l.equity.toFixed(2);
      if (S.hiddenSeries[x[0]]) span.className = "off";
      span.addEventListener("click", function () {
        S.hiddenSeries[x[0]] = !S.hiddenSeries[x[0]];
        s.applyOptions({visible: !S.hiddenSeries[x[0]]}); span.classList.toggle("off");
      });
      leg.appendChild(span);
    });
    c.timeScale().fitContent();
    if (!any) leg.insertAdjacentHTML("beforeend", '<small>هنوز معامله‌ی بسته‌شده‌ای نیست.</small>');
  }

  // ------------------------------------------------------------------ chart tab
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
    return byId(id) || candidates().filter(function (c) { return c.id === id; })[0] || null;
  }
  function chartTab() {
    var open = openTrades().sort(function (a, b) { return b.created - a.created; });
    var pend = pendingTrades();
    var closed = closedTrades().filter(function (t) { return t.filled_at; }).sort(function (a, b) { return (b.closed_at || 0) - (a.closed_at || 0); });
    var cands = candidates();
    if (!findSel(S.sel)) S.sel = (open[0] || pend[0] || closed[0] || cands[0] || {}).id || null;
    function item(t, r) {
      return '<div class="titem ' + (t.id === S.sel ? "sel" : "") + '" data-open="' + esc(t.id) + '"><span class="c">' + esc(t.base) + "</span>" + side(t.side) +
        ' <small>' + esc(sysShort(t.system)) + (t.cand ? "" : " · " + (t.approved ? "تأیید" : "Watch")) + '</small><span class="r">' + r + "</span></div>";
    }
    var list = '<div class="card tlist">' +
      "<h4>پوزیشن‌های باز (" + fa(open.length) + ")</h4>" + (open.map(function (t) { return item(t, R(openR(t))); }).join("") || '<small class="muted" style="padding:6px">—</small>') +
      (pend.length ? "<h4>سفارش در انتظار (" + fa(pend.length) + ")</h4>" + pend.map(function (t) { return item(t, '<small>' + esc(t.grade) + "</small>"); }).join("") : "") +
      (cands.length ? "<h4>کاندیدهای نزدیک به سیگنال</h4>" + cands.map(function (t) { return item(t, '<small class="ltr">' + (t.score || 0).toFixed(0) + "</small>"); }).join("") : "") +
      "<h4>معاملات بسته (" + fa(closed.length) + ")</h4>" + closed.slice(0, 60).map(function (t) { return item(t, R(t.net_r)); }).join("") + "</div>";
    app.innerHTML = '<div class="trade-layout">' + list + '<div><section id="tview"></section></div></div>';
    tradeView(findSel(S.sel));
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
  function tradeView(t) {
    var box = document.getElementById("tview");
    if (!t) { box.innerHTML = '<div class="empty">معامله‌ای برای نمایش نیست.</div>'; return; }
    var p = livePrice(t), r = t.cand ? null : openR(t);
    var stat = t.cand ? '<span class="badge gray">کاندید</span>' : '<span class="badge gray">' + esc(t.status_fa) + "</span>";
    box.innerHTML =
      '<div class="chead"><span class="coin">' + esc(t.base) + '<small>/USDT</small></span>' + side(t.side) + (t.cand ? "" : kind(t)) + stat +
      ' <small>' + esc(t.system) + " · رده " + esc(t.grade || "—") + ' · امتیاز <span class="ltr">' + (t.score || 0).toFixed(1) + "</span></small>" +
      '<span class="px" id="tv-px">' + px(p) + "</span>" + (t.open ? R(r) : t.cand ? "" : R(t.net_r)) +
      '<div class="seg tfs" id="tfs"></div></div>' +
      '<div class="chartbox tall" id="tv-chart"><div class="empty">در حال بارگذاری نمودار…</div></div>' +
      '<div class="legend"><span><i style="background:' + cssv("--acc") + '"></i>ورود</span><span><i style="background:' + cssv("--neg") + '"></i>استاپ</span>' +
      '<span><i style="background:' + cssv("--pos") + '"></i>تارگت‌ها</span><small>فلش‌ها: زمان سیگنال، ورود و خروج‌ها</small></div>' +
      levels(t, p) + detail(t);
    loadCandles(t.base).then(function (data) {
      var el = document.getElementById("tv-chart");
      if (!el || S.sel !== t.id) return;
      if (!data || !Object.keys(data).length) { el.innerHTML = '<div class="empty">کندلی برای این کوین ذخیره نشده است.</div>'; return; }
      var tf = pickTf(t, data);
      document.getElementById("tfs").innerHTML = TFS.filter(function (x) { return data[x[0]]; }).map(function (x) {
        return '<button data-tf="' + x[0] + '" class="' + (x[0] === tf ? "on" : "") + '">' + x[1] + "</button>";
      }).join("");
      document.getElementById("tfs").onclick = function (e) {
        var b = e.target.closest("button"); if (!b) return;
        S.tf = b.dataset.tf; tradeView(t);
      };
      el.innerHTML = "";
      drawTrade(el, t, data[tf], tf);
    });
  }
  var current = null;                        // the candle series on screen (live updates)
  function drawTrade(el, t, rows, tf) {
    var c = makeChart(el, {rightPriceScale: {scaleMargins: {top: 0.08, bottom: 0.22}}});
    if (!c) return;
    var up = cssv("--pos"), dn = cssv("--neg");
    var cs = c.addCandlestickSeries({upColor: up, downColor: dn, borderUpColor: up, borderDownColor: dn, wickUpColor: up, wickDownColor: dn,
      priceFormat: {type: "custom", formatter: px, minMove: 1e-9}});
    var bars = rows.map(function (r) { return {time: r[0], open: r[1], high: r[2], low: r[3], close: r[4]}; });
    cs.setData(bars);
    var vol = c.addHistogramSeries({priceScaleId: "vol", priceFormat: {type: "volume"}, lastValueVisible: false, priceLineVisible: false});
    c.priceScale("vol").applyOptions({scaleMargins: {top: 0.82, bottom: 0}});
    vol.setData(rows.map(function (r) { return {time: r[0], value: r[5] || 0, color: r[4] >= r[1] ? "rgba(46,189,133,.35)" : "rgba(246,70,93,.35)"}; }));
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
    var step = TFSEC[tf] || 3600, first = bars.length ? bars[0].time : 0, last = bars.length ? bars[bars.length - 1].time : 0;
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
    // show the trade: from a little before the signal to its end (or now)
    var from = t.created ? snap(t.created) : null, to = t.closed_at ? snap(t.closed_at) : last;
    if (from != null) {
      var pad = step * 40;
      c.timeScale().setVisibleRange({from: Math.max(first, from - pad), to: Math.min(last, (to || last) + step * 25)});
    } else c.timeScale().fitContent();
    current = {series: cs, base: t.base, tf: tf, step: step, last: bars[bars.length - 1]};
  }
  function levels(t, p) {
    function lv(label, v, ref) {
      var d = (ref && v != null && p) ? (v - p) / p * 100 : null;
      return '<div class="lv"><span class="l">' + label + '</span><span class="v">' + px(v) + "</span>" +
        (d != null ? '<span class="d ' + cls(d * (t.side || 1)) + '">' + (d > 0 ? "+" : "") + d.toFixed(2) + "% از قیمت</span>" : "") + "</div>";
    }
    var risk = t.risk_usd, r = t.cand ? null : (t.open ? openR(t) : t.net_r);
    var money = "";
    if (!t.cand) {
      money = '<div class="lv"><span class="l">ریسک (حساب همه)</span><span class="v">' + usd(risk, false) + "</span></div>" +
        '<div class="lv"><span class="l">حجم پوزیشن</span><span class="v">' + usd(t.size_usd, false) + "</span></div>" +
        '<div class="lv"><span class="l">' + (t.open ? "سود/زیان باز" : "سود/زیان") + '</span><span class="v">' +
        usd(t.open ? (risk != null && r != null ? risk * r : null) : t.pnl_usd) + "</span></div>" +
        '<div class="lv"><span class="l">درصد ریسک / فاصله‌ی استاپ</span><span class="v">' + (t.risk_pct || 0).toFixed(2) + "% / " + (t.sl_pct || 0).toFixed(2) + "%</span></div>";
    }
    return '<div class="levels">' + lv("قیمت الان", p) + lv("ورود", t.entry, 1) + lv(t.open ? "استاپ الان" : "استاپ", t.open ? t.sl_now : t.sl, 1) +
      lv("TP1", t.tp1, 1) + lv("TP2", t.tp2, 1) + money + "</div>";
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
  function history() {
    var f = S.hist;
    var names = uniqList(trades().map(function (t) { return t.system; }));
    function seg(g, opts) {
      return '<div class="seg" data-g="' + g + '">' + opts.map(function (o) { return '<button data-v="' + esc(o[0]) + '" class="' + (f[g] === o[0] ? "on" : "") + '">' + esc(o[1]) + "</button>"; }).join("") + "</div>";
    }
    app.innerHTML = '<section><h2>تاریخچه‌ی معاملات</h2><div class="filters">' +
      seg("k", [["", "همه"], ["ok", "تأییدشده"], ["w", "Watch"]]) +
      seg("s", [["", "همه‌ی سیستم‌ها"]].concat(names.map(function (n) { return [n, n]; }))) +
      seg("side", [["", "هر دو جهت"], ["1", "لانگ"], ["-1", "شورت"]]) +
      seg("res", [["", "همه‌ی نتایج"], ["win", "سود"], ["loss", "ضرر"], ["none", "بدون ورود"]]) +
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
  var HCOLS = [["created", "سیگنال"], ["closed", "بسته شدن"], ["kind", "نوع"], ["system", "سیستم"], ["base", "کوین"], ["side", "جهت"],
    ["score", "رده / امتیاز"], ["status", "نتیجه"], ["hold", "مدت"], ["gross", "R خام"], ["net", "R خالص"], ["pnl", "سود/زیان (همه)"],
    ["own", "در حساب خودش"], ["bal", "موجودی بعد"]];
  function hval(t, k) {
    return {created: t.created, closed: t.closed_at || t.created, kind: t.approved ? 1 : 0, system: t.system, base: t.base, side: t.side,
      score: t.score, status: t.status, hold: t.closed_at && t.filled_at ? t.closed_at - t.filled_at : -1,
      gross: t.filled_at ? t.gross_r : -99, net: t.filled_at ? t.net_r : -99, pnl: t.pnl_usd == null ? -1e9 : t.pnl_usd,
      own: t.own_pnl_usd == null ? -1e9 : t.own_pnl_usd, bal: t.own_balance_after || 0}[k];
  }
  function histTable() {
    var f = S.hist;
    var rows = closedTrades().filter(function (t) {
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
    var sumP = rows.reduce(function (s, t) { return s + (t.pnl_usd || 0); }, 0);
    document.getElementById("hsum").innerHTML = plural(rows.length, "معامله") + " · برد " + (filled.length ? fa(Math.round(wins / filled.length * 100)) + "٪" : "—") +
      " · مجموع " + R(sumR) + " · " + usd(sumP) + ' <small>(روی هر ردیف کلیک کنید تا روی نمودار ببینید)</small>';
    if (!rows.length) { document.getElementById("htab").innerHTML = '<div class="empty">هنوز معامله‌ی بسته‌شده‌ای نیست.</div>'; return; }
    var head = "<tr>" + HCOLS.map(function (c) {
      return '<th class="sort" data-k="' + c[0] + '"' + (f.sort === c[0] ? ' data-dir="' + f.dir + '"' : "") + ">" + c[1] + "</th>";
    }).join("") + "</tr>";
    var body = rows.map(function (t) {
      var hold = t.closed_at && t.filled_at ? dur((t.closed_at - t.filled_at) / 3600000) : "—";
      return '<tr class="click" data-open="' + esc(t.id) + '"><td>' + esc(jfmt(t.created)) + "</td><td>" + esc(jfmt(t.closed_at)) + "</td><td>" + kind(t) +
        "</td><td>" + esc(sysShort(t.system)) + "</td><td><b>" + esc(t.base) + "</b></td><td>" + side(t.side) + "</td><td>" + esc(t.grade) +
        ' <small class="ltr">' + (t.score || 0).toFixed(0) + "</small></td><td>" + esc(t.status_fa) + "</td><td>" + hold + "</td><td>" +
        R(t.filled_at ? t.gross_r : null) + "</td><td>" + R(t.filled_at ? t.net_r : null) + "</td><td>" + usd(t.pnl_usd) + "</td><td>" +
        usd(t.own_pnl_usd) + "</td><td>" + usd(t.own_balance_after, false) + "</td></tr>";
    }).join("");
    var el = document.getElementById("htab");
    el.innerHTML = "<table><thead>" + head + "</thead><tbody>" + body + "</tbody></table>";
    el.querySelectorAll("th.sort").forEach(function (th) {
      th.addEventListener("click", function () {
        var k = th.dataset.k;
        if (f.sort === k) f.dir = f.dir === "asc" ? "desc" : "asc"; else { f.sort = k; f.dir = "desc"; }
        histTable();
      });
    });
  }

  // ------------------------------------------------------------------ compare
  var CMP = [["signals", "تعداد سیگنال", 0, function (v) { return fa(v); }],
    ["closed", "معامله‌ی بسته", 0, function (v) { return fa(v); }],
    ["win_rate", "نرخ برد", 1, function (v) { return v == null ? "—" : fa(Math.round(v)) + "٪"; }],
    ["net_r", "مجموع R خالص", 1, function (v) { return R(v); }],
    ["avg_r", "میانگین R هر معامله", 1, function (v) { return R(v); }],
    ["pf", "ضریب سود", 1, function (v) { return v == null ? "—" : '<span class="ltr">' + v.toFixed(2) + "</span>"; }],
    ["max_dd_r", "بیشترین افت (R)", -1, function (v) { return '<span class="ltr">' + (v || 0).toFixed(2) + "R</span>"; }],
    ["best", "بهترین معامله", 1, function (v) { return R(v); }],
    ["worst", "بدترین معامله", 1, function (v) { return R(v); }],
    ["avg_hold_h", "میانگین مدت معامله", 0, function (v) { return dur(v); }],
    ["open", "پوزیشن باز", 0, function (v) { return fa(v); }],
    ["open_r", "R باز", 1, function (v) { return R(v); }],
    ["longs", "لانگ / شورت", 0, null],
    ["unfilled", "بدون ورود", 0, function (v) { return fa(v); }]];
  function compare() {
    var C = D.compare || {}, names = Object.keys(C).sort();
    if (!names.length) { app.innerHTML = '<section><div class="empty">داده‌ای برای مقایسه نیست.</div></section>'; return; }
    var cols = [];
    names.forEach(function (n) { [["approved", "تأییدشده"], ["watch", "Watch"]].forEach(function (k) { cols.push({n: n, k: k[0], label: n + " · " + k[1], s: C[n][k[0]]}); }); });
    var head = "<tr><th></th>" + cols.map(function (c) { return "<th>" + esc(c.label) + "</th>"; }).join("") + "</tr>";
    var body = CMP.map(function (m) {
      var vals = cols.map(function (c) { return c.s[m[0]]; });
      var nums = vals.filter(function (v) { return typeof v === "number"; });
      var best = m[2] && nums.length > 1 ? (m[2] > 0 ? Math.max.apply(null, nums) : Math.min.apply(null, nums)) : null;
      var worst = m[2] && nums.length > 1 ? (m[2] > 0 ? Math.min.apply(null, nums) : Math.max.apply(null, nums)) : null;
      return "<tr><td class=\"muted\">" + m[1] + "</td>" + cols.map(function (c, i) {
        var v = vals[i], txt = m[3] ? m[3](v) : fa(c.s.longs) + " / " + fa(c.s.shorts);
        var cl = best != null && best !== worst && v === best ? "best" : worst != null && best !== worst && v === worst ? "worst" : "";
        return '<td class="' + cl + '">' + txt + "</td>";
      }).join("") + "</tr>";
    }).join("");
    var duel = "";
    if (names.length >= 2) {
      var a = C[names[0]].all, b = C[names[1]].all;
      function vs(label, x, y, f, higher) {
        var wa = higher ? (x || 0) > (y || 0) : (x || 0) < (y || 0), wb = higher ? (y || 0) > (x || 0) : (y || 0) < (x || 0);
        return '<div class="vs"><div class="side ' + (wa ? "win" : "") + '"><div class="v">' + f(x) + '</div></div><div class="mid">' + label + '</div><div class="side ' + (wb ? "win" : "") + '"><div class="v">' + f(y) + "</div></div></div>";
      }
      duel = '<section><h2>' + esc(names[0]) + ' در برابر ' + esc(names[1]) + ' <small>(همه‌ی معاملات، تأییدشده و Watch)</small></h2><div class="grid" style="gap:8px">' +
        '<div class="vs head"><div class="side">' + esc(names[0]) + "<small>" + fa(a.closed || 0) + " معامله‌ی بسته · " + fa(a.signals || 0) + ' سیگنال</small></div><div class="mid"></div><div class="side">' +
        esc(names[1]) + "<small>" + fa(b.closed || 0) + " معامله‌ی بسته · " + fa(b.signals || 0) + " سیگنال</small></div></div>" +
        vs("مجموع R خالص", a.net_r, b.net_r, function (v) { return R(v); }, true) +
        vs("نرخ برد", a.win_rate, b.win_rate, function (v) { return v == null ? "—" : Math.round(v) + "%"; }, true) +
        vs("ضریب سود", a.pf, b.pf, function (v) { return v == null ? "—" : v.toFixed(2); }, true) +
        vs("بیشترین افت (R)", a.max_dd_r, b.max_dd_r, function (v) { return (v || 0).toFixed(2) + "R"; }, false) +
        vs("میانگین R", a.avg_r, b.avg_r, function (v) { return R(v); }, true) + "</div></section>";
    }
    app.innerHTML = duel +
      '<section><h2>منحنی R تجمعی <small>(بعد از هزینه‌ها، به ترتیب زمان بسته شدن)</small></h2><div class="chartbox" id="cmp-chart"></div><div class="legend" id="cmp-leg"></div></section>' +
      '<section><h2>جدول مقایسه <small>سبز = بهتر، قرمز = بدتر</small></h2><div class="scroll"><table class="cmp"><thead>' + head + "</thead><tbody>" + body + "</tbody></table></div></section>";
    var c = makeChart(document.getElementById("cmp-chart"));
    if (!c) return;
    var colors = [cssv("--acc"), cssv("--warn"), cssv("--acc2"), cssv("--pos")];
    var leg = document.getElementById("cmp-leg");
    cols.forEach(function (col, i) {
      var pts = uniq([{time: tsec(D.start), value: 0}].concat((col.s.curve || []).map(function (p) { return {time: tsec(p[0]), value: p[1]}; })));
      var s = c.addLineSeries({color: colors[i % 4], lineWidth: 2, lineType: 1, lineStyle: col.k === "watch" ? 2 : 0,
        priceFormat: {type: "custom", formatter: function (v) { return v.toFixed(2) + "R"; }}, priceLineVisible: false});
      s.setData(pts);
      if (i === 0) s.createPriceLine({price: 0, color: cssv("--muted"), lineStyle: 2, lineWidth: 1, axisLabelVisible: false});
      var span = document.createElement("span");
      span.innerHTML = '<i style="background:' + colors[i % 4] + '"></i>' + esc(col.label) + ": " + (col.s.net_r || 0).toFixed(2) + "R";
      span.addEventListener("click", function () { var on = span.classList.toggle("off"); s.applyOptions({visible: !on}); });
      leg.appendChild(span);
    });
    c.timeScale().fitContent();
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
  function market() {
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
        (m.btc_weak ? '<p class="warn">BTC ضعیف است؛ لانگ آلت‌ها امتیاز بالاتر لازم دارد.</p>' : "") +
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
        '</td><td><span class="ltr">' + (c.score || 0).toFixed(0) + "</span> " + scoreBar(c.score) + "</td><td>" + esc(c.grade || "—") + '</td><td class="num">' + px(c.price) +
        '</td><td class="num">' + px(p.entry) + '</td><td class="num">' + px(p.sl) + '</td><td class="num">' + px(p.tp1) + "</td><td>" +
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
    app.innerHTML = '<div class="grid g2">' + cards + "</div>" + domHtml +
      '<section><h2>نزدیک‌ترین کاندیدها به سیگنال <small>کلیک = دیدن پلن روی نمودار</small></h2><div class="filters"><div class="seg" id="cf">' +
      [["", "همه"]].concat(names.map(function (n) { return [n, n]; })).map(function (o) { return '<button data-v="' + esc(o[0]) + '" class="' + (cf === o[0] ? "on" : "") + '">' + esc(o[1]) + "</button>"; }).join("") +
      '</div><small>سیگنال = رده‌ی A یا B بدون مانع؛ Watch = ستاپ نزدیک که فقط دنبال می‌شود</small></div>' +
      (rows ? '<div class="scroll"><table><thead><tr><th>سیستم</th><th>کوین</th><th>جهت</th><th>امتیاز</th><th>رده</th><th>قیمت</th><th>ورود</th><th>استاپ</th><th>TP1</th><th>چرا سیگنال نشد</th></tr></thead><tbody>' + rows + "</tbody></table></div>" : '<div class="empty">در آخرین بررسی کاندیدی نبود.</div>') + "</section>" +
      '<section><h2>شورت‌لیست <small>' + fa((s0.shortlist || []).length) + " کوین · قیف " + esc(ago(s0.funnel_ts ? Number(s0.funnel_ts) : null)) + '</small></h2><div class="grid g4">' + (sl || '<div class="muted">—</div>') + "</div></section>" +
      '<section><h2>واچ‌لیست <small>' + fa(Object.keys(s0.watchlist || {}).length) + "</small></h2>" + (wl || "—") +
      (bw ? '<h3>منتظر شکست</h3>' + bw : "") + ((s0.hot_categories || []).length ? '<h3>دسته‌های داغ</h3>' + s0.hot_categories.map(function (h) { return '<span class="chip">' + esc(h) + "</span>"; }).join("") : "") + "</section>";
    var cfEl = document.getElementById("cf");
    if (cfEl) cfEl.addEventListener("click", function (e) { var b = e.target.closest("button"); if (b) { S.cand.s = b.dataset.v; market(); } });
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
  }
  function valueAt(rows, ms, i) {
    var v = null;
    for (var k = 0; k < rows.length; k++) { if (rows[k][0] <= ms) v = rows[k][i]; else break; }
    return v;
  }

  // ------------------------------------------------------------------ news / strategy / method
  function news() {
    var n = D.news || [];
    app.innerHTML = "<section><h2>هشدارهای خبری <small>۱۴ روز اخیر</small></h2>" + (n.length ? '<div class="scroll"><table><thead><tr><th>زمان</th><th>کوین</th><th>اهمیت</th><th>خبر</th><th>منبع</th></tr></thead><tbody>' +
      n.map(function (x) {
        var title = x.url ? '<a href="' + esc(x.url) + '" target="_blank" rel="noopener noreferrer">' + esc(x.title) + "</a>" : esc(x.title);
        return "<tr><td>" + esc(jfmt(x.ts)) + "</td><td>" + esc((x.coins || []).join(", ")) + "</td><td>" + "⭐".repeat(x.level || 0) + '</td><td style="white-space:normal">' + title + "</td><td>" + esc(x.source) + "</td></tr>";
      }).join("") + "</tbody></table></div>" : '<div class="empty">در ۱۴ روز اخیر هشدار خبری نبود.</div>') + "</section>";
  }
  function strategy() { app.innerHTML = D.strategy_html || ""; }
  function method() { app.innerHTML = "<section><h2>روش محاسبه</h2>" + (D.method_html || "") + "</section>"; }

  // ------------------------------------------------------------------ live data
  function refresh() {
    fetch("data.json?t=" + Date.now(), {cache: "no-store"}).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
      if (!j || j.now === D.now) { status(); return; }
      D = j; candleCache = {};
      if (S.tab !== "chart") render(); else { status(); }
    }).catch(function () { status(); });
  }
  function liveBases() {
    var set = {};
    openTrades().forEach(function (t) { set[t.base] = 1; });
    var sel = findSel(S.sel); if (sel && S.tab === "chart") set[sel.base] = 1;
    return Object.keys(set);
  }
  function fetchTicker(base) {
    var gate = "https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=" + encodeURIComponent(base) + "_USDT";
    return fetch(gate).then(function (r) { if (!r.ok) throw 0; return r.json(); }).then(function (j) {
      var x = j && j[0]; if (!x) throw 0; return parseFloat(x.last);
    }).catch(function () {
      return fetch("https://www.okx.com/api/v5/market/ticker?instId=" + encodeURIComponent(base) + "-USDT-SWAP").then(function (r) { return r.json(); })
        .then(function (j) { var x = j && j.data && j.data[0]; return x ? parseFloat(x.last) : null; });
    });
  }
  var liveFails = 0, liveSkip = 0;
  function liveTick() {
    if (document.hidden) return;
    if (liveSkip > 0) { liveSkip--; return; }     // the exchanges were unreachable: try again less often
    var bases = liveBases();
    if (!bases.length) return;
    Promise.all(bases.map(function (b) {
      return fetchTicker(b).then(function (p) { if (p && isFinite(p)) S.live[b] = {price: p, ts: Date.now()}; return p; }).catch(function () { return null; });
    })).then(function (res) {
      var ok = res.some(function (p) { return p != null; });
      S.liveOk = ok;
      if (ok) liveFails = 0; else { liveFails++; liveSkip = Math.min(8, liveFails * 2); }
      status();
      updateLive();
    });
  }
  function updateLive() {
    if (S.tab === "chart") {
      var t = findSel(S.sel);
      if (t && current && current.base === t.base && S.live[t.base]) {
        var p = S.live[t.base].price, b = current.last, now = tsec(Date.now());
        var start = Math.floor(now / current.step) * current.step;
        if (b) {
          var bar = start > b.time ? {time: start, open: b.close, high: Math.max(b.close, p), low: Math.min(b.close, p), close: p}
            : {time: b.time, open: b.open, high: Math.max(b.high, p), low: Math.min(b.low, p), close: p};
          try { current.series.update(bar); current.last = bar; } catch (e) {}
        }
        var el = document.getElementById("tv-px"); if (el) el.textContent = px(p);
      }
      document.querySelectorAll(".titem").forEach(function (it) {
        var tr = byId(it.getAttribute("data-open"));
        if (tr && tr.open) { var r = it.querySelector(".r"); if (r) r.innerHTML = R(openR(tr)); }
      });
    } else if (S.tab === "home") {          // only the live numbers: the charts and toggles stay as they are
      ACC.forEach(function (x) {
        var l = accLive(x[0]), v = document.getElementById("eqv-" + x[0]), r = document.getElementById("eqr-" + x[0]);
        if (v) v.textContent = "$" + l.equity.toFixed(2);
        if (r) r.innerHTML = accLine(x[0], l);
      });
      var mp = document.getElementById("minipos");
      if (mp) mp.innerHTML = miniPositions(openTrades().sort(function (a, b) { return b.created - a.created; }));
    }
  }

  // ------------------------------------------------------------------ start
  setTheme(theme());
  var h = (location.hash || "").replace(/^#/, "").split("/");
  var saved = null; try { saved = localStorage.getItem("tab2"); } catch (e) {}
  if (h[1]) S.sel = decodeURIComponent(h[1]);
  show(TABS[h[0]] ? h[0] : (saved && TABS[saved] ? saved : "home"), false);
  setInterval(refresh, 60000);
  setInterval(status, 30000);
  setInterval(liveTick, 15000);
  liveTick();
})();
