"""The panel's strategy page (Ali, 2026-11-15): both live systems explained in Persian.

The numbers come from the configs the live runs use (config.live_config and
system2.system2_config), so the page follows any change to config.yaml.
"""
from __future__ import annotations

import html

SECTION_FA = {"phase": "فاز بازار (وایکاف و استیج)", "dow": "ساختار داو (سقف و کف‌ها)",
              "levels": "سطوح حمایت و مقاومت", "volume": "حجم", "candles": "کندل ورود",
              "cycles": "سیکل‌ها (W/D/4H)", "patterns": "الگوها و سلامت روند",
              "confirmation": "تأیید ورود در ۱ ساعته"}
REGIME_FA = {"alt_season": "آلت‌سیزن", "btc_led": "رهبری بیت‌کوین", "risk_off": "ریسک‌گریزی",
             "capitulation": "تسلیم (ریزش شدید)", "neutral": "خنثی", "bull": "صعودی", "range": "رنج",
             "bear": "نزولی"}
BIAS_FA = {"long": "فقط لانگ", "short": "فقط شورت", "both": "هر دو طرف"}
LEVEL_FA = {"cluster 1d": "خوشه‌ی سطوح روزانه", "cluster 1w": "خوشه‌ی سطوح هفتگی",
            "prev_high 1w": "سقف هفته‌ی قبل", "prev_low 1w": "کف هفته‌ی قبل", "round round": "اعداد رُند"}
CYCLE_FA = {"late": "انتهای سیکل", "fresh_turn": "تازه برگشته"}


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _weights(cfg: dict) -> str:
    from .layers.technical import section_max
    w = cfg["technical"].get("section_weights") or section_max(cfg)
    rows = "".join(
        f'<div class="wrow"><span>{_e(SECTION_FA.get(k, k))}</span>'
        f'<span class="wbar"><i style="width:{float(v) * 4:.0f}%"></i></span><b>{float(v):.0f}</b></div>'
        for k, v in sorted(w.items(), key=lambda kv: -float(kv[1])))
    return f'<div class="weights">{rows}</div>'


def _avoid(cfg: dict) -> str:
    a = cfg["technical"].get("avoid") or {}
    out = []
    if a.get("max_touches"):
        out.append(f"سطحی که بیش از {a['max_touches']} بار لمس شده (فرسوده) رد می‌شود.")
    if a.get("max_score"):
        out.append(f"امتیاز بالای {a['max_score']} رد می‌شود؛ در بررسی ضررها، امتیازهای خیلی بالا اغلب ورود دیرهنگام بودند.")
    if a.get("btc_extended"):
        out.append(f"وقتی BTC بیش از {a['btc_extended']}٪ از میانگینش کشیده شده، لانگ آلت گرفته نمی‌شود.")
    if a.get("cycles"):
        out.append("سیکل‌های " + "، ".join(CYCLE_FA.get(c, c) for c in a["cycles"]) + " کنار گذاشته می‌شوند.")
    if a.get("levels"):
        out.append("ورود روی این سطوح ممنوع است: " + "، ".join(LEVEL_FA.get(x, x) for x in a["levels"]) + ".")
    return "".join(f"<li>{_e(x)}</li>" for x in out) or "<li>فیلتر اضافه‌ای ندارد.</li>"


def _trade(cfg: dict) -> str:
    t, g = cfg["trade"], cfg["grades"]
    share = g["risk_share"]
    stop = (f"{t['atr_stop_mult']} برابر ATR تایم ۴ ساعته از نقطه‌ی ورود" if t["stop_mode"] == "atr"
            else "پشت سطح حمایت/مقاومت با فاصله‌ی ATR")
    tp1 = f"{t['tp1_fixed_r']}R ثابت" if t["tp1_mode"] == "fixed_r" else "اولین سطح مقابل (حداقل 2R)"
    return f"""<ul>
<li><b>ریسک پایه:</b> {t['base_risk_pct']}٪ موجودی در هر معامله. رده‌ی A {share['A'] * 100:.0f}٪ و رده‌ی B {share['B'] * 100:.0f}٪ این ریسک را می‌گیرد.</li>
<li><b>استاپ:</b> {_e(stop)}.</li>
<li><b>TP1:</b> {_e(tp1)}؛ {t['tp1_close_pct']}٪ حجم بسته و استاپ به نقطه‌ی ورود (سر به سر) می‌رود.</li>
<li><b>TP2:</b> {t['tp2_r']}R؛ {t['tp2_close_pct']}٪ دیگر بسته می‌شود.</li>
<li><b>TP3:</b> باقی حجم با میانگین ۲۵ (MA25) دنبال می‌شود تا قیمت آن را بشکند یا ساختار برگردد.</li>
<li><b>رده‌ها:</b> A از امتیاز {g['A']}، B از {g['B']}، Watch از {g['watch']} (Watch ارسال نمی‌شود؛ در پنل دنبال می‌شود).</li>
<li><b>حداقل نسبت سود به ضرر:</b> {cfg['technical']['levels']['min_rr']}.</li>
</ul>"""


def _regimes(cfg: dict, price: bool) -> str:
    if price:
        p = cfg["regime"]["price"]
        rows = "".join(f"<li><b>{_e(REGIME_FA.get(k, k))}:</b> {_e(BIAS_FA.get(v.get('bias'), v.get('bias')))}</li>"
                       for k, v in (p.get("regimes") or {}).items())
        sysm = p.get("systems") or {}
        extra = ("<li>در رژیم نزولی، امتیازدهی با وزن‌های سیستم ۲ انجام می‌شود (روندمحورتر).</li>"
                 if sysm.get("bear") else "")
        return (f"<p>رژیم از روی <b>قیمت</b> تعیین می‌شود: روند BTC و سبد {len(p.get('basket') or [])} آلت بزرگ "
                f"نسبت به میانگین {p.get('ma')} روزه و قدرت روند (ADX بالای {p.get('adx_min')}). "
                f"هر رژیم باید حداقل {p.get('hold_days')} روز بماند تا عوض شود.</p><ul>{rows}{extra}</ul>")
    regs = cfg["regime"]["regimes"]
    rows = []
    for k, v in regs.items():
        bits = [BIAS_FA.get(v.get("bias"), v.get("bias") or "")]
        if v.get("trend"):
            bits.append(f"جهت اصلی {'لانگ' if v['trend'] == 'long' else 'شورت'}")
        if v.get("counter_risk"):
            bits.append(f"خلاف جهت اصلی با {float(v['counter_risk']) * 100:.0f}٪ ریسک")
        if v.get("min_grade"):
            bits.append(f"فقط رده‌ی {v['min_grade']}")
        rows.append(f"<li><b>{_e(REGIME_FA.get(k, k))}:</b> {_e('، '.join(b for b in bits if b))}</li>")
    return ("<p>رژیم از روی <b>دامیننس</b> تعیین می‌شود: جهت USDT.D، BTC.D و TOTAL2 در تایم روزانه و ۴ ساعته.</p>"
            f"<ul>{''.join(rows)}</ul>")


def _news(cfg: dict) -> str:
    n = (cfg.get("technical") or {}).get("news") or {}
    if not n.get("enabled"):
        return ""
    return (f"<li><b>اخبار:</b> خبرهای دسته‌بندی‌شده‌ی {n.get('window_h', 72)} ساعت اخیر هر کوین جمع می‌شوند "
            f"(اطلاعیه‌ی صرافی سنگین‌تر، خبر قدیمی‌تر سبک‌تر). خبر هم‌جهت تا {n.get('max_points', 10)} امتیاز اضافه می‌کند و "
            "خبر خلاف جهت کم می‌کند؛ خبر خیلی قوی خلاف جهت ستاپ را Watch نگه می‌دارد.</li>")


def strategy_html(cfg: dict) -> str:
    from .config import live_config
    c1 = live_config(cfg)
    systems = [("سیستم ۱", (cfg.get("live") or {}).get("variant") or "base", c1, True,
                "سیستم اصلی. با رژیم قیمتی کار می‌کند و در هر رژیم فقط یک طرف بازار را معامله می‌کند.")]
    if (cfg.get("system2") or {}).get("enabled"):
        from .system2 import system2_config
        c2 = system2_config(cfg)
        systems.append(("سیستم ۲ (High Risk)", cfg["system2"]["variant"], c2, False,
                        "سیستم پرریسک‌تر. با رژیم دامیننس کار می‌کند، در هر فاز هر دو طرف را می‌گیرد و ریسک پایه‌اش دو برابر است."))
    u = cfg["universe"]
    rt = cfg.get("realtime") or {}
    funnel = f"""<section class="strat"><h2>مسیر مشترک هر دو سیستم (قیف)</h2><ol class="funnel">
<li><b>دامیننس:</b> USDT.D، BTC.D و TOTAL2 جهت کلی پول را نشان می‌دهند.</li>
<li><b>بازارهای اصلی:</b> BTC، ETH و ETH/BTC در تایم‌های هفتگی، روزانه و ۴ ساعته امتیاز می‌گیرند.</li>
<li><b>اسکن:</b> {u.get('coins_top_n')} کوین برتر فیوچرز با حجم روزانه‌ی حداقل ${(u.get('min_volume_24h_usd') or 0) / 1e6:.0f}M.
برچسب‌هایی مثل فشردگی (SQUEEZE)، افزایش اوپن‌اینترست، حجم غیرعادی و قدرت نسبی پیدا می‌شوند.</li>
<li><b>جفت‌ها و همبستگی:</b> جهت هر کوین در برابر USDT، BTC و ETH، و همبستگی و بتای آن با بازار.</li>
<li><b>شورت‌لیست:</b> بهترین‌ها برای بررسی دقیق (هر {rt.get('periods_min', {}).get('run-4h', 240)} دقیقه به‌روز می‌شود).</li>
<li><b>تحلیل تکنیکال (۱۰۰ امتیاز):</b> هر {rt.get('periods_min', {}).get('run-1h', 60)} دقیقه، روی کندل‌های در حال شکل‌گیری ۴ ساعته و ۱ ساعته.</li>
<li><b>دروازه‌ها:</b> رژیم، فاز، نسبت سود به ضرر و تأیید ورود باید همه سبز باشند.</li>{_news(cfg)}
<li><b>سیگنال و مدیریت:</b> رده‌ی A یا B به تلگرام می‌رود؛ پوزیشن روی کندل‌های ۵ دقیقه‌ای مدیریت می‌شود.</li>
</ol></section>"""
    cards = []
    for name, variant, c, price, intro in systems:
        cards.append(f"""<section class="strat"><h2>{_e(name)} <small>({_e(variant)})</small></h2>
<p>{_e(intro)}</p>
<h3>رژیم بازار</h3>{_regimes(c, price)}
<h3>وزن بخش‌های امتیاز</h3>{_weights(c)}
<h3>مدیریت معامله</h3>{_trade(c)}
<h3>فیلترهای حذف (از بررسی ضررها)</h3><ul>{_avoid(c)}</ul></section>""")
    return funnel + '<div class="strat-grid">' + "".join(cards) + "</div>"
