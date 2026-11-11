import numpy as np
import pytest

from corteviral.models import Segment, Transcript, Word
from corteviral.pipeline.render import render_clip
from corteviral.project import Project
from corteviral.vision.frames import grab

CAM = {"rect": [0.70, 0.05, 0.25, 0.30], "face": [0.8, 0.1, 0.05, 0.08], "share": 1.0, "conf": 0.9}
MOMENT = {"id": "m00001", "start": 1.0, "end": 6.0, "score": 80}


def _project(settings, sample_video, words=True):
    p = Project.create(settings.paths.projects_dir, str(sample_video), "teste")
    p.data.update(video_path=str(sample_video), duration=8.0,
                  layout_detect={"cams": [CAM], "gameplay": None, "source": [1280, 720]})
    p.save()
    ws = [Word(w="OLHA", start=1.5, end=2.4, p=1), Word(w="ISSO", start=2.5, end=4.0, p=1)] if words else []
    Transcript(language="pt", duration=8.0, words=ws, segments=[Segment(start=0, end=8, text="x")],
               model="tiny", device="cpu").save(p.path("transcript.json"))
    return p


@pytest.fixture
def rs(settings_tmp):
    settings_tmp.render.presets_dir = settings_tmp.paths.data_dir / "presets"
    settings_tmp.render.encoder = "libx264"
    return settings_tmp


def _bands(path):
    img = grab(path, [1.5], width=540)[0].astype(np.int16)  # 540x960
    return img[:300], img[600:740]  # topo (câmera) e faixa da legenda (y≈1344/1920 → 672/960)


def test_render_with_captions_burns_text(rs, sample_video):
    p = _project(rs, sample_video)
    with_c = render_clip(p, MOMENT, rs, captions=True)
    top_c, band_c = _bands(with_c)
    p2 = _project(rs, sample_video)
    without = render_clip(p2, MOMENT, rs, captions=False)
    top_n, band_n = _bands(without)
    changed = np.abs(band_c - band_n).sum(axis=2) > 60
    assert changed.mean() > 0.03                   # legenda queimada em parte da faixa
    assert (np.abs(top_c - top_n).sum(axis=2) > 60).mean() < 0.001  # resto do quadro igual
    assert with_c.with_suffix(".ass").exists()


def test_render_without_speech_has_no_caption_events(rs, sample_video):
    p = _project(rs, sample_video, words=False)
    out = render_clip(p, MOMENT, rs, captions=True)
    assert out.exists()
    ass = out.with_suffix(".ass")
    assert not ass.exists() or "Dialogue:" not in ass.read_text(encoding="utf-8")


def test_caption_path_with_accents_and_spaces(rs, sample_video, tmp_path):
    rs.paths.projects_dir = tmp_path / "João Teste" / "projetos ção"
    p = _project(rs, sample_video)
    out = render_clip(p, MOMENT, rs, captions=True)
    _, band = _bands(out)
    assert out.exists() and band.std() > 5


def test_caption_change_invalidates_cache(rs, sample_video, monkeypatch):
    p = _project(rs, sample_video)
    render_clip(p, MOMENT, rs, captions=True)
    calls = []
    monkeypatch.setattr("corteviral.ffmpeg.run", lambda args: calls.append(args))
    render_clip(p, MOMENT, rs, captions=True)
    assert calls == []
    rs.captions.highlight = "#00FF00"
    render_clip(p, MOMENT, rs, captions=True)
    assert len(calls) == 1
    render_clip(p, {**MOMENT, "caption_words": [{"w": "EDITADO", "start": 1.5, "end": 3.0}]}, rs, captions=True)
    assert len(calls) == 2


def test_moment_toggle_overrides_job_and_style(rs, sample_video, monkeypatch):
    p = _project(rs, sample_video)
    seen = []
    monkeypatch.setattr("corteviral.ffmpeg.run", lambda args: seen.append("ass=" in " ".join(args)))
    render_clip(p, {**MOMENT, "captions": False}, rs, captions=True)
    render_clip(p, {**MOMENT, "id": "m2", "captions": True}, rs, captions=False)
    rs.captions.enabled = False
    render_clip(p, {**MOMENT, "id": "m3"}, rs)
    render_clip(p, {**MOMENT, "id": "m4"}, rs, captions=True)
    assert seen == [False, True, False, True]
