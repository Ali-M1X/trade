import copy
import json

import pytest

from agent import ai_review as ai

HOUR = 3_600_000
NOW = 1_794_700_800_000


def _case(i, win, created, closed=None, score=60, side=1, btc=0):
    x = {"score": score, "grade": 2, "btc": side * btc, "trend": side}
    done = closed is not None
    return {"id": f"c{i}", "created": created, "filled_at": created, "closed_at": closed, "done": done, "open": not done,
            "r": (1.0 if win else -1.0) if done else None, "win": bool(done and win), "x": x,
            "info": {"base": f"C{i}", "side": side, "system": "سیستم ۱", "approved": True, "grade": "B",
                     "score": score, "status": "sl", "regime": "bear", "flags": [], "sections": {}}}


def test_features_are_signed_by_the_trade_side():
    t = {"side": -1, "score": 70, "grade": "A", "approved": True, "system": "سیستم ۲",
         "detail": {"bias": "short", "majors": {"btc": -4, "eth": -6},
                    "eval": {"phase_d": "TREND_DOWN", "gates": {"confirmation": False}, "flags": ["x", "y"],
                             "funding_pct": 0.02, "sections": {"phase": 20}},
                    "plan": {"sl_pct": 3.0, "leverage": 5, "tp1_r": 2.0}}}
    x = ai.features(t)
    assert x["trend"] == 1 and x["btc"] == 4 and x["eth"] == 6 and x["phase_d"] == 1   # all favour the short
    assert x["sys2"] == 1 and x["grade"] == 3 and x["confirmed"] == 0 and x["flags"] == 2
    assert x["funding"] == pytest.approx(-0.02) and x["phase"] == 20


def test_models_learn_and_stay_near_the_base_rate_with_little_data():
    train = [_case(i, win=i % 2 == 0, created=i * HOUR, closed=i * HOUR + 1, btc=5 if i % 2 == 0 else -5) for i in range(30)]
    m = ai.Model(train)
    assert m.base == pytest.approx(16 / 32)
    good, bad = {"score": 60, "grade": 2, "btc": 5, "trend": 1}, {"score": 60, "grade": 2, "btc": -5, "trend": 1}
    assert m.knn(good) > 0.6 > 0.4 > m.knn(bad)
    assert m.logit(good) > 0.6 and m.logit(bad) < 0.4
    assert ai.Model(train[:2]).knn(good) == pytest.approx(ai.Model(train[:2]).base)   # too few: base rate


def test_local_scores_are_frozen_and_retro_ones_walk_forward(cfg, tmp_path):
    c = copy.deepcopy(cfg)
    c["storage"]["db_path"] = str(tmp_path / "state.db")
    cases = [_case(0, True, NOW - 50 * HOUR, NOW - 40 * HOUR), _case(1, False, NOW - 30 * HOUR, NOW - 20 * HOUR),
             _case(2, True, NOW - 5 * 60_000)]
    st = ai.score_local(c, cases, NOW)
    p = st["preds"]
    assert p["c0"]["base"] == [NOW - 50 * HOUR, 50.0, 1]          # nothing closed before it opened
    assert p["c1"]["base"][1] == pytest.approx(2 / 3 * 100, abs=0.1) and p["c1"]["base"][2] == 1
    assert p["c2"]["knn"][0] == NOW and p["c2"]["knn"][2] == 0     # live
    cases[2].update(done=True, closed_at=NOW + HOUR, win=False, r=-1.0)
    again = ai.score_local(c, cases, NOW + 2 * HOUR)
    assert again["preds"]["c2"] == p["c2"]                          # never rescored after the result


def test_grade_compares_with_the_base_rate():
    rows = [(0.8, True, 2.0)] * 15 + [(0.2, False, -1.0)] * 15
    base = [(0.5, w, r) for _, w, r in rows]
    g = ai.grade(rows, base)
    assert g["n"] == 30 and g["hit"] == 100 and g["brier"] == pytest.approx(0.04) and g["bss"] > 0.8
    assert g["auc"] == 1.0 and g["hi_r"] == 2.0 and g["lo_r"] == -1.0 and g["verdict"] == "good" and g["trust"] > 40
    assert ai.grade(rows[:10], base[:10])["verdict"] == "few"
    assert ai.grade([], [])["trust"] == 0


class _Resp:
    def __init__(self, text, code=200):
        self.status_code, self.text = code, text

    def json(self):
        return {"choices": [{"message": {"content": self.text}}]}


def test_llm_reviewer_scores_writes_lessons_and_survives_failures(cfg, tmp_path, monkeypatch):
    c = copy.deepcopy(cfg)
    c["storage"]["db_path"] = str(tmp_path / "state.db")
    c["ai"] = {"llm": [{"key": "g", "name": "G", "url": "https://x.test/v1", "model": "m", "key_env": "G_KEY", "budget": 5}]}
    monkeypatch.setenv("G_KEY", "secret")
    asked = []

    def post(url, json=None, timeout=None, headers=None):
        asked.append(json["messages"][0]["content"][:20])
        if "finished trade" in json["messages"][0]["content"]:
            return _Resp('{"lesson": "درس"}')
        return _Resp('ok: {"p_win": 72, "reason": "روند"}')

    cases = [_case(0, True, NOW - 50 * HOUR, NOW - 40 * HOUR), _case(1, False, NOW - HOUR)]
    st = ai.run_llm(c, cases, NOW, post)
    assert st["preds"]["c1"]["g"] == [NOW, 72.0, 0, "روند"] and st["preds"]["c0"]["g"][2] == 1
    assert st["errors"]["g"] == "" and st["ran"]["g"] == NOW
    st = ai.run_llm(c, cases, NOW + HOUR, post)                      # c0 closed with a score: one lesson
    assert [x[1:] for x in st["lessons"]["g"]] == [["c0", "درس"]] and st["post"]["g"] == ["c0"]
    v = ai.view(c, cases, NOW)
    assert v["open"][0]["scores"]["g"] == 72.0 and v["lessons"]["g"][0][2] == "درس"
    monkeypatch.delenv("G_KEY")
    assert ai.run_llm(c, cases, NOW, post)["errors"]["g"] == "no key"
    assert json.loads((tmp_path / "ai_llm.json").read_text())["preds"]["c1"]["g"][1] == 72.0


def test_panel_build_carries_the_reviewers(cfg, tmp_path):
    from agent.panel import build
    from agent.store.repository import Repository
    c = copy.deepcopy(cfg)
    c["storage"]["db_path"] = str(tmp_path / "state.db")
    c["system2"] = {"enabled": False}
    c["shadow"] = {"enabled": False}
    c["panel"] = {"start": "2026-11-06", "balance_usd": 100, "backfill": str(tmp_path / "none.json")}
    c["ai"] = {"enabled": True}
    Repository(c["storage"]["db_path"]).close()
    build(c, tmp_path / "out", NOW)
    d = json.loads((tmp_path / "out" / "data.json").read_text())
    assert [r["key"] for r in d["ai"]["reviewers"]] == ["base", "knn", "logit"] and d["ai"]["n_cases"] == 0


def test_llm_daily_cap(cfg, tmp_path, monkeypatch):
    c = copy.deepcopy(cfg)
    c["storage"]["db_path"] = str(tmp_path / "state.db")
    c["ai"] = {"llm": [{"key": "o", "url": "https://x.test", "model": "m", "key_env": "O_KEY", "budget": 5, "daily": 3}]}
    monkeypatch.setenv("O_KEY", "k")
    asked = []

    def post(url, json=None, timeout=None, headers=None):
        asked.append(1)
        return _Resp('{"p_win": 40, "reason": ""}')

    cases = [_case(i, False, NOW - i * HOUR) for i in range(6)]
    ai.run_llm(c, cases, NOW, post)
    st = ai.run_llm(c, cases, NOW + 60_000, post)
    assert len(asked) == 3 and st["calls"]["o"]["n"] == 3 and st["errors"]["o"] == "daily limit"
    ai.run_llm(c, cases, NOW + 86_400_000, post)                    # next day: allowed again
    assert len(asked) == 6


def test_openrouter_picks_a_free_model_and_moves_on_when_one_is_gone(cfg, tmp_path, monkeypatch):
    c = copy.deepcopy(cfg)
    c["storage"]["db_path"] = str(tmp_path / "state.db")
    c["ai"] = {"llm": [{"key": "o", "url": "https://or.test/api/v1", "model": "auto-free", "prefer": ["deepseek", "qwen"],
                        "key_env": "O_KEY", "budget": 4}]}
    monkeypatch.setenv("O_KEY", "k")

    class _List:
        def json(self):
            return {"data": [{"id": "meta/llama:free"}, {"id": "paid/model"}, {"id": "qwen/qwen3:free"},
                             {"id": "deepseek/v3:free"}, {"id": "img/x:free", "architecture": {"output_modalities": ["image"]}}]}

    assert ai.free_models(c["ai"]["llm"][0], lambda url, timeout=None: _List()) == ["deepseek/v3:free", "qwen/qwen3:free", "meta/llama:free"]
    used = []

    def post(url, json=None, timeout=None, headers=None):
        used.append(json["model"])
        if json["model"] == "deepseek/v3:free":
            return _Resp('{"error": "This model is unavailable for free"}', 404)
        return _Resp('{"p_win": 55, "reason": ""}')

    cases = [_case(i, False, NOW - i * HOUR) for i in range(3)]
    st = ai.run_llm(c, cases, NOW, post, lambda url, timeout=None: _List())
    assert used[:2] == ["deepseek/v3:free"] * 2 and set(used[2:]) == {"qwen/qwen3:free"}
    assert st["model"]["o"] == "qwen/qwen3:free" and st["errors"]["o"] == "" and st["preds"]["c2"]["o"][1] == 55.0
    assert ai.run_llm(c, cases, NOW + 60_000, post, lambda url, timeout=None: _List())["preds"]["c0"]["o"][1] == 55.0   # retried next run
    assert list(st["dead"]["o"]) == ["deepseek/v3:free"]

    def busy(url, json=None, timeout=None, headers=None):
        return _Resp('{"error": "rate-limited upstream"}', 429)
    c["storage"]["db_path"] = str(tmp_path / "other" / "state.db")
    (tmp_path / "other").mkdir()
    st = ai.run_llm(c, cases, NOW, busy, lambda url, timeout=None: _List())
    assert st["dead"]["o"] == {} and st["why"]["o"].startswith("HTTP 429")    # busy, not withdrawn


def test_failure_reason_is_kept_for_the_panel(cfg, tmp_path, monkeypatch):
    c = copy.deepcopy(cfg)
    c["storage"]["db_path"] = str(tmp_path / "state.db")
    c["ai"] = {"llm": [{"key": "g", "url": "https://x.test", "model": "m", "key_env": "G_KEY", "budget": 3}]}
    monkeypatch.setenv("G_KEY", "k")
    st = ai.run_llm(c, [_case(i, False, NOW - i * HOUR) for i in range(3)], NOW, lambda *a, **k: _Resp("", 200))
    assert st["errors"]["g"] == "API failed" and st["why"]["g"].startswith("empty answer")
