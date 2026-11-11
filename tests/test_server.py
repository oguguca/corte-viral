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
