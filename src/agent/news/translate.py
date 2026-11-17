"""Persian news titles for the panel (2026-11-17, Ali: «عناوین خبرها فارسی باشند تا سریع‌تر پیش بریم»).

Display only: the news rules, scores and Telegram alerts still read the original English title.

Every title is translated once and kept in data/news_fa.json ({english: persian}). Two writers fill it:
- the panel build on the server, a few dozen new titles per build (free Google Translate endpoint,
  MyMemory as the fallback);
- GitHub Actions (ai-review.yml, `trade-agent translate-news`), in case these hosts refuse the
  server: it writes the same file to the `ai` branch and the server merges it in (pull_remote).
A title no one could translate yet is shown in English and tried again in the next build.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

GTX = "https://translate.googleapis.com/translate_a/single"
MYMEMORY = "https://api.mymemory.translated.net/get"
FILE = "news_fa.json"
KEEP = 5000                     # newest titles kept in the file
BATCH_CHARS = 1500              # English characters per Google request (titles joined by new lines)
PERSIAN = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")


def cache_path(cfg: dict) -> Path:
    return Path(cfg["storage"]["db_path"]).parent / FILE


def load(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items() if v} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path: Path, data: dict[str, str]) -> None:
    if len(data) > KEEP:
        data = dict(list(data.items())[-KEEP:])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def needs(title: str) -> bool:
    """Only titles with English words and no Persian/Arabic letters (some sources are Persian already)."""
    return bool(title) and bool(LATIN.search(title)) and not PERSIAN.search(title)


def _clean(fa: str) -> str:
    return re.sub(r"\s+", " ", fa or "").strip()


class Translator:
    """en -> fa through free endpoints. After a provider fails it is not asked again in this run."""

    def __init__(self, get=None, timeout: float = 6.0):
        if get is None:
            import requests
            get = requests.get
        self.get = get
        self.timeout = timeout
        self.down: set[str] = set()

    def _json(self, name: str, url: str, params: dict):
        if name in self.down:
            return None
        try:
            r = self.get(url, params=params, timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0 trade-panel"})
            if r.status_code != 200:
                raise OSError(f"HTTP {r.status_code}")
            return r.json()
        except Exception as e:                    # noqa: BLE001  (network, JSON, a blocked host)
            log.info("translate %s failed: %s", name, e)
            self.down.add(name)
            return None

    def google(self, texts: list[str]) -> list[str] | None:
        """Several titles in one request, one per line; None when the lines don't come back one for one."""
        data = self._json("google", GTX, {"client": "gtx", "sl": "en", "tl": "fa", "dt": "t", "q": "\n".join(texts)})
        try:
            out = "".join(seg[0] for seg in data[0] if seg and isinstance(seg[0], str))
        except (TypeError, IndexError, KeyError):
            return None
        lines = [_clean(x) for x in out.split("\n")]
        return lines if len(lines) == len(texts) and all(lines) else None

    def mymemory(self, text: str) -> str | None:
        data = self._json("mymemory", MYMEMORY, {"q": text[:480], "langpair": "en|fa"})
        try:
            fa = _clean((data.get("responseData") or {}).get("translatedText"))
            if int(data.get("responseStatus") or 0) != 200:
                return None
        except (AttributeError, TypeError, ValueError):
            return None
        # MyMemory answers in English when it has nothing (or its quota is used up)
        return fa if fa and PERSIAN.search(fa) and "MYMEMORY WARNING" not in fa else None

    def one(self, text: str) -> str | None:
        got = self.google([text])
        return got[0] if got else self.mymemory(text)

    def many(self, texts: list[str], deadline: float | None = None) -> dict[str, str]:
        out: dict[str, str] = {}
        batch: list[str] = []

        def flush():
            if not batch:
                return
            got = self.google(batch) if len(batch) > 1 else None
            if got:
                out.update({t: fa for t, fa in zip(batch, got) if PERSIAN.search(fa)})
            else:                                  # line count mismatch or Google down: one by one
                for t in batch:
                    if deadline is not None and time.monotonic() > deadline:
                        break
                    fa = self.one(t)
                    if fa and PERSIAN.search(fa):
                        out[t] = fa
                    if {"google", "mymemory"} <= self.down:
                        break
            batch.clear()

        size = 0
        for t in texts:
            if deadline is not None and time.monotonic() > deadline:
                break
            if {"google", "mymemory"} <= self.down:
                break
            if batch and size + len(t) > BATCH_CHARS:
                flush()
                size = 0
            batch.append(t.replace("\n", " "))
            size += len(t) + 1
        if not (deadline is not None and time.monotonic() > deadline):
            flush()
        return out


def translate_titles(path: Path, titles: list[str], max_new: int = 40, budget_s: float | None = 12.0,
                     tr: Translator | None = None) -> dict[str, str]:
    """{title: persian} for the titles already known, after translating up to max_new new ones
    (newest first: the caller passes them in the order they matter)."""
    cache = load(path)
    seen, todo = set(), []
    for t in titles:
        if t in seen or not needs(t):
            continue
        seen.add(t)
        if t not in cache and len(todo) < max_new:
            todo.append(t)
    if todo:
        tr = tr or Translator()
        deadline = time.monotonic() + budget_s if budget_s else None
        got = tr.many(todo, deadline)
        if got:
            cache.update(got)
            try:
                save(path, cache)
            except OSError as e:
                log.info("news_fa not saved: %s", e)
    return {t: cache[t] for t in seen if t in cache}


def pull_remote(cfg: dict, every_s: float = 300) -> None:
    """Merge the titles GitHub Actions translated (branch `ai`, news_fa.json) into the local file."""
    branch = (cfg.get("ai") or {}).get("llm_branch")
    if not branch:
        return
    path = cache_path(cfg)
    mark = path.with_name(".news_fa_pulled")
    try:
        if time.time() - mark.stat().st_mtime < every_s:
            return
    except OSError:
        pass
    try:
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.touch()
        remote = cfg["storage"].get("git_remote", "origin")
        subprocess.run(["git", "fetch", "--depth=1", remote, branch], check=True, capture_output=True, timeout=30)
        data = subprocess.run(["git", "show", f"FETCH_HEAD:{FILE}"], check=True, capture_output=True, timeout=10).stdout
        theirs = json.loads(data)
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        log.info("news_fa from the ai branch not read: %s", e)
        return
    if not isinstance(theirs, dict):
        return
    mine = load(path)
    new = {str(k): str(v) for k, v in theirs.items() if v and k not in mine}
    if new:
        mine.update(new)
        save(path, mine)


def titles_in(pl: dict) -> list[str]:
    """Every news title the panel shows, newest first: the alerts, the news page, and the news
    behind candidates' and trades' scores."""
    out: list[str] = [n.get("title") or "" for n in pl.get("news") or []]
    out += [n.get("title") or "" for n in (pl.get("news_view") or {}).get("items") or []]

    def walk(x, depth=0):
        if depth > 8:
            return
        if isinstance(x, dict):
            nw = x.get("news")
            if isinstance(nw, dict):
                for row in nw.get("top") or []:
                    if isinstance(row, (list, tuple)) and row and isinstance(row[0], str):
                        out.append(row[0])
            for k, v in x.items():
                if k not in ("news", "news_view", "candles", "market", "strategy_html", "method_html"):
                    walk(v, depth + 1)
        elif isinstance(x, list):
            for v in x:
                walk(v, depth + 1)
    walk({"systems": pl.get("systems"), "trades": pl.get("trades")})
    return [t for t in out if t]
