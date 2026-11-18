"""Live salva só os melhores momentos: trecho de cada clipe (com folga), nunca a gravação inteira."""
from pathlib import Path

import pytest

from corteviral import ffmpeg
from corteviral.live.finalize import finalize
from corteviral.live.state import LiveState, Part, Window
from corteviral.live.window import process_window
from corteviral.models import Segment, Transcript, Word
from corteviral.pipeline.analyze import MOMENTS, load_moments
from corteviral.project import Project
from corteviral.utils import write_json_atomic


@pytest.fixture
def part_video(tmp_path):
    out = tmp_path / "part-000.ts"
    ffmpeg.run(["-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=60",
                "-f", "lavfi", "-i", "sine=f=440:d=60", "-c:v", "libx264", "-preset", "ultrafast", "-x264-params", "keyint=30:min-keyint=30:scenecut=0",
                "-c:a", "aac", "-f", "mpegts", str(out)])
    return out


def _fakes(moments, complete=True):
    def tr(wp, s, on_progress=None):
        t = Transcript(language="pt", duration=1, words=[Word(w="oi", start=1, end=1.3, p=1)],
                       segments=[Segment(start=1, end=1.3, text="oi")], model="m", device="d")
        t.save(wp.path("transcript.json"))
        return t

    def an(wp, s, n=None, profile=None, on_progress=None):
        write_json_atomic(wp.path(MOMENTS), {"complete": complete, "moments": moments, "all": moments})

    rendered = []
    return dict(transcribe_fn=tr, analyze_fn=an, render_fn=lambda p, m, s, **k: rendered.append(m)), rendered


def test_window_keeps_only_clip_segment_and_deletes_window_video(settings_tmp, part_video):
    settings_tmp.live.keep_margin = 5
    main = Project.create(settings_tmp.paths.projects_dir, "u", "Live")
    st = LiveState(url="u", parts=[Part(file=str(part_video), offset=0, duration=60)])
    win = Window(n=0, part=0, start=0, end=60)
    fakes, rendered = _fakes([{"id": "m1", "start": 20.0, "end": 30.0, "score": 90}])
    process_window(main, st, win, settings_tmp, **fakes)
    m = load_moments(main)["moments"][0]
    seg = Path(m["source"]["video"])
    assert seg.exists() and seg.parent.name == "src"
    assert 14.0 <= m["source"]["offset"] <= 15.1 and abs(m["source"]["end"] - 35.0) < 0.5  # ±5 s de folga
    assert abs(ffmpeg.probe_duration(seg) - (m["source"]["end"] - m["source"]["offset"])) < 0.3
    assert rendered and rendered[0]["source"]["video"] == str(seg)      # o clipe sai do trecho salvo
    assert not (main.dir / "live" / "win-000" / "video.mp4").exists()   # a janela de 5 min some
    assert not (main.dir / "live" / "win-000" / "audio.wav").exists()
    assert Path(Project.load(main.dir).data["video_path"]) == seg       # nada aponta para arquivo apagado


def test_window_without_clips_deletes_its_video(settings_tmp, part_video):
    main = Project.create(settings_tmp.paths.projects_dir, "u", "Live")
    st = LiveState(url="u", parts=[Part(file=str(part_video), offset=0, duration=60)])
    fakes, _ = _fakes([])
    process_window(main, st, Window(n=0, part=0, start=0, end=60), settings_tmp, **fakes)
    assert not (main.dir / "live" / "win-000" / "video.mp4").exists()


def test_window_waiting_for_ai_keeps_its_video(settings_tmp, part_video):
    main = Project.create(settings_tmp.paths.projects_dir, "u", "Live")
    st = LiveState(url="u", parts=[Part(file=str(part_video), offset=0, duration=60)])
    fakes, _ = _fakes([], complete=False)
    process_window(main, st, Window(n=0, part=0, start=0, end=60), settings_tmp, **fakes)
    assert (main.dir / "live" / "win-000" / "video.mp4").exists()  # ainda vai ser avaliada


def test_finalize_deletes_recording_and_keeps_clip_segments(settings_tmp, part_video, tmp_path):
    p = Project.create(settings_tmp.paths.projects_dir, "u", "Live")
    seg = p.dir / "live" / "src" / "L000-m1.mp4"
    seg.parent.mkdir(parents=True)
    seg.write_bytes(b"trecho")
    write_json_atomic(p.path(MOMENTS), {"moments": [{"id": "L000-m1", "start": 20, "end": 30, "score": 90,
                                                     "source": {"video": str(seg), "offset": 15, "end": 35}}]})
    st = LiveState(url="u", parts=[Part(file=str(part_video), offset=0, duration=60)])
    finalize(p, st)
    assert not part_video.exists() and not p.path("video.mp4").exists()   # nada de gravação inteira
    assert seg.exists() and load_moments(p)["moments"][0]["source"]["video"] == str(seg)
    assert st.finalized and st.ended and not st.active


def test_keyframe_before_is_relative_to_file_start(part_video):
    # MPEG-TS não começa em 0 (aqui ~1,4 s; numa live real pode ser qualquer valor)
    from corteviral.live.window import keyframe_before
    assert abs(keyframe_before(part_video, 10.3) - 10.0) < 0.05


def test_keyframe_at_very_start_is_found(part_video):
    from corteviral.live.window import keyframe_before
    assert keyframe_before(part_video, 0.0) < 0.1  # senão o corte do começo da live recodifica tudo


def _clock_video(path):
    """Vídeo cuja cor diz o tempo: vermelho = (t mod 12) * 20."""
    ffmpeg.run(["-y", "-f", "lavfi", "-i", "color=c=black:size=160x120:rate=30:duration=40",
                "-f", "lavfi", "-i", "sine=d=40", "-vf", "geq=r='mod(T,12)*20':g=0:b=0",
                "-c:v", "libx264", "-preset", "ultrafast", "-x264-params", "keyint=30:min-keyint=30:scenecut=0",
                "-c:a", "aac", "-f", "mpegts", str(path)])
    return path


def _clock_time(video, t):
    import subprocess
    import numpy as np
    raw = subprocess.run([ffmpeg.binary("ffmpeg"), "-v", "error", "-i", str(video), "-vf",
                          f"select='gte(t\,{t})',scale=8:8", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)[:, 0].mean() / 20


def test_cut_and_segment_are_time_aligned_with_the_live(tmp_path):
    from corteviral.live.window import cut_window, save_segment
    part = _clock_video(tmp_path / "clock.ts")
    w = Window(n=0, part=0, start=17.0, end=35.0)
    v = cut_window(part, 0.0, w, tmp_path / "w")
    assert abs((_clock_time(v, 0.5) + 12) - (w.start + 0.5)) < 0.12   # tempo 0 do corte = w.start na live
    src = save_segment(v, w, {"start": 25.0, "end": 30.0}, 5, tmp_path / "seg.mp4")
    assert abs((_clock_time(Path(src["video"]), 0.5) + 12) - (src["offset"] + 0.5)) < 0.12
