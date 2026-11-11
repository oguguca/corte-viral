from corteviral.config import Settings
from corteviral.llm import LLMRateLimited
from corteviral.models import Segment, Transcript, Word
from corteviral.pipeline.deep_review import apply_review, criteria_score, deep_review
from corteviral.project import Project

W = {"gancho": 0.25, "emocao": 0.25, "visual": 0.15, "final": 0.20, "contexto": 0.15}


def _sentences(duration=600.0):
    words, t = [], 0.0
    while t < duration - 6:
        for k in range(10):
            words.append(Word(w="fim." if k == 9 else "fala", start=round(t, 2), end=round(t + 0.45, 2), p=0.9))
            t += 0.55
        t += 0.5
    return Transcript(language="pt", duration=duration, words=words, segments=[Segment(start=0, end=duration, text="x")])


def _review(**kw):
    r = {"descricao": "explode o carro", "motivo": "reação forte", "risco": "nenhum", "gancho": 90, "emocao": 80,
         "visual": 70, "final": 60, "contexto": 50, "inicio": 15.0, "fim": 45.0}
    r.update(kw)
    return r


def test_criteria_score_weighted():
    assert criteria_score({"gancho": 100, "emocao": 100, "visual": 100, "final": 100, "contexto": 100}, W) == 100
    assert criteria_score({"gancho": 90, "emocao": 80, "visual": 70, "final": 60, "contexto": 50}, W) == 73


def test_apply_review_converts_and_snaps_cut():
    tr = _sentences()
    m = {"id": "a", "start": 200.0, "end": 240.0, "score": 60, "reason": "x", "ai": True}
    out = apply_review(m, _review(), (185.0, 255.0), tr, (15, 60), W)
    assert out["ai"] == "video" and out["score"] == 73 and out["triage_score"] == 60
    assert out["criteria"]["gancho"] == 90 and out["description"] == "explode o carro" and out["risk"] == "nenhum"
    assert out["reason"] == "reação forte"
    assert 194.0 <= out["start"] <= 200.0          # 185 + 15, recuado até o início da frase (pausa)
    assert 230.0 <= out["end"] <= 236.0 and 15 <= out["end"] - out["start"] <= 60


def test_absurd_cut_falls_back_to_triage_bounds():
    tr = _sentences()
    m = {"id": "a", "start": 200.0, "end": 240.0, "score": 60, "reason": "x", "ai": True}
    for bad in (_review(inicio=50.0, fim=20.0), _review(inicio=-30.0, fim=10.0), _review(inicio=10.0, fim=500.0),
                _review(inicio=20.0, fim=21.0)):
        out = apply_review(m, bad, (185.0, 255.0), tr, (15, 60), W)
        assert abs(out["start"] - 200.0) <= 10 and abs(out["end"] - 240.0) <= 10, bad


class FakeVision:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def review_video(self, video_bytes, context):
        self.calls.append(context["window"])
        if self.fail_at is not None and len(self.calls) > self.fail_at:
            raise LLMRateLimited("429")
        assert video_bytes[:4] != b""
        return _review()


def _proj(settings, sample_video):
    p = Project.create(settings.paths.projects_dir, str(sample_video), "x")
    p.data.update(video_path=str(sample_video), duration=8.0, streamer="Canal")
    p.save()
    return p


def _moments(n):
    return [{"id": f"m{i}", "start": 1.0, "end": 6.0, "score": 50 + i, "reason": "r", "ai": True} for i in range(n)]


def test_only_top_k_are_reviewed(settings_tmp, sample_video):
    settings_tmp.deep.top_k = 2
    tr = _sentences(8.0)
    vis = FakeVision()
    out, stats = deep_review(_proj(settings_tmp, sample_video), _moments(4), tr, settings_tmp, vis)
    reviewed = {m["id"] for m in out if m["ai"] == "video"}
    assert reviewed == {"m3", "m2"} and stats["video"] == 2 and len(vis.calls) == 2


def test_quota_midway_keeps_triage_for_rest(settings_tmp, sample_video):
    settings_tmp.deep.top_k = 4
    vis = FakeVision(fail_at=1)
    out, stats = deep_review(_proj(settings_tmp, sample_video), _moments(4), _sentences(8.0), settings_tmp, vis)
    assert stats["video"] == 1 and stats["rate_limited"] is True
    assert sum(m["ai"] == "video" for m in out) == 1 and len(out) == 4
    assert len(vis.calls) == 2


def test_client_without_vision_skips(settings_tmp, sample_video):
    class TextOnly:
        pass
    out, stats = deep_review(_proj(settings_tmp, sample_video), _moments(2), _sentences(8.0), settings_tmp, TextOnly())
    assert stats["video"] == 0 and all(m["ai"] is True for m in out)


def test_analyze_runs_deep_stage_and_marks_ai_video(settings_tmp, monkeypatch, sample_video):
    from tests.test_analyze import make_project
    from corteviral.pipeline.analyze import analyze, load_moments
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat", lambda project, settings: None)
    settings_tmp.deep.top_k = 2
    p = make_project(settings_tmp, seconds=180, loud_at=90)
    p.data["video_path"] = str(sample_video)
    p.save()
    monkeypatch.setattr("corteviral.pipeline.deep_review.make_mini_clip",
                        lambda video, s, e, out: (out.parent.mkdir(parents=True, exist_ok=True), out.write_bytes(b"mp4"))[1] and out)

    class Both(FakeVision):
        def score(self, items, profile, language):
            return [{"id": it["id"], "score": 60, "reason": "ok", "hook": "h", "standalone": True} for it in items]

    ms = analyze(p, settings_tmp, n=3, client=Both())
    assert any(m["ai"] == "video" for m in ms)
    d = load_moments(p)
    assert d["stats"]["deep"]["video"] >= 1
