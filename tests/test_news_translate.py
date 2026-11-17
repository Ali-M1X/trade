import json

from agent.news import translate as T


class R:
    def __init__(self, data, code=200):
        self.data, self.status_code = data, code

    def json(self):
        return self.data


def google_like(fa_of):
    calls = []

    def get(url, params=None, timeout=None, headers=None):
        calls.append((url, params))
        if url == T.GTX:
            lines = params["q"].split("\n")
            out = "\n".join(fa_of(x) for x in lines)
            # Google splits by sentence; the new lines ride inside the segments
            segs = [[p + ("\n" if i < len(out.split("\n")) - 1 else ""), "src"] for i, p in enumerate(out.split("\n"))]
            return R([segs, None, "en"])
        raise OSError("blocked")
    return get, calls


def test_translates_new_titles_once_and_keeps_them(tmp_path):
    path = tmp_path / "news_fa.json"
    get, calls = google_like(lambda x: "ترجمه " + x)
    titles = ["Binance Will List FOO", "SEC approves ETF", "Binance Will List FOO", "خبر فارسی", "123"]
    got = T.translate_titles(path, titles, tr=T.Translator(get=get))
    assert got == {"Binance Will List FOO": "ترجمه Binance Will List FOO", "SEC approves ETF": "ترجمه SEC approves ETF"}
    assert len(calls) == 1                                   # one request for the batch
    assert json.loads(path.read_text(encoding="utf-8")) == got
    get2, calls2 = google_like(lambda x: "x")
    assert T.translate_titles(path, titles, tr=T.Translator(get=get2)) == got
    assert calls2 == []                                      # cached: nothing asked
    assert T.translate_titles(path, titles + ["New one"], max_new=0, tr=T.Translator(get=get2)) == got


def test_falls_back_to_mymemory_and_survives_a_dead_network(tmp_path):
    path = tmp_path / "news_fa.json"

    def get(url, params=None, timeout=None, headers=None):
        if url == T.GTX:
            return R({}, 403)
        return R({"responseStatus": 200, "responseData": {"translatedText": "بیت‌کوین بالا رفت"}})
    assert T.translate_titles(path, ["Bitcoin rises", "ETH falls"], tr=T.Translator(get=get)) == \
        {"Bitcoin rises": "بیت‌کوین بالا رفت", "ETH falls": "بیت‌کوین بالا رفت"}

    def dead(url, params=None, timeout=None, headers=None):
        raise OSError("offline")
    n = []

    def counting(*a, **k):
        n.append(1)
        return dead(*a, **k)
    assert T.translate_titles(path, ["Bitcoin rises", "A", "B", "C"], tr=T.Translator(get=counting)) == \
        {"Bitcoin rises": "بیت‌کوین بالا رفت"}
    assert len(n) <= 3                                       # each provider asked once, then left alone


def test_line_mismatch_goes_one_by_one(tmp_path):
    def get(url, params=None, timeout=None, headers=None):
        q = params["q"]
        if "\n" in q:
            return R([[["همه با هم", q]], None, "en"])       # lines merged: unusable
        return R([[["فا " + q, q]], None, "en"])
    got = T.translate_titles(tmp_path / "f.json", ["One", "Two"], tr=T.Translator(get=get))
    assert got == {"One": "فا One", "Two": "فا Two"}


def test_titles_in_payload_newest_first():
    pl = {"news": [{"title": "alert"}], "news_view": {"items": [{"title": "page"}]},
          "systems": [{"candidates": [{"eval": {"news": {"points": 2, "top": [["cand news", 2]]}}}]}],
          "trades": [{"detail": {"eval": {"news": {"top": [["trade news", -1]]}}}}]}
    assert T.titles_in(pl) == ["alert", "page", "cand news", "trade news"]


def test_panel_build_shows_cached_titles_without_network(cfg, tmp_path):
    from agent.panel import news_fa
    c = dict(cfg, storage=dict(cfg["storage"], db_path=str(tmp_path / "state.db")))
    T.save(T.cache_path(c), {"Old title": "عنوان قدیمی"})
    pl = {"news": [{"title": "Old title"}, {"title": "Unknown"}]}
    assert news_fa(c, pl) == {"Old title": "عنوان قدیمی"}    # translate_news off in tests: no network
