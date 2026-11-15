"""News sources: exchange announcement APIs and RSS feeds. Every fetcher returns a list of
items {id, source, kind, title, url, ts} and raises on failure; the caller skips a failing
source. All of these answer from GitHub Actions without a key (tested 2026-10-07)."""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime

import requests

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) trade-signal-agent"}
BINANCE_LIST = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20"
BINANCE_ARTICLE = "https://www.binance.com/en/support/announcement/detail/{code}"
OKX_ANN = "https://www.okx.com/api/v5/support/announcements?annType={t}"
OKX_TYPES = ("announcements-new-listings", "announcements-delistings")


def _get(session, url: str, timeout: float):
    r = (session or requests).get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


# More exchanges (2026-11-15, Ali: "aggregators and more sources"): announcement APIs that answer
# from Ali's server without a key. Their JSON differs, so the list is found by its fields.
EXCHANGES = {
    "upbit": ("https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=20&category=trade",
              "https://upbit.com/service_center/notice?id={id}"),
    "kucoin": ("https://api.kucoin.com/api/v3/announcements?annType=new-listings&pageSize=20", ""),
    "bybit": ("https://api.bybit.com/v5/announcements/index?locale=en-US&type=new_crypto&limit=20", ""),
    "bitget": ("https://api.bitget.com/api/v2/public/annoucements?language=en_US&annType=coin_listings", ""),
}
_TITLE = ("title", "annTitle")
_URL = ("url", "annUrl", "link")
_TIME = ("publishTime", "dateTimestamp", "cTime", "first_listed_at", "listed_at", "created_at", "pTime")


def _rows(data):
    """The first list of dicts with a title anywhere in the JSON."""
    if isinstance(data, list) and data and isinstance(data[0], dict) and any(k in data[0] for k in _TITLE):
        return data
    vals = data.values() if isinstance(data, dict) else data if isinstance(data, list) else []
    for v in vals:
        if isinstance(v, (dict, list)):
            found = _rows(v)
            if found:
                return found
    return []


def _when(v) -> int | None:
    if v in (None, ""):
        return None
    try:
        x = float(v)
        return int(x * 1000) if x < 1e11 else int(x)
    except (TypeError, ValueError):
        return _ts(str(v))


def parse_exchange(data, source: str, link: str = "") -> list[dict]:
    out = []
    for a in _rows(data):
        title = next((str(a[k]).strip() for k in _TITLE if a.get(k)), "")
        if not title:
            continue
        url = next((str(a[k]) for k in _URL if a.get(k)), "") or (link.format(id=a.get("id")) if link and a.get("id") else "")
        ts = next((_when(a[k]) for k in _TIME if a.get(k) not in (None, "")), None)
        key = a.get("id") or a.get("annId") or url or title
        out.append({"id": f"{source}:{key}", "source": source, "kind": "exchange", "title": " ".join(title.split()),
                    "url": url, "ts": ts, "section": ""})
    return out


def exchange(source: str, session=None, timeout: float = 15) -> list[dict]:
    url, link = EXCHANGES[source]
    return parse_exchange(_get(session, url, timeout).json(), source, link)


def binance(session=None, timeout: float = 15) -> list[dict]:
    data = _get(session, BINANCE_LIST, timeout).json()["data"]
    out = []
    for cat in data.get("catalogs") or []:
        for a in cat.get("articles") or []:
            out.append({"id": f"binance:{a['id']}", "source": "binance", "kind": "exchange",
                        "title": a["title"].strip(), "url": BINANCE_ARTICLE.format(code=a["code"]),
                        "ts": int(a["releaseDate"]), "section": cat.get("catalogName") or ""})
    return out


def okx(session=None, timeout: float = 15) -> list[dict]:
    out = []
    for t in OKX_TYPES:
        for page in _get(session, OKX_ANN.format(t=t), timeout).json().get("data") or []:
            for a in page.get("details") or []:
                out.append({"id": f"okx:{a['url']}", "source": "okx", "kind": "exchange",
                            "title": a["title"].strip(), "url": a["url"], "ts": int(a["pTime"]),
                            "section": t})
    return out


def _ts(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(parsedate_to_datetime(text.strip()).timestamp() * 1000)
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.fromisoformat(text.strip().replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def parse_feed(xml: bytes | str, source: str) -> list[dict]:
    """RSS 2.0 <item> or Atom <entry>."""
    root = ET.fromstring(xml)
    out = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        f = {c.tag.rsplit("}", 1)[-1]: c for c in el}
        title = (f["title"].text or "").strip() if "title" in f else ""
        link = ""
        if "link" in f:
            link = (f["link"].text or f["link"].get("href") or "").strip()
        guid = (f["guid"].text or "").strip() if "guid" in f and f["guid"].text else link
        ts = _ts(f["pubDate"].text if "pubDate" in f else
                 f["published"].text if "published" in f else
                 f["updated"].text if "updated" in f else None)
        if title and (guid or link):
            out.append({"id": f"{source}:{guid or link}", "source": source, "kind": "media",
                        "title": " ".join(title.split()), "url": link.split("?utm_")[0], "ts": ts,
                        "section": ""})
    return out


def rss(name: str, url: str, session=None, timeout: float = 15) -> list[dict]:
    return parse_feed(_get(session, url, timeout).content, name)


def fetch_all(cfg: dict, session=None) -> tuple[list[dict], list[str]]:
    """All configured sources. Returns (items, names of the sources that failed)."""
    n = cfg["news"]
    items, failed = [], []
    jobs = [("binance", lambda: binance(session, n["timeout_s"])),
            ("okx", lambda: okx(session, n["timeout_s"]))]
    jobs += [(name, (lambda name=name: exchange(name, session, n["timeout_s"])))
             for name in n.get("exchanges") or [] if name in EXCHANGES]
    jobs += [(name, (lambda name=name, url=url: rss(name, url, session, n["timeout_s"])))
             for name, url in n["feeds"].items()]
    for name, job in jobs:
        try:
            items += job()
        except Exception as e:                     # one broken source must not stop the rest
            log.warning("news source %s failed: %s", name, e)
            failed.append(name)
    return items, failed
