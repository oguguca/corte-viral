import json
import subprocess

import pytest

from corteviral import ffmpeg
from corteviral.pipeline.render import render_clip
from corteviral.project import Project
from corteviral.vision.frames import video_size

CAM = {"rect": [0.70, 0.05, 0.25, 0.30], "face": [0.8, 0.1, 0.05, 0.08], "share": 1.0, "conf": 0.9}


def _project(settings, sample_video, cams=(CAM,)):
    p = Project.create(settings.paths.projects_dir, str(sample_video), "teste")
    p.data.update(video_path=str(sample_video), duration=8.0,
                  layout_detect={"cams": list(cams), "gameplay": None, "source": [1280, 720]})
    p.save()
    return p


def _probe(path):
    out = subprocess.run([ffmpeg.binary("ffprobe"), "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    d = json.loads(out)
    return float(d["format"]["duration"]), {s["codec_type"] for s in d["streams"]}


MOMENT = {"id": "m00003", "start": 1.0, "end": 6.0, "score": 80}


@pytest.fixture
def rs_fast(settings_tmp):
    settings_tmp.render.presets_dir = settings_tmp.paths.data_dir / "presets"
    return settings_tmp


def test_render_split_outputs_1080x1920_with_duration(rs_fast, sample_video):
    p = _project(rs_fast, sample_video)
    out = render_clip(p, MOMENT, rs_fast)
    assert out.exists() and out.name == "m00003.mp4"
    assert video_size(out) == (1080, 1920)
    dur, kinds = _probe(out)
    assert abs(dur - 5.0) < 0.25 and kinds == {"video", "audio"}


def test_render_fallback_without_cam(rs_fast, sample_video):
    p = _project(rs_fast, sample_video, cams=())
    out = render_clip(p, MOMENT, rs_fast)
    assert video_size(out) == (1080, 1920)
    meta = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["layout"] == "fallback"


def test_render_cached_second_time(rs_fast, sample_video, monkeypatch):
    p = _project(rs_fast, sample_video)
    render_clip(p, MOMENT, rs_fast)
    calls = []
    monkeypatch.setattr("corteviral.ffmpeg.run", lambda args: calls.append(args))
    render_clip(p, MOMENT, rs_fast)
    assert calls == []
    render_clip(p, {**MOMENT, "end": 5.0}, rs_fast)  # trecho mudou → renderiza de novo
    assert len(calls) == 1


def test_nvenc_failure_falls_back_to_x264(rs_fast, sample_video, caplog):
    rs_fast.render.encoder = "encoder_que_nao_existe"
    p = _project(rs_fast, sample_video)
    out = render_clip(p, MOMENT, rs_fast)
    assert video_size(out) == (1080, 1920)
    meta = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["encoder"] == "libx264"


def test_preview_is_540x960(rs_fast, sample_video):
    p = _project(rs_fast, sample_video)
    out = render_clip(p, MOMENT, rs_fast, preview=True)
    assert out.name == "m00003.preview.mp4" and video_size(out) == (540, 960)


def test_failed_encoder_is_not_retried_for_next_clips(rs_fast, sample_video, monkeypatch):
    import corteviral.pipeline.render as mod
    rs_fast.render.encoder = "encoder_quebrado_2"
    p = _project(rs_fast, sample_video)
    render_clip(p, MOMENT, rs_fast)
    used = []
    real = mod.ffmpeg.run
    monkeypatch.setattr(mod.ffmpeg, "run", lambda args: used.append(args[args.index("-c:v") + 1]) or real(args))
    render_clip(p, {**MOMENT, "id": "m00004", "start": 2.0}, rs_fast)
    assert used == ["libx264"]
