import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from corteviral.config import AnalysisSettings, Settings
from corteviral.pipeline.boundaries import duration_range


def test_duration_range_profile_default_and_custom():
    s = Settings()
    assert duration_range(s) == (15, 60)
    s.analysis.profile = "monetizacao"
    assert duration_range(s) == (61, 120)
    s.analysis.min_seconds, s.analysis.max_seconds = 20, 35
    assert duration_range(s) == (20, 35)


def test_duration_settings_validated():
    with pytest.raises(ValidationError):
        AnalysisSettings(min_seconds=40, max_seconds=30)   # mínimo maior que máximo
    with pytest.raises(ValidationError):
        AnalysisSettings(min_seconds=3, max_seconds=30)    # menos de 5 s
    with pytest.raises(ValidationError):
        AnalysisSettings(min_seconds=30, max_seconds=400)  # mais de 180 s
    with pytest.raises(ValidationError):
        AnalysisSettings(min_seconds=30, max_seconds=32)   # faixa estreita demais (< 5 s)


def test_candidate_windows_follow_custom_range():
    from corteviral.pipeline.candidates import window_for
    s = Settings()
    s.analysis.min_seconds, s.analysis.max_seconds = 15, 30
    pre, post = window_for(s)
    assert 15 <= pre + post <= 40
    s.analysis = AnalysisSettings(min_seconds=61, max_seconds=90)
    pre, post = window_for(s)
    assert pre + post >= 61


def test_analyze_respects_custom_duration(settings_tmp, monkeypatch):
    from tests.test_analyze import make_project
    from corteviral.pipeline.analyze import analyze
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat", lambda project, settings: None)
    settings_tmp.llm.provider = "none"
    settings_tmp.analysis.min_seconds, settings_tmp.analysis.max_seconds = 20, 30
    p = make_project(settings_tmp, seconds=400, loud_at=200)
    ms = analyze(p, settings_tmp, n=3)
    assert ms and all(20 <= m["end"] - m["start"] <= 30 for m in ms)
    settings_tmp.analysis = AnalysisSettings(min_seconds=40, max_seconds=55)
    ms2 = analyze(p, settings_tmp, n=3)  # faixa nova invalida o cache
    assert all(40 <= m["end"] - m["start"] <= 55 for m in ms2)


def test_prompt_mentions_custom_range():
    from corteviral.llm import _review_prompt
    text = _review_prompt({"profile": "viral", "range": [20, 30], "transcript": "x", "window": [0, 60], "examples": []})
    assert "20" in text and "30 s" in text


def test_api_analyze_job_passes_duration(settings_tmp):
    from corteviral.project import Project
    from corteviral.server.app import create_app
    from tests.test_server import _wait
    seen = {}

    def fake(project, settings, n=None, profile=None, on_progress=None):
        seen["range"] = (settings.analysis.min_seconds, settings.analysis.max_seconds)
        return []

    p = Project.create(settings_tmp.paths.projects_dir, "x.mp4", "X")
    c = TestClient(create_app(settings_tmp, analyzer=fake))
    _wait(c, c.post("/api/jobs", json={"kind": "analyze", "project": p.dir.name,
                                       "min_seconds": 20, "max_seconds": 35}).json()["id"])
    assert seen["range"] == (20, 35)
    r = c.post("/api/jobs", json={"kind": "analyze", "project": p.dir.name, "min_seconds": 50, "max_seconds": 20})
    assert r.status_code == 422
