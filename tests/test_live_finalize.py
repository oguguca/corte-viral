import pytest

from corteviral import ffmpeg
from corteviral.live.finalize import finalize
from corteviral.live.state import LiveState, Part
from corteviral.pipeline.analyze import MOMENTS, load_moments
from corteviral.project import Project
from corteviral.utils import write_json_atomic
from corteviral.vision.frames import grab


def _part(path, seconds, color="red"):
    ffmpeg.run(["-y", "-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=30:duration={seconds}",
                "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-shortest", "-f", "mpegts", str(path)])
    return path


@pytest.fixture
def live_project(settings_tmp, tmp_path):
    p = Project.create(settings_tmp.paths.projects_dir, "https://live", "Live")
    write_json_atomic(p.path(MOMENTS), {"moments": [{"id": "L000-m1", "start": 1, "end": 3, "score": 80,
                                                     "source": {"video": "x", "offset": 0}}], "all": []})
    return p


def test_finalize_fills_gap_and_keeps_timeline(live_project, tmp_path):
    a, b = _part(tmp_path / "part-000.ts", 5, "red"), _part(tmp_path / "part-001.ts", 5, "blue")
    st = LiveState(url="u", parts=[Part(file=str(a), offset=0, duration=5), Part(file=str(b), offset=8, duration=5)])
    out = finalize(live_project, st)
    assert abs(ffmpeg.probe_duration(out) - 13.0) < 0.3
    frame = grab(out, [6.5], width=64)[0]          # no buraco: preto
    assert frame.mean() < 20
    after = grab(out, [10.0], width=64)[0]          # depois do buraco: azul (BGR → canal 0)
    assert after[..., 0].mean() > 150


def test_finalize_single_part_remux_strips_sources_and_sets_video_path(live_project, tmp_path):
    a = _part(tmp_path / "part-000.ts", 4)
    st = LiveState(url="u", parts=[Part(file=str(a), offset=0, duration=4)])
    out = finalize(live_project, st)
    p = Project.load(live_project.dir)
    assert p.data["video_path"] == str(out) and abs(p.data["duration"] - 4.0) < 0.3
    assert p.path("audio.wav").exists()
    assert "source" not in load_moments(p)["moments"][0]
    assert not st.active and st.ended
