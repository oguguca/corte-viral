import math
import wave

import numpy as np

from corteviral.models import Segment, Transcript, Word
from corteviral.pipeline.analyze import analyze, load_moments
from corteviral.project import Project


def make_project(settings, seconds=180, loud_at=90):
    p = Project.create(settings.paths.projects_dir, "live.mp4", "Live teste")
    rate = 16000
    t = np.arange(seconds * rate) / rate
    sig = 0.02 * np.sin(2 * math.pi * 200 * t)
    sig[loud_at * rate:(loud_at + 2) * rate] = 0.9 * np.sin(2 * math.pi * 440 * t[:2 * rate])
    with wave.open(str(p.path("audio.wav")), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((sig * 32767).astype(np.int16).tobytes())
    words, x = [], 0.0
    while x < seconds - 1:
        words.append(Word(w="fala", start=round(x, 2), end=round(x + 0.3, 2), p=0.9))
        x += 0.6
    words += [Word(w="MEU", start=loud_at + 0.1, end=loud_at + 0.3, p=1), Word(w="DEUS!", start=loud_at + 0.3, end=loud_at + 0.6, p=1)]
    words.sort(key=lambda w: w.start)
    Transcript(language="pt", duration=float(seconds), words=words, model="medium", device="cuda",
               segments=[Segment(start=0, end=seconds - 1, text="x")]).save(p.path("transcript.json"))
    p.data.update(duration=float(seconds), ingest_params={"source": "live.mp4"})
    p.save()
    return p


def test_analyze_end_to_end_without_llm(settings_tmp, monkeypatch):
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat", lambda project, settings: None)
    settings_tmp.llm.provider = "none"
    p = make_project(settings_tmp)
    moments = analyze(p, settings_tmp, n=3)
    assert 1 <= len(moments) <= 3
    top = moments[0]
    assert top["start"] <= 90 <= top["end"]
    assert 15 <= top["end"] - top["start"] <= 60
    assert top["ai"] is False and top["reason"]
    saved = load_moments(p)
    assert saved["moments"][0]["id"] == top["id"] and saved["profile"] == "viral"


def test_analyze_is_cached_and_profile_change_recomputes(settings_tmp, monkeypatch):
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat", lambda project, settings: None)
    settings_tmp.llm.provider = "none"
    p = make_project(settings_tmp, seconds=400, loud_at=200)
    calls = []
    import corteviral.pipeline.analyze as mod
    real = mod.find_candidates
    monkeypatch.setattr(mod, "find_candidates", lambda *a, **k: calls.append(1) or real(*a, **k))
    analyze(p, settings_tmp, n=3)
    analyze(p, settings_tmp, n=3)
    assert len(calls) == 1
    m = analyze(p, settings_tmp, n=3, profile="monetizacao")
    assert len(calls) == 2
    assert all(61 <= x["end"] - x["start"] <= 120 for x in m)


def test_new_transcript_invalidates_moments(settings_tmp, monkeypatch):
    from corteviral.models import Transcript, Word, Segment
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat", lambda project, settings: None)
    settings_tmp.llm.provider = "none"
    p = make_project(settings_tmp)
    p.mark_stage("transcribe", {"model": "medium"})
    first = analyze(p, settings_tmp, n=3)
    # retranscrição com outro modelo: momentos/sinais antigos não podem voltar do cache
    p.mark_stage("transcribe", {"model": "small"})
    calls = []
    import corteviral.pipeline.analyze as mod
    real = mod.find_candidates
    monkeypatch.setattr(mod, "find_candidates", lambda *a, **k: calls.append(1) or real(*a, **k))
    analyze(p, settings_tmp, n=3)
    assert calls == [1]


def test_signal_settings_change_recomputes(settings_tmp, monkeypatch):
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat", lambda project, settings: None)
    settings_tmp.llm.provider = "none"
    p = make_project(settings_tmp)
    analyze(p, settings_tmp, n=3)
    settings_tmp.analysis.extra_reactions = {"fala": 2.0}
    calls = []
    import corteviral.pipeline.analyze as mod
    real = mod.find_candidates
    monkeypatch.setattr(mod, "find_candidates", lambda *a, **k: calls.append(1) or real(*a, **k))
    analyze(p, settings_tmp, n=3)
    assert calls == [1]


def test_partial_ai_result_is_not_cached(settings_tmp, monkeypatch):
    from corteviral.llm import LLMRateLimited
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat", lambda project, settings: None)
    p = make_project(settings_tmp)

    class Limited:
        def score(self, items, profile, language):
            raise LLMRateLimited("429")

    analyze(p, settings_tmp, n=3, client=Limited())
    assert not p.is_stage_done("scoring", {}) and "scoring" not in p.data["stages"]


def test_missing_key_result_is_not_cached(settings_tmp, monkeypatch):
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat", lambda project, settings: None)
    settings_tmp.llm.provider = "gemini"
    p = make_project(settings_tmp)
    analyze(p, settings_tmp, n=3, client=None)
    assert "scoring" not in p.data["stages"]
