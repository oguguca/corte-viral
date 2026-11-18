"""Correções da revisão final da live ao vivo (cada teste reproduz um achado)."""
from pathlib import Path

import pytest

from corteviral import ffmpeg
from corteviral.live import monitor as mon
from corteviral.live.monitor import shutdown_all, start_monitor
from corteviral.live.state import LiveState, Part, Window
from corteviral.project import Project
from tests.test_live_monitor import Clock, FakeRecorder


@pytest.fixture
def env(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "https://live", "Live")
    rec, clock, finals = FakeRecorder(), Clock(), []

    def make(process, is_live=lambda url: True, sleep=lambda s: None, killer=None):
        return start_monitor(p, settings_tmp, "https://live", {}, recorder=rec, process=process, is_live=is_live,
                             clock=clock, sleep=sleep, finalize_fn=lambda pr, st: finals.append(True), thread=False,
                             **({"killer": killer} if killer else {}))
    yield p, rec, clock, finals, make
    mon.ACTIVE.clear()


def _done(project, state, win, settings, **kw):
    win.state = "done"
    return []


# 1. queda de rede: "não sei se está ao vivo" não pode encerrar a live
def test_unknown_live_status_retries_instead_of_finishing(env):
    p, rec, clock, finals, make = env
    m = make(_done, is_live=lambda url: None)
    rec.sizes["part-000.ts"] = 100
    m.step()
    rec.alive = False
    m.step()
    assert finals == [] and rec.started == [0, 1]


# 2. Parar durante a espera entre tentativas não pode reabrir a gravação
def test_stop_during_backoff_does_not_restart_recording(env):
    p, rec, clock, finals, make = env
    holder = {}
    m = make(_done, sleep=lambda s: holder["m"].stop())
    holder["m"] = m
    rec.sizes["part-000.ts"] = 100
    m.step()
    rec.alive = False
    m.step()
    assert rec.started == [0] and finals == [True]


# 3. no fechamento, janela que a IA não avaliou é tentada uma vez só (não 500)
def test_finish_tries_pending_ai_once(env):
    p, rec, clock, finals, make = env
    calls = []

    def always_pending(project, state, win, settings, **kw):
        calls.append(win.n)
        win.state = "pending_ai"
        return []
    m = make(always_pending, is_live=lambda url: False)
    rec.sizes["part-000.ts"] = 400
    m.step()              # janela 0 → pending_ai
    rec.alive = False
    m.step()              # encerra: janela 1 (resto) + uma tentativa da 0
    assert sorted(calls) == [0, 0, 1] and finals == [True]


# 9. janela que deu erro passageiro é tentada de novo (até 3 vezes)
def test_failed_window_is_retried(env):
    p, rec, clock, finals, make = env
    calls = []

    def flaky(project, state, win, settings, **kw):
        calls.append(win.n)
        if len(calls) == 1:
            raise OSError("disco ocupado")
        win.state = "done"
        return []
    m = make(flaky)
    rec.sizes["part-000.ts"] = 300
    m.step()
    clock.t += 300
    m.step()
    assert calls == [0, 0] and m.state.windows[0].state == "done"


# 6. fechar o app para a gravação; retomar mata o gravador órfão de antes
def test_shutdown_all_stops_recorders_but_keeps_live_resumable(env):
    p, rec, clock, finals, make = env
    make(_done)
    shutdown_all()
    assert not rec.alive and LiveState.load(p.dir / "live.json").active and not mon.ACTIVE


def test_resume_kills_stale_recorder_pid(env):
    p, rec, clock, finals, make = env
    killed = []
    st = LiveState(url="https://live", parts=[Part(file="part-000.ts", offset=0, duration=50, pid=999)])
    st.save(p.dir / "live.json")
    make(_done, killer=killed.append)
    assert killed == [999]


# 5. durante a live dá para ajustar clipes depois dos primeiros 5 min, mas só dentro da janela do clipe
def test_live_edit_validates_against_window(settings_tmp):
    from corteviral.review import ReviewError, _validate_cut
    p = Project.create(settings_tmp.paths.projects_dir, "https://live", "Live")
    p.data.update(duration=300.0)
    src = {"video": "w.mp4", "offset": 600.0, "end": 900.0}
    _validate_cut(p, 610.0, 640.0, source=src)          # janela 600–900: ok mesmo com duration=300
    with pytest.raises(ReviewError):
        _validate_cut(p, 590.0, 620.0, source=src)       # antes da janela: não existe no vídeo dela


def test_process_window_keeps_main_duration_at_recorded_length(settings_tmp, tmp_path, monkeypatch):
    from corteviral.live import window as wmod
    p = Project.create(settings_tmp.paths.projects_dir, "https://live", "Live")
    st = LiveState(url="u", parts=[Part(file="x.ts", offset=0, duration=650)])
    monkeypatch.setattr(wmod, "cut_window", lambda *a: (a[3].mkdir(parents=True, exist_ok=True),
                                                         (a[3] / "video.mp4").write_bytes(b"v"))[1])

    def tr(wp, s, on_progress=None):
        from corteviral.models import Transcript
        t = Transcript(language="pt", duration=1, words=[], segments=[], model="m", device="d")
        t.save(wp.path("transcript.json"))
        return t

    def an(wp, s, n=None, profile=None, on_progress=None):
        from corteviral.pipeline.analyze import MOMENTS
        from corteviral.utils import write_json_atomic
        write_json_atomic(wp.path(MOMENTS), {"complete": True, "moments": []})
    wmod.process_window(p, st, Window(n=1, part=0, start=240, end=540), settings_tmp,
                        transcribe_fn=tr, analyze_fn=an, render_fn=lambda *a, **k: None)
    assert Project.load(p.dir).data["duration"] == 650


# 4 e 8. corte busca antes de abrir (rápido em live longa) e nunca deixa vídeo pela metade
def test_cut_window_seeks_on_input_and_is_atomic(tmp_path, monkeypatch):
    from corteviral.live import window as wmod
    cmds = []

    def fake_run(args):
        cmds.append(args)
        out = Path(args[-1])
        out.write_bytes(b"metade")
        raise ffmpeg.FFmpegError("caiu no meio")
    monkeypatch.setattr(wmod.ffmpeg, "run", fake_run)
    with pytest.raises(ffmpeg.FFmpegError):
        wmod.cut_window(Path("p.ts"), 0.0, Window(n=0, part=0, start=3600, end=3900), tmp_path / "w")
    a = cmds[0]
    assert a.index("-ss") < a.index("-i")
    assert not (tmp_path / "w" / "video.mp4").exists()
