import time

import pytest
from fastapi.testclient import TestClient

from corteviral.models import Transcript, Word, Segment
from corteviral.pipeline.ingest import IngestError
from corteviral.project import Project
from corteviral.server.app import create_app


def _wait(client, job_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job não terminou")


def _fake_pipeline(settings_tmp):
    def run(source, settings, on_progress):
        assert settings.whisper.model == "small"           # opção vinda do formulário
        assert settings.download.cookies_from_browser == "firefox"
        on_progress("ingest", 1.0, "ingest concluído")
        p = Project.create(settings.paths.projects_dir, source, "Minha Live")
        Transcript(language="pt", duration=2.0, model="small", device="cuda",
                   words=[Word(w="oi", start=0.1, end=0.4, p=0.9)],
                   segments=[Segment(start=0.1, end=0.4, text="oi")]).save(p.path("transcript.json"))
        on_progress("transcribe", 1.0, "pronto")
        return p
    return run


@pytest.fixture
def client(settings_tmp):
    app = create_app(settings_tmp, pipeline=_fake_pipeline(settings_tmp))
    return TestClient(app)


def test_index_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200 and "Corte Viral" in r.text


def test_job_runs_and_reports_progress_then_project(client):
    r = client.post("/api/jobs", json={"source": "https://youtu.be/abc", "model": "small",
                                       "language": "auto", "cookies_from_browser": "firefox"})
    assert r.status_code == 200
    job = _wait(client, r.json()["id"])
    assert job["status"] == "done" and job["stage"] == "transcribe" and job["progress"] == 1.0
    projects = client.get("/api/projects").json()
    assert projects[0]["title"] == "Minha Live" and projects[0]["has_transcript"]
    assert job["project"] == projects[0]["name"]


def test_job_error_message_is_returned(settings_tmp):
    def failing(source, settings, on_progress):
        raise IngestError("vídeo com restrição de idade")
    c = TestClient(create_app(settings_tmp, pipeline=failing))
    job = _wait(c, c.post("/api/jobs", json={"source": "x"}).json()["id"])
    assert job["status"] == "error" and "restrição de idade" in job["error"]


def test_only_one_job_at_a_time(settings_tmp):
    import threading
    gate = threading.Event()

    def slow(source, settings, on_progress):
        gate.wait(2)
        raise IngestError("fim")
    c = TestClient(create_app(settings_tmp, pipeline=slow))
    assert c.post("/api/jobs", json={"source": "a"}).status_code == 200
    r = c.post("/api/jobs", json={"source": "b"})
    gate.set()
    assert r.status_code == 409


def test_transcript_endpoint(client):
    job = _wait(client, client.post("/api/jobs", json={
        "source": "x", "model": "small", "cookies_from_browser": "firefox"}).json()["id"])
    t = client.get(f"/api/projects/{job['project']}/transcript").json()
    assert t["words"][0]["w"] == "oi" and t["language"] == "pt"


def test_project_name_cannot_escape_projects_dir(client):
    assert client.get("/api/projects/..%2F..%2Fetc/transcript").status_code == 404
    assert client.get("/api/projects/nao-existe/transcript").status_code == 404


def test_video_endpoint_supports_range(client, settings_tmp, sample_video):
    p = Project.create(settings_tmp.paths.projects_dir, "v", "v")
    p.data["video_path"] = str(sample_video)
    p.save()
    r = client.get(f"/api/projects/{p.dir.name}/video", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


def test_job_passes_cookie_file_to_settings(settings_tmp):
    seen = {}

    def capture(source, settings, on_progress):
        seen["file"] = settings.download.cookies_file
        seen["browser"] = settings.download.cookies_from_browser
        raise IngestError("fim")
    c = TestClient(create_app(settings_tmp, pipeline=capture))
    _wait(c, c.post("/api/jobs", json={"source": "x", "cookies_file": "C:/c/cookies.txt"}).json()["id"])
    assert str(seen["file"]).replace("\\", "/") == "C:/c/cookies.txt" and seen["browser"] is None


def test_session_status_not_connected(client):
    s = client.get("/api/session/status").json()
    assert s["connected"] is False


def test_session_sync_saves_status(settings_tmp, monkeypatch):
    def fake_export(profile_dir, out_file, timeout=20):
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        return {"count": 12, "platforms": ["youtube", "twitch"]}
    monkeypatch.setattr("corteviral.server.app.export_cookies", fake_export)
    (settings_tmp.paths.data_dir / "session-profile").mkdir(parents=True)
    c = TestClient(create_app(settings_tmp, pipeline=_fake_pipeline(settings_tmp)))
    r = c.post("/api/session/sync").json()
    assert r["platforms"] == ["youtube", "twitch"]
    assert c.get("/api/session/status").json()["connected"] is True


def test_session_sync_reports_open_window(settings_tmp, monkeypatch):
    from corteviral.browser_session import BrowserSessionError

    def fail(*a, **k):
        raise BrowserSessionError("feche a janela de login (Edge) antes de continuar")
    monkeypatch.setattr("corteviral.server.app.export_cookies", fail)
    (settings_tmp.paths.data_dir / "session-profile").mkdir(parents=True)
    c = TestClient(create_app(settings_tmp))
    r = c.post("/api/session/sync")
    assert r.status_code == 409 and "feche a janela" in r.json()["detail"]


def test_url_job_refreshes_and_uses_app_session(settings_tmp, monkeypatch):
    calls = []

    def fake_export(profile_dir, out_file, timeout=20):
        calls.append(out_file)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        return {"count": 3, "platforms": ["youtube"]}
    monkeypatch.setattr("corteviral.server.app.export_cookies", fake_export)
    (settings_tmp.paths.data_dir / "session-profile").mkdir(parents=True)
    seen = {}

    def capture(source, settings, on_progress):
        seen["file"] = settings.download.cookies_file
        raise IngestError("fim")
    c = TestClient(create_app(settings_tmp, pipeline=capture))
    _wait(c, c.post("/api/jobs", json={"source": "https://www.youtube.com/watch?v=x"}).json()["id"])
    assert calls and seen["file"] == calls[0]
    seen.clear()
    _wait(c, c.post("/api/jobs", json={"source": "https://kick.com/fulano/videos/abc"}).json()["id"])
    assert seen["file"] == calls[0]  # qualquer site usa a sessão do app


def test_local_file_job_does_not_touch_session(settings_tmp, monkeypatch):
    monkeypatch.setattr("corteviral.server.app.export_cookies",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria")))
    (settings_tmp.paths.data_dir / "session-profile").mkdir(parents=True)
    seen = {}

    def capture(source, settings, on_progress):
        seen["file"] = settings.download.cookies_file
        raise IngestError("fim")
    c = TestClient(create_app(settings_tmp, pipeline=capture))
    _wait(c, c.post("/api/jobs", json={"source": "C:/videos/live.mp4"}).json()["id"])
    assert seen["file"] is None


def _transcribed_project(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "https://youtu.be/z", "Live Z")
    p.data["url"] = "https://youtu.be/z"
    p.save()
    return p


def test_api_analyze_job_and_moments_endpoint(settings_tmp):
    from corteviral.pipeline.analyze import MOMENTS
    from corteviral.utils import write_json_atomic
    seen = {}

    def fake_analyze(project, settings, n=None, profile=None, on_progress=None):
        seen.update(n=n, profile=profile, provider=settings.llm.provider)
        on_progress(0.5, "IA avaliando")
        moments = [{"id": "m1", "start": 10.0, "end": 40.0, "score": 88, "reason": "engraçado", "ai": True}]
        write_json_atomic(project.path(MOMENTS), {"profile": profile, "moments": moments})
        return moments

    p = _transcribed_project(settings_tmp)
    c = TestClient(create_app(settings_tmp, analyzer=fake_analyze))
    job = _wait(c, c.post("/api/jobs", json={"kind": "analyze", "project": p.dir.name, "n": 5,
                                             "profile": "monetizacao", "provider": "none"}).json()["id"])
    assert job["status"] == "done" and job["stage"] == "analyze"
    assert seen == {"n": 5, "profile": "monetizacao", "provider": "none"}
    m = c.get(f"/api/projects/{p.dir.name}/moments").json()
    assert m["moments"][0]["score"] == 88
    assert c.get("/api/projects").json()[0]["has_moments"] is True


def test_api_moments_404_when_not_analyzed(client, settings_tmp):
    p = _transcribed_project(settings_tmp)
    assert client.get(f"/api/projects/{p.dir.name}/moments").status_code == 404


def test_api_analyze_requires_project(client):
    assert client.post("/api/jobs", json={"kind": "analyze"}).status_code == 422


def test_api_usage_percent(settings_tmp):
    from corteviral.usage import UsageTracker
    settings_tmp.llm.daily_request_limit = 10
    settings_tmp.llm.daily_token_limit = 1000
    UsageTracker(settings_tmp.paths.data_dir / "usage.json").record("gemini", "m", 100, 50, ok=True)
    u = TestClient(create_app(settings_tmp)).get("/api/usage").json()
    assert u["requests"] == 1 and u["total_tokens"] == 150
    assert u["limits"] == {"requests": 10, "tokens": 1000}
    assert u["percent"] == 15.0  # max(1/10=10%, 150/1000=15%)
    assert len(u["per_minute"]) == 60


def _video_project(settings_tmp, sample_video, cams=True):
    p = Project.create(settings_tmp.paths.projects_dir, str(sample_video), "Video")
    det = {"cams": [{"rect": [0.7, 0.05, 0.25, 0.3], "face": [0.8, 0.1, 0.05, 0.08], "share": 1.0, "conf": 0.9}] if cams else [],
           "gameplay": [0.0, 0.0, 1.0, 1.0], "source": [1280, 720]}
    p.data.update(video_path=str(sample_video), duration=8.0, streamer="Canal Teste", layout_detect=det)
    p.save()
    return p


def test_api_layout_detect_and_save(settings_tmp, sample_video):
    settings_tmp.render.presets_dir = settings_tmp.paths.data_dir / "presets"
    p = _video_project(settings_tmp, sample_video)
    c = TestClient(create_app(settings_tmp))
    r = c.get(f"/api/projects/{p.dir.name}/layout").json()
    assert r["origin"] == "detected" and r["preset"]["layout"] == "split" and r["streamer"] == "Canal Teste"
    assert len(r["detected"]["cams"]) == 1
    new = {"layout": "split", "facecam": [0.6, 0.0, 0.3, 0.3], "gameplay": [0.0, 0.2, 0.7, 0.8], "credit_handle": "@canal"}
    assert c.put(f"/api/projects/{p.dir.name}/layout", json=new).status_code == 200
    r2 = c.get(f"/api/projects/{p.dir.name}/layout").json()
    assert r2["origin"] == "preset" and r2["preset"]["facecam"] == [0.6, 0.0, 0.3, 0.3]
    assert (settings_tmp.render.presets_dir / "canal-teste.json").exists()


def test_api_layout_rejects_invalid_region(settings_tmp, sample_video):
    p = _video_project(settings_tmp, sample_video)
    c = TestClient(create_app(settings_tmp))
    r = c.put(f"/api/projects/{p.dir.name}/layout", json={"layout": "split", "facecam": [0.1, 0.1, 0.0, 0.2]})
    assert r.status_code == 422


def test_api_streamer_rename(settings_tmp, sample_video):
    p = _video_project(settings_tmp, sample_video)
    c = TestClient(create_app(settings_tmp))
    assert c.put(f"/api/projects/{p.dir.name}/streamer", json={"streamer": "Flow Games"}).status_code == 200
    assert Project.load(p.dir).data["streamer"] == "Flow Games"


def test_api_frame_jpeg(settings_tmp, sample_video):
    p = _video_project(settings_tmp, sample_video)
    r = TestClient(create_app(settings_tmp)).get(f"/api/projects/{p.dir.name}/frame?t=2")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"


def test_api_render_job_and_clip_endpoint(settings_tmp, sample_video):
    from corteviral.pipeline.analyze import MOMENTS
    from corteviral.utils import write_json_atomic
    settings_tmp.render.presets_dir = settings_tmp.paths.data_dir / "presets"
    p = _video_project(settings_tmp, sample_video)
    write_json_atomic(p.path(MOMENTS), {"profile": "viral", "moments": [
        {"id": "m1", "start": 1.0, "end": 4.0, "score": 80, "reason": "r", "ai": False},
        {"id": "m2", "start": 4.0, "end": 7.0, "score": 70, "reason": "r", "ai": False}]})
    c = TestClient(create_app(settings_tmp))
    job = _wait(c, c.post("/api/jobs", json={"kind": "render", "project": p.dir.name, "ids": ["m2"],
                                             "preview": True}).json()["id"], timeout=120)
    assert job["status"] == "done", job
    m = c.get(f"/api/projects/{p.dir.name}/moments").json()
    assert m["clips"]["m2"]["preview"] is True and "m1" not in m["clips"]
    r = c.get(f"/api/projects/{p.dir.name}/clips/m2?preview=1", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert c.get(f"/api/projects/{p.dir.name}/clips/m1").status_code == 404
