# هشدار خبر (آزمایشی)

از ۲۰۲۶-۱۰-۰۷. کنار سیستم سیگنال کار می‌کند و هیچ چیزی از منطق سیگنال را عوض نمی‌کند. با `news.enabled: false` در `config.yaml` خاموش می‌شود.

## دو مسیر
| مسیر | کجا | هر چند وقت | چه می‌خواند | چه می‌کند |
|---|---|---|---|---|
| سریع | Cloudflare Workers (`cloudflare/news-worker`) | هر ۱ دقیقه | اعلان‌های Binance و OKX (لیستینگ، دلیست) | پیام تلگرام |
| کند | GitHub Actions (همراه `run-scheduled`) | هر اجرا (~۱۵ دقیقه) | همان اعلان‌ها + RSS سایت‌های CoinDesk، Cointelegraph، The Block، Decrypt، Blockworks، Bitcoin Magazine، The Defiant | پیام تلگرام برای خبر سایت‌ها، ذخیره‌ی همه‌ی خبرها و قیمت کوین در لحظه‌ی خبر و ۱، ۴ و ۲۴ ساعت بعد |

اعلان‌های صرافی را فقط مسیر سریع خبر می‌دهد (`news.fast_path: cloudflare`). اگر worker نصب نشده باشد، `fast_path: none` باعث می‌شود مسیر کند آن‌ها را هم بفرستد.

## امتیاز خبر
با قانون، بدون هوش مصنوعی و بدون هزینه. قانون‌ها در `src/agent/news/rules.json` هستند و هر دو مسیر از همین فایل می‌خوانند.
- **۲ (خیلی مثبت):** لیستینگ اسپات بایننس، HODLer Airdrops و Launchpool بایننس، لیستینگ در Coinbase/Upbit/Robinhood/Binance در خبرها، تأیید ETF، همکاری با شرکت بزرگ.
- **۱ (مثبت):** فیوچرز جدید بایننس/OKX، لیستینگ OKX، Binance Alpha، بای‌بک، مین‌نت/آپگرید، همکاری، جذب سرمایه.
- **منفی:** دلیست، هک، مشکل حقوقی، آنلاک توکن. فقط وقتی فرستاده می‌شود که آن کوین سیگنال باز داشته باشد.
- سهام توکنی (bStocks، X-Perp، TradFi، Pre-IPO) نادیده گرفته می‌شوند.

پیام فقط وقتی فرستاده می‌شود که کوین مشخص باشد، خبر از ۳ ساعت قدیمی‌تر نباشد و امتیاز ≥ `news.min_alert_level` باشد. اولین اجرای هر مسیر فقط خبرهای موجود را ثبت می‌کند و پیامی نمی‌فرستد.

## پیام
```
🚀 خبر خیلی مثبت | HYPE
لیستینگ اسپات بایننس
«Binance Will List Hyperliquid (HYPE) with Seed Tag Applied»
منبع: Binance · 2 دقیقه پیش
وضعیت تکنیکال:
• HYPE: ستاپ تکنیکال ندارد
https://www.binance.com/en/support/announcement/detail/...
ℹ️ فقط اطلاع‌رسانی است و سیگنال معامله نیست.
```
«وضعیت تکنیکال» از فایل `news_context.json` روی شاخه‌ی `data` می‌آید که هر اجرای GitHub به‌روز می‌کند: سیگنال باز، آخرین رده‌ی L6 (A/B/Watch) یا اینکه کوین در OKX فیوچرز نیست.

## نصب مسیر سریع
1. حساب رایگان Cloudflare، و یک API Token با الگوی «Edit Cloudflare Workers».
2. در GitHub دو secret بسازید: `CLOUDFLARE_API_TOKEN` و `CLOUDFLARE_ACCOUNT_ID`.
3. workflow «news-worker» (با هر تغییر در `cloudflare/news-worker` روی main یا دستی) تست‌ها را اجرا می‌کند، فضای KV را یک بار می‌سازد، worker را نصب می‌کند و توکن تلگرام را از secretهای GitHub به آن می‌دهد.

محدودیت‌های پلن رایگان Cloudflare: ۱۰ میلی‌ثانیه CPU در هر اجرا (این worker فقط JSON کوچک می‌خواند)، ۱۰۰ هزار درخواست در روز، ۱۰۰۰ نوشتن KV در روز (worker فقط وقتی خبر جدید باشد می‌نویسد).

## دستورها
- `trade-agent run-news`: یک بار مسیر کند.
- `trade-agent run-news --preview`: برای تست؛ خبرهای همین حالا را هم (اعلان صرافی‌ها را هم) پیام می‌کند.
- `npm test --prefix cloudflare/news-worker`: تست worker با همان نمونه‌های `tests/fixtures/news_titles.json`.

## قدم بعد
بعد از ۲ تا ۴ هفته، جدول `news` (قیمت لحظه‌ی خبر و ۱/۴/۲۴ ساعت بعد) نشان می‌دهد کدام نوع خبر واقعاً قیمت را بالا برده است. بعد از آن خبر با سیگنال ترکیب می‌شود.
