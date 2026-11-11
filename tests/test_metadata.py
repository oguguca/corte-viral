import io
import json

from corteviral.llm import GeminiClient, LLMError
from corteviral.metadata import (PLATFORMS, fallback_posts, fit, generate_posts, normalize_hashtags,
                                 normalize_post)
from corteviral.pipeline.analyze import MOMENTS, load_moments
from corteviral.project import Project
from corteviral.utils import write_json_atomic


def test_normalize_hashtags():
    tags = normalize_hashtags(["gta6", "#GTA6", "gta 6", "#cortes", "", "#viral!!", "#a" * 1])
    assert tags[:3] == ["#gta6", "#cortes", "#viral"]
    assert all(t.startswith("#") and " " not in t for t in tags)
    assert len(tags) == len({t.lower() for t in tags})
    assert len(normalize_hashtags([f"t{i}" for i in range(30)])) == 10


def test_fit_cuts_on_word_boundary():
    assert fit("curto", 80) == "curto"
    out = fit("palavra " * 20, 30)
    assert len(out) <= 30 and not out.endswith(" ") and out.endswith("…")


def test_shorts_title_gets_shorts_tag():
    p = normalize_post({"title": "Perseguição insana no GTA 6", "description": "x", "hashtags": ["gta6"]}, "shorts", None)
    assert p["title"].endswith("#shorts") and len(p["title"]) <= 100
    t = normalize_post({"title": "Perseguição", "description": "x", "hashtags": []}, "tiktok", None)
    assert "#shorts" not in t["title"]


def test_credit_appended_to_descriptions():
    p = normalize_post({"title": "t", "description": "Olha isso", "hashtags": []}, "reels", "@flowgames")
    assert p["description"].endswith("Créditos: @flowgames") and len(p["description"]) <= 300
    p2 = normalize_post({"title": "t", "description": "x", "hashtags": []}, "reels", "flowgames")
    assert p2["description"].endswith("Créditos: @flowgames")


def test_fallback_posts_without_ai():
    m = {"id": "m1", "hook": "Olha a perseguição", "reason": "ação forte com reação", "text": "meu deus olha isso"}
    out = fallback_posts(m, "Flow Games", None)
    assert out["hook"] == "Olha a perseguição"
    assert set(out["posts"]) == set(PLATFORMS)
    assert "#gta6" in out["posts"]["tiktok"]["hashtags"] and "#flowgames" in out["posts"]["tiktok"]["hashtags"]


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _post(title):
    return {"title": title, "description": "desc", "hashtags": ["gta6", "cortes", "viral", "gaming", "flow"]}


def _project(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "x.mp4", "X")
    p.data["streamer"] = "Flow Games"
    p.save()
    ms = [{"id": "m1", "start": 0, "end": 20, "score": 90, "reason": "r", "text": "t", "ai": True},
          {"id": "m2", "start": 30, "end": 50, "score": 80, "reason": "r", "text": "t", "ai": True}]
    write_json_atomic(p.path(MOMENTS), {"moments": ms, "all": [dict(m) for m in ms]})
    return p


class FakeWriter:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def write_posts(self, items, language):
        self.calls += 1
        if self.fail:
            raise LLMError("fora do ar")
        return [{"id": it["id"], "hook": "Gancho " + it["id"], **{p: _post(f"Título {it['id']} {p}") for p in PLATFORMS}}
                for it in items]


def test_generate_posts_uses_ai_and_saves(settings_tmp):
    p = _project(settings_tmp)
    stats = generate_posts(p, ["m1", "m2"], settings_tmp, client=FakeWriter())
    assert stats == {"ai": 2, "fallback": 0}
    d = load_moments(p)
    meta = d["moments"][0]["meta"]
    assert meta["hook"] == "Gancho m1" and meta["posts"]["shorts"]["title"].endswith("#shorts")
    assert d["all"][0]["meta"] == meta


def test_generate_posts_ai_failure_falls_back(settings_tmp):
    p = _project(settings_tmp)
    stats = generate_posts(p, ["m1"], settings_tmp, client=FakeWriter(fail=True))
    assert stats == {"ai": 0, "fallback": 1}
    assert load_moments(p)["moments"][0]["meta"]["posts"]["kwai"]["hashtags"]


def test_write_posts_request_shape(monkeypatch):
    seen = {}

    def fake(req, timeout):
        seen["body"] = json.loads(req.data)
        out = [{"id": "m1", "hook": "h", **{p: _post("t") for p in PLATFORMS}}]
        return _Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(out)}]}}]}).encode())

    monkeypatch.setattr("corteviral.llm.urllib.request.urlopen", fake)
    out = GeminiClient("k", "m", 5).write_posts([{"id": "m1", "description": "explode", "streamer": "Flow"}], "pt")
    assert out[0]["hook"] == "h"
    schema = seen["body"]["generationConfig"]["responseSchema"]
    assert schema["type"] == "array" and set(PLATFORMS) <= set(schema["items"]["properties"])
    assert "explode" in seen["body"]["contents"][0]["parts"][0]["text"]
