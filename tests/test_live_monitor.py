from pathlib import Path

import pytest

from corteviral.live import monitor as mon
from corteviral.live.monitor import LiveError, LiveMonitor, start_monitor
from corteviral.live.recorder import Recorder
from corteviral.live.state import LiveState
from corteviral.project import Project


class FakeRecorder:
    def __init__(self):
        self.alive = True
        self.sizes: dict[str, float] = {}
        self.started: list[int] = []
        self.dir = None

    def start(self, n):
        self.started.append(n)
        self.alive = True
        return Path(f"part-{n:03d}.ts")

    def running(self):
        return self.alive

    def recorded(self, path):
        return self.sizes.get(Path(path).name, 0.0)

    def stop(self):
        self.alive = False


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def setup(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "https://live", "Live")
    rec, clock, done, finals = FakeRecorder(), Clock(), [], []

    def process(project, state, win, settings, **kw):
        done.append((win.n, win.start, win.end))
        win.state = "done"
        return []

    def make(is_live=lambda url: True):
        return start_monitor(p, settings_tmp, "https://live", {"min_seconds": 20, "max_seconds": 35},
                             recorder=rec, process=process, is_live=is_live, clock=clock, sleep=lambda s: None,
                             finalize_fn=lambda project, state: finals.append(True), thread=False)
    yield p, rec, clock, done, finals, make
    mon.ACTIVE.clear()


def test_step_processes_windows_in_order_one_per_step(setup):
    p, rec, clock, done, finals, make = setup
    m = make()
    rec.sizes["part-000.ts"] = 650
    m.step()
    m.step()
    m.step()
    assert done == [(0, 0.0, 300.0), (1, 240.0, 600.0)]  # 3º passo: só 50 s novos, espera
    assert LiveState.load(p.dir / "live.json").windows[1].state == "done"


def test_recorder_crash_while_live_starts_new_part_with_wall_offset(setup):
    p, rec, clock, done, finals, make = setup
    m = make()
    rec.sizes["part-000.ts"] = 100
    m.step()
    rec.alive = False
    clock.t = 1150.0
    m.step()
    assert rec.started == [0, 1]
    assert [(x.file.endswith("part-001.ts"), x.offset) for x in m.state.parts][1] == (True, 150.0)


def test_live_ended_finalizes_without_retrying(setup):
    p, rec, clock, done, finals, make = setup
    m = make(is_live=lambda url: False)
    rec.sizes["part-000.ts"] = 400
    m.step()  # janela 0–300
    rec.alive = False
    m.step()
    assert rec.started == [0] and finals == [True]
    assert done[-1] == (1, 240.0, 400.0)  # o resto da gravação é processado antes de fechar
    st = LiveState.load(p.dir / "live.json")
    assert st.ended and not st.active and "https://live" not in [x.state.url for x in mon.ACTIVE.values()]


def test_pending_ai_retried_before_new_windows(setup, settings_tmp):
    p, rec, clock, done, finals, make = setup
    m = make()
    calls = []

    def flaky(project, state, win, settings, **kw):
        calls.append(win.n)
        win.state = "pending_ai" if len(calls) == 1 else "done"
        return []
    m.process = flaky
    rec.sizes["part-000.ts"] = 650
    m.step()                 # janela 0 fica pending_ai
    m.step()                 # ainda não deu o tempo de tentar de novo: segue para a janela 1
    clock.t += 300
    m.step()                 # agora tenta a 0 de novo antes de qualquer janela nova
    assert calls == [0, 1, 0]


def test_only_one_live_at_a_time(setup, settings_tmp):
    p, rec, clock, done, finals, make = setup
    make()
    with pytest.raises(LiveError):
        make()


def test_state_survives_reload(setup, settings_tmp):
    p, rec, clock, done, finals, make = setup
    m = make()
    rec.sizes["part-000.ts"] = 300
    m.step()
    mon.ACTIVE.clear()
    m2 = make()  # retomar: lê o live.json, abre parte nova e não refaz a janela 0
    assert [w.n for w in m2.state.windows] == [0] and len(m2.state.parts) == 2


def test_recorder_command_records_from_now_as_mpegts(settings_tmp, tmp_path):
    seen = {}

    class P:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd

        def poll(self):
            return None

    r = Recorder("https://youtu.be/x", tmp_path, settings_tmp, popen=P)
    out = r.start(2)
    cmd = seen["cmd"]
    assert out == tmp_path / "part-002.ts" and str(out) in cmd and "--hls-use-mpegts" in cmd
    assert "--live-from-start" not in cmd and cmd[-1] == "https://youtu.be/x" and r.running()


def test_recorder_finds_file_when_ytdlp_appends_extension(tmp_path):
    (tmp_path / "part-000.ts.mp4").write_bytes(b"x")  # live com vídeo+áudio separados: yt-dlp põe .mp4 no fim
    assert Recorder.resolve(tmp_path / "part-000.ts") == tmp_path / "part-000.ts.mp4"
    assert Recorder.resolve(tmp_path / "part-001.ts") == tmp_path / "part-001.ts"


def test_recorder_format_merges_video_and_audio(settings_tmp, tmp_path):
    cmd = Recorder("https://x", tmp_path, settings_tmp).command(tmp_path / "p.ts")
    fmt = cmd[cmd.index("-f") + 1]
    assert fmt.startswith("bv*[height<=") and "+ba" in fmt


def test_recorder_stop_kills_whole_process_tree_on_windows(settings_tmp, tmp_path, monkeypatch):
    import corteviral.live.recorder as recmod
    ran = []

    class P:
        pid = 4321

        def __init__(self, cmd, **kw):
            pass

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(recmod.os, "name", "nt")
    monkeypatch.setattr(recmod.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    r = Recorder("https://x", tmp_path, settings_tmp, popen=P)
    r.start(0)
    r.stop()
    assert ran and ran[0][:2] == ["taskkill", "/PID"] and "4321" in ran[0] and "/T" in ran[0]  # leva o ffmpeg junto
