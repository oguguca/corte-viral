"""Pendências menores da live ao vivo (cada teste reproduz uma)."""
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from corteviral.live import monitor as mon
from corteviral.live.monitor import start_monitor
from corteviral.live.state import LiveState, Part, Window
from corteviral.project import Project
from corteviral.server.app import create_app
from tests.test_live_monitor import Clock, FakeRecorder


@pytest.fixture(autouse=True)
def _clean():
    yield
    mon.ACTIVE.clear()


# M1: clipe que não é de live mantém o mesmo hash de cache de antes (não re-renderiza tudo)
def test_render_hash_has_no_source_key_for_normal_clips(settings_tmp, monkeypatch):
    from corteviral.pipeline import render as r
    seen = []
    monkeypatch.setattr(r, "params_hash", lambda d: (seen.append(d), "h")[1])
    from corteviral.presets import LayoutPreset
    monkeypatch.setattr(r, "resolve_layout", lambda *a: LayoutPreset(layout="fallback"))
    monkeypatch.setattr(r, "video_size", lambda v: (1280, 720))
    monkeypatch.setattr(r, "silences", lambda *a: [])
    monkeypatch.setattr(r.ffmpeg, "run", lambda args: (_ for _ in ()).throw(RuntimeError("parar aqui")))
    p = Project.create(settings_tmp.paths.projects_dir, "x", "x")
    p.data.update(video_path="v.mp4")
    with pytest.raises(RuntimeError):
        r.render_clip(p, {"id": "m1", "start": 0, "end": 10}, settings_tmp)
    keys = [d for d in seen if "preset" in d]
    assert keys and all("source" not in d for d in keys)


# M2: Retomar usa a sessão de login do app (live de membros / com idade)
def test_resume_uses_app_login_cookies(settings_tmp):
    got = {}
    data = Path(settings_tmp.paths.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    (data / "session-cookies.txt").write_text("# Netscape", encoding="utf-8")
    p = Project.create(settings_tmp.paths.projects_dir, "youtube:x#live", "Live")
    LiveState(url="https://youtu.be/x").save(p.dir / "live.json")

    def factory(project, s, url, opts):
        got["cookies"] = s.download.cookies_file

    c = TestClient(create_app(settings_tmp, live_factory=factory))
    assert c.post(f"/api/live/resume?project={p.dir.name}").status_code == 200
    assert got["cookies"] and Path(got["cookies"]).name == "session-cookies.txt"


# M3: ao retomar, todas as partes têm a duração relida (cauda gravada depois do último save)
def test_resume_rereads_all_part_durations(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "u", "Live")
    LiveState(url="u", parts=[Part(file="part-000.ts", offset=0, duration=100)]).save(p.dir / "live.json")
    rec = FakeRecorder()
    rec.sizes["part-000.ts"] = 180
    m = start_monitor(p, settings_tmp, "u", {}, recorder=rec, process=lambda *a, **k: [], is_live=lambda u: True,
                      clock=Clock(), sleep=lambda s: None, finalize_fn=lambda *a: None, thread=False, killer=lambda p: None)
    assert m.state.parts[0].duration == 180


# M4: mudanças no projeto feitas pela tela durante a live chegam às janelas novas
def test_process_window_uses_fresh_project_data(settings_tmp, monkeypatch):
    from corteviral.live import window as wmod
    from corteviral.models import Transcript
    from corteviral.pipeline.analyze import MOMENTS
    from corteviral.utils import write_json_atomic
    main = Project.create(settings_tmp.paths.projects_dir, "u", "Live")
    fresh = Project.load(main.dir)
    fresh.data["streamer"] = "coringa"  # usuário trocou o preset pela tela
    fresh.save()
    monkeypatch.setattr(wmod, "cut_window", lambda *a: (a[3].mkdir(parents=True, exist_ok=True),
                                                         (a[3] / "video.mp4").write_bytes(b"v"))[1])
    seen = {}

    def tr(wp, s, on_progress=None):
        seen["streamer"] = wp.data.get("streamer")
        t = Transcript(language="pt", duration=1, words=[], segments=[], model="m", device="d")
        t.save(wp.path("transcript.json"))
        return t

    wmod.process_window(main, LiveState(url="u", parts=[Part(file="x", offset=0, duration=300)]),
                        Window(n=0, part=0, start=0, end=300), settings_tmp, transcribe_fn=tr,
                        analyze_fn=lambda wp, *a, **k: write_json_atomic(wp.path(MOMENTS), {"complete": True, "moments": []}),
                        render_fn=lambda *a, **k: None)
    assert seen["streamer"] == "coringa"


# M5: monitor e "Gerar de novo" no mesmo clipe não rodam o ffmpeg ao mesmo tempo
def test_same_clip_never_renders_concurrently(monkeypatch):
    from corteviral.pipeline import render as r
    active, peak = [0], [0]

    def body():
        with r.clip_lock(Path("x/clips/m1.mp4")):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            time.sleep(0.1)
            active[0] -= 1
    ts = [threading.Thread(target=body) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak[0] == 1


# M6: se juntar as partes falhar, o app sabe (e dá para tentar de novo); com sucesso, libera o disco
def test_finalize_failure_is_recorded_and_retryable(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "u", "Live")
    rec = FakeRecorder()

    def boom(project, state):
        raise RuntimeError("disco cheio")
    m = start_monitor(p, settings_tmp, "u", {}, recorder=rec, process=lambda *a, **k: [], is_live=lambda u: False,
                      clock=Clock(), sleep=lambda s: None, finalize_fn=boom, thread=False, killer=lambda p: None)
    rec.alive = False
    m.step()
    st = LiveState.load(p.dir / "live.json")
    assert st.ended and not st.finalized
    called = []
    c = TestClient(create_app(settings_tmp, live_finalize=lambda project, state: called.append(True)))
    item = next(x for x in c.get("/api/projects").json() if x["name"] == p.dir.name)
    assert item["live_ended"] and not item["live_finalized"]
    assert c.post(f"/api/live/finalize?project={p.dir.name}").status_code == 200 and called


def test_successful_finalize_frees_disk(settings_tmp, tmp_path):
    from corteviral import ffmpeg
    from corteviral.live.finalize import finalize
    p = Project.create(settings_tmp.paths.projects_dir, "u", "Live")
    live = p.dir / "live"
    (live / "win-000").mkdir(parents=True)
    part = live / "part-000.ts"
    ffmpeg.run(["-y", "-f", "lavfi", "-i", "color=c=red:size=160x120:rate=30:duration=3", "-f", "lavfi",
                "-i", "sine=d=3", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                "-f", "mpegts", str(part)])
    (live / "win-000" / "video.mp4").write_bytes(b"v" * 1000)
    st = LiveState(url="u", parts=[Part(file=str(part), offset=0, duration=3)])
    finalize(p, st)
    assert st.finalized and not part.exists() and not (live / "win-000" / "video.mp4").exists()
    assert not p.path("video.mp4").exists()  # só os melhores momentos ficam salvos


def test_projects_list_keeps_past_live_with_its_date(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "kick:1#live", "Live de ontem")
    LiveState(url="u", active=False, ended=True, finalized=True, started_at=1795000000.0).save(p.dir / "live.json")
    item = next(x for x in TestClient(create_app(settings_tmp)).get("/api/projects").json() if x["name"] == p.dir.name)
    assert item["live"] is False and item["live_ended"] and item["live_started"] == 1795000000.0


def test_new_live_while_previous_is_finishing_explains_what_to_do(settings_tmp):
    from corteviral.live.monitor import LiveError
    p = Project.create(settings_tmp.paths.projects_dir, "u", "Live")
    rec = FakeRecorder()
    m = start_monitor(p, settings_tmp, "u", {}, recorder=rec, process=lambda *a, **k: [], is_live=lambda u: True,
                      clock=Clock(), sleep=lambda s: None, finalize_fn=lambda *a: None, thread=False, killer=lambda p: None)
    m.stop()
    q = Project.create(settings_tmp.paths.projects_dir, "u2", "Outra")
    with pytest.raises(LiveError, match="terminando"):
        start_monitor(q, settings_tmp, "u2", {}, recorder=FakeRecorder(), thread=False, killer=lambda p: None)
