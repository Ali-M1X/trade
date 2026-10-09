import io
import json
import subprocess
from pathlib import Path

import pytest

from agent.config import load_secrets
from agent.news.classify import classify, exchange_coins, load_rules, media_coins
from agent.news.format import coin_status, news_message
from agent.news.run import fill_prices, run_news
from agent.news.sources import parse_feed
from agent.notify.telegram import Notifier
from agent.store.repository import Repository
from agent.store.storage import GitBranchStorage

HOUR = 3_600_000
NOW = 1_791_400_000_000


# ------------------------------------------------------------------ rules (real titles, 2026-10-07)
VECTORS = json.loads((Path(__file__).parent / "fixtures" / "news_titles.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("v", VECTORS, ids=[v["title"][:40] for v in VECTORS])
def test_classify_real_titles(v):
    """The same vectors are checked against the Cloudflare worker (cloudflare/news-worker/test)."""
    s = classify(v["title"], v["source"])
    assert (s.level, s.rule) == (v["level"], v["rule"])
    if v["coins"] is not None:
        assert exchange_coins(v["title"]) == v["coins"]


def test_every_rule_compiles_and_has_persian_text():
    rules = load_rules()["rules"]
    assert len({r["id"] for r in rules}) == len(rules)
    assert all(r["fa"] and r["level"] in (-2, -1, 1, 2) for r in rules)


def test_exchange_coins():
    assert exchange_coins("Binance Will List Hyperliquid (HYPE) with Seed Tag Applied") == ["HYPE"]
    assert exchange_coins("Binance Futures Will Launch USDⓈ-Margined CTUSDT Perpetual Contract") == ["CT"]
    assert exchange_coins("OKX will launch AEON/USD for spot trading") == ["AEON"]
    assert exchange_coins("OKX to list perpetual futures for QNT crypto") == ["QNT"]


def test_media_coins_matches_names_and_tickers_not_common_words():
    coins = [("SOL", "Solana"), ("HYPE", "Hyperliquid"), ("ONE", "Harmony"), ("NEAR", "NEAR Protocol"),
             ("ETH", "Ethereum")]
    assert media_coins("Visa partners with Solana for stablecoin settlement", coins) == ["SOL"]
    assert media_coins("Coinbase to list Hyperliquid's HYPE token", coins) == ["HYPE"]
    assert media_coins("ONE more thing: $ETH rallies", coins) == ["ETH"]
    assert media_coins("The ETF market is near a turning point", coins) == []


# ------------------------------------------------------------------ feeds
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>X</title>
<item><title><![CDATA[Coinbase to list Hyperliquid's HYPE token]]></title>
<link><![CDATA[https://x.test/a?utm_source=rss]]></link><guid>https://x.test/a</guid>
<pubDate>Wed, 07 Oct 2026 18:55:36 +0000</pubDate></item>
<item><title>Visa partners with Solana</title><link>https://x.test/b</link></item>
</channel></rss>"""


def test_parse_feed():
    items = parse_feed(RSS, "ct")
    assert [i["title"] for i in items] == ["Coinbase to list Hyperliquid's HYPE token", "Visa partners with Solana"]
    assert items[0]["url"] == "https://x.test/a" and items[0]["id"] == "ct:https://x.test/a"
    assert items[0]["ts"] == 1791399336000 and items[1]["ts"] is None


# ------------------------------------------------------------------ the run
class Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return json.loads(self.body)

    @property
    def content(self):
        return self.body.encode()


class Session:
    def __init__(self, ts):
        self.ts = ts

    def get(self, url, headers=None, timeout=None):
        if "binance" in url:
            return Resp(json.dumps({"data": {"catalogs": [{"catalogName": "New Cryptocurrency Listing", "articles": [
                {"id": 1, "code": "c1", "title": "Binance Will List Hyperliquid (HYPE) with Seed Tag Applied",
                 "releaseDate": self.ts}]}]}}))
        if "okx" in url:
            return Resp(json.dumps({"data": [{"details": []}]}))
        if "cointelegraph" in url:
            return Resp(RSS.replace("Wed, 07 Oct 2026 18:55:36 +0000", "Wed, 07 Oct 2026 19:40:00 +0000"))
        raise ConnectionError("down")


class Client:
    def __init__(self):
        self.calls = []

    def fetch_ohlcv(self, symbol, tf, since, limit):
        self.calls.append((symbol, since))
        return "okx", [[since, 1, 1, 1, 10.0], [since + 300_000, 1, 1, 1, 11.0]]


class Market:
    def __init__(self):
        self.client = Client()

    def perp_bases_and_spreads(self):
        return {"HYPE", "SOL", "BTC"}, {}


class CG:
    def markets(self):
        return [{"symbol": "hype", "name": "Hyperliquid"}, {"symbol": "sol", "name": "Solana"}]


def run(cfg, repo, now, session, **over):
    cfg = {**cfg, "news": {**cfg["news"], **over}}
    out = io.StringIO()
    n = Notifier(cfg, load_secrets({}), out=out)
    return run_news(cfg, repo, Market(), CG(), n, now, session=session), n


def test_first_run_stores_without_alerting_then_alerts_new_media(cfg):
    repo = Repository()
    t = NOW - 10 * 60_000
    res, n = run(cfg, repo, NOW, Session(t))
    assert res["new"] == 3 and res["alerts"] == 0 and not n.sent      # seeding run
    assert len(res["failed"]) == len(cfg["news"]["feeds"]) - 1          # only cointelegraph answers
    # a new headline later is alerted; the exchange one is left to the worker
    repo2 = Repository()
    repo2.set_state("news_seeded", 1)
    res, n = run(cfg, repo2, 1791399600000 + 20 * 60_000, Session(t))
    assert res["alerts"] == 2
    assert all("Binance" not in m.split("\n")[2] for m in n.sent)
    hype = [m for m in n.sent if "HYPE" in m.split("\n")[0]]
    assert hype[0].startswith("🚀 خبر خیلی مثبت | HYPE\nلیستینگ در صرافی بزرگ")
    assert "منبع: Cointelegraph" in hype[0]
    stored = {r["id"]: r for r in repo2.get_news()}
    assert stored["binance:1"]["level"] == 2 and stored["binance:1"]["alerted"] is False


def test_exchange_news_alerted_here_when_no_worker(cfg):
    repo = Repository()
    repo.set_state("news_seeded", 1)
    res, n = run(cfg, repo, NOW, Session(NOW - 60_000), fast_path="none")
    assert any(m.startswith("🚀 خبر خیلی مثبت | HYPE\nلیستینگ اسپات بایننس") for m in n.sent)


def test_old_headlines_are_not_alerted(cfg):
    repo = Repository()
    repo.set_state("news_seeded", 1)
    res, n = run(cfg, repo, NOW + 30 * 24 * HOUR, Session(NOW), fast_path="none")
    # only the headline without a date (first seen now) is alerted
    assert res["new"] == 3 and res["alerts"] == 1 and "Visa partners" in n.sent[0]


def test_prices_filled_as_time_passes(cfg):
    repo = Repository()
    t0 = NOW
    repo.add_news({"id": "x", "source": "binance", "kind": "exchange", "title": "t", "ts": t0,
                   "seen_at": t0, "level": 2, "coins": ["HYPE", "NEWCOIN"]})
    m = Market()
    fill_prices(repo, m.client, "USDT", {"HYPE"}, t0 + 2 * HOUR)
    p = repo.get_news()[0]["prices"]
    assert set(p) == {"HYPE"} and set(p["HYPE"]) == {"p0", "1h"}
    fill_prices(repo, m.client, "USDT", {"HYPE"}, t0 + 25 * HOUR)
    assert set(repo.get_news()[0]["prices"]["HYPE"]) == {"p0", "1h", "4h", "24h"}


def test_coin_status_lines():
    ctx = {"perp_bases": ["SOL", "ADA", "XRP"], "coins": {
        "SOL": {"signal": {"side": "long", "grade": "B", "status": "active"}},
        "ADA": {"eval": {"grade": "Watch", "score": 62.4, "side": 1}},
        "XRP": {"watch": True}}}
    assert coin_status("SOL", ctx) == "SOL: سیگنال باز LONG رده B"
    assert coin_status("ADA", ctx) == "ADA: Watch LONG امتیاز 62"
    assert coin_status("XRP", ctx) == "XRP: در لیست بررسی، هنوز ستاپ ندارد"
    assert coin_status("NEW", ctx) == "NEW: در OKX فیوچرز نیست"


def test_message_layout():
    s = classify("Binance Will List Hyperliquid (HYPE) with Seed Tag Applied", "binance")
    item = {"source": "binance", "title": "Binance Will List Hyperliquid (HYPE) with Seed Tag Applied",
            "url": "https://www.binance.com/en/support/announcement/detail/c1", "ts": NOW - 2 * 60_000}
    msg = news_message(item, s, ["HYPE"], {"perp_bases": ["HYPE"], "coins": {}}, NOW)
    assert msg.split("\n") == [
        "🚀 خبر خیلی مثبت | HYPE", "لیستینگ اسپات بایننس",
        "«Binance Will List Hyperliquid (HYPE) with Seed Tag Applied»",
        "منبع: Binance · 2 دقیقه پیش", "وضعیت تکنیکال:", "• HYPE: ستاپ تکنیکال ندارد",
        "https://www.binance.com/en/support/announcement/detail/c1",
        "ℹ️ فقط اطلاع‌رسانی است و سیگنال معامله نیست."]


# ------------------------------------------------------------------ storage: context file on the data branch
def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_git_branch_keeps_extra_files(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", str(tmp_path / "a"))
    git(tmp_path / "a", "remote", "add", "origin", str(remote))
    s = GitBranchStorage(tmp_path / "a" / "data" / "state.db", repo_dir=tmp_path / "a")
    s.db_path.parent.mkdir(parents=True)
    s.db_path.write_bytes(b"db")
    s.push("run", extra={"news_context.json": b'{"ts": 1}'})
    assert git(remote, "ls-tree", "--name-only", "data").split() == ["news_context.json", "state.db.gz"]
    assert git(remote, "show", "data:news_context.json") == '{"ts": 1}'
    s.push("run 2")                                  # without extra: only the database
    assert git(remote, "ls-tree", "--name-only", "data").split() == ["state.db.gz"]


def test_fresh_headline_without_coins_is_stored_not_alerted(cfg):
    # a coin-less fresh headline used to store `alerted` as [] and crash the news pass
    class NoCoins(CG):
        def markets(self):
            return []
    repo = Repository()
    repo.set_state("news_seeded", 1)
    n = Notifier(cfg, load_secrets({}), out=io.StringIO())
    market = Market()
    market.perp_bases_and_spreads = lambda: (set(), {})
    res = run_news(cfg, repo, market, NoCoins(), n, NOW, session=Session(NOW - 60_000))
    assert res["new"] == 3 and res["alerts"] == 0
    assert all(r["alerted"] is False for r in repo.get_news())
