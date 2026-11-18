import json

import pytest

from corteviral import ffmpeg
from corteviral.live.state import LiveState, Part, Window
from corteviral.live.window import cut_window, process_window
from corteviral.models import Segment, Transcript, Word
from corteviral.pipeline.analyze import MOMENTS, load_moments
from corteviral.project import Project
from corteviral.utils import write_json_atomic


@pytest.fixture
def part_video(tmp_path):
    out = tmp_path / "part-000.ts"
    ffmpeg.run(["-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=30",
                "-f", "lavfi", "-i", "sine=f=440:d=30", "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-f", "mpegts", str(out)])
    return out


def _probe(path):
    return ffmpeg.probe_duration(path)


def test_cut_window_is_exact(part_video, tmp_path):
    win = Window(n=1, part=0, start=110.0, end=120.0)  # parte começa em 100 s na linha do tempo
    video = cut_window(part_video, 100.0, win, tmp_path / "win-001")
    # sem recodificar: começa no quadro-chave antes de 110 s e a janela passa a começar nele
    assert 100.0 <= win.start <= 110.0
    assert abs(_probe(video) - (win.end - win.start)) < 0.2
    assert (tmp_path / "win-001" / "audio.wav").exists()


def test_cut_window_copies_without_reencoding(part_video, tmp_path, monkeypatch):
    from corteviral.live import window as wmod
    calls = []
    real = wmod.ffmpeg.run
    monkeypatch.setattr(wmod.ffmpeg, "run", lambda args: (calls.append(args), real(args))[1])
    cut_window(part_video, 0.0, Window(n=0, part=0, start=12.0, end=22.0), tmp_path / "w")
    video_cut = calls[0]
    assert video_cut[video_cut.index("-c") + 1] == "copy"  # rápido no notebook: nada de libx264 aqui


def _main(settings):
    p = Project.create(settings.paths.projects_dir, "https://live", "Live do Flow")
    p.data.update(streamer="flow")
    p.save()
    return p


def _fakes(calls, complete=True, moments=None):
    def transcribe_fn(wp, settings, on_progress=None):
        t = Transcript(language="pt", duration=10.0, model="m", device="groq",
                       words=[Word(w="oi", start=1.0, end=1.4, p=1.0), Word(w="gente", start=5.0, end=5.5, p=1.0)],
                       segments=[Segment(start=1.0, end=5.5, text="oi gente")])
        t.save(wp.path("transcript.json"))
        calls.append("transcribe")
        return t

    def analyze_fn(wp, settings, n=None, profile=None, on_progress=None):
        calls.append(("analyze", settings.analysis.max_auto_clips, settings.deep.top_k, n))
        ms = moments if moments is not None else [{"id": "m1", "start": 2.0, "end": 8.0, "score": 85, "hook": "Ele falou?"}]
        write_json_atomic(wp.path(MOMENTS), {"complete": complete, "moments": ms, "all": ms})
        return ms

    def render_fn(project, moment, settings, **kw):
        calls.append(("render", moment["id"]))

    return dict(transcribe_fn=transcribe_fn, analyze_fn=analyze_fn, render_fn=render_fn)


@pytest.fixture
def fake_cut(monkeypatch):
    """Recorte falso que não mexe no início da janela (estes testes são sobre transcrição e momentos)."""
    from corteviral.live import window as wmod

    def cut(part_path, part_offset, win, dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "video.mp4").write_bytes(b"v")
        return dest / "video.mp4"
    monkeypatch.setattr(wmod, "cut_window", cut)


def _state(part_video):
    return LiveState(url="https://live", opts={"min_seconds": 20, "max_seconds": 35, "profile": "viral"},
                     parts=[Part(file=str(part_video), offset=100.0, duration=30.0)])


def test_process_window_appends_transcript_and_moments(settings_tmp, part_video, fake_cut):
    main, calls = _main(settings_tmp), []
    win = Window(n=1, part=0, start=110.0, end=120.0)
    new = process_window(main, _state(part_video), win, settings_tmp, **_fakes(calls))
    assert [m["id"] for m in new] == ["L001-m1"]
    m = load_moments(main)["moments"][0]
    assert (m["start"], m["end"]) == (112.0, 118.0)
    assert m["source"]["offset"] == 110.0 and m["source"]["video"].endswith("video.mp4")
    words = Transcript.load(main.path("transcript.json")).words
    assert [(w.w, w.start) for w in words] == [("oi", 111.0), ("gente", 115.0)]
    assert ("analyze", 3, 3, None) in calls and ("render", "L001-m1") in calls
    assert win.state == "done" and win.clips == ["L001-m1"]
    assert Project.load(main.dir).data["video_path"].endswith("video.mp4")  # provisório p/ layout e /video


def test_overlap_moment_not_duplicated(settings_tmp, part_video, fake_cut):
    main, calls = _main(settings_tmp), []
    write_json_atomic(main.path(MOMENTS), {"moments": [{"id": "L000-m9", "start": 111.0, "end": 117.0, "score": 80}],
                                           "all": []})
    new = process_window(main, _state(part_video), Window(n=1, part=0, start=110.0, end=120.0), settings_tmp,
                         **_fakes(calls))
    assert new == [] and len(load_moments(main)["moments"]) == 1


def test_incomplete_analysis_marks_pending_ai_and_renders_nothing(settings_tmp, part_video, fake_cut):
    main, calls = _main(settings_tmp), []
    win = Window(n=1, part=0, start=110.0, end=120.0)
    assert process_window(main, _state(part_video), win, settings_tmp, **_fakes(calls, complete=False)) == []
    assert win.state == "pending_ai" and win.tries == 1
    assert not any(c[0] == "render" for c in calls if isinstance(c, tuple))


def test_overlap_words_not_duplicated_in_transcript(settings_tmp, part_video, fake_cut):
    main, calls = _main(settings_tmp), []
    st = _state(part_video)
    process_window(main, st, Window(n=1, part=0, start=110.0, end=120.0), settings_tmp, **_fakes(calls))
    # janela seguinte sobrepõe: as palavras antes do fim já transcrito não entram de novo
    process_window(main, st, Window(n=2, part=0, start=114.0, end=124.0), settings_tmp, **_fakes(calls, moments=[]))
    words = [(w.w, w.start) for w in Transcript.load(main.path("transcript.json")).words]
    assert words == [("oi", 111.0), ("gente", 115.0), ("gente", 119.0)]
