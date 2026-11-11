from pathlib import Path

import pytest
from pydantic import ValidationError

from corteviral.presets import LayoutPreset, load_preset, resolve_layout, save_preset
from corteviral.project import Project


def test_region_clamped_and_validated():
    p = LayoutPreset(facecam=[0.9, -0.1, 0.3, 0.5], gameplay=[0, 0, 1, 1])
    x, y, w, h = p.facecam
    assert x + w <= 1.0 and y >= 0 and w > 0
    with pytest.raises(ValidationError):
        LayoutPreset(facecam=[0.1, 0.1, 0.0, 0.2])
    with pytest.raises(ValidationError):
        LayoutPreset(gameplay=[0.1, 0.2, 0.3])


def test_save_load_roundtrip_by_slug(tmp_path):
    save_preset(tmp_path, "Flow Games", LayoutPreset(facecam=[0.84, 0.18, 0.13, 0.18], credit_handle="@flowgames"))
    assert (tmp_path / "flow-games.json").exists()
    p = load_preset(tmp_path, "Flow Games")
    assert p.facecam == [0.84, 0.18, 0.13, 0.18] and p.credit_handle == "@flowgames"
    assert load_preset(tmp_path, "outro") is None
    assert load_preset(tmp_path, None) is None


def _project(settings, streamer=None, detect=None):
    p = Project.create(settings.paths.projects_dir, "x.mp4", "x")
    p.data.update(video_path="x.mp4", duration=100.0, streamer=streamer)
    if detect is not None:
        p.data["layout_detect"] = detect
    p.save()
    return p


def test_resolve_prefers_preset_then_detection_then_fallback(settings_tmp, monkeypatch):
    settings_tmp.render.presets_dir = settings_tmp.paths.data_dir / "presets"
    det = {"cams": [{"rect": [0.84, 0.18, 0.13, 0.18], "face": [0, 0, 0.1, 0.1], "share": 1, "conf": 0.9}],
           "gameplay": [0.02, 0.16, 0.8, 0.8], "source": [1920, 1080]}
    # 1) detecção salva no projeto
    p = _project(settings_tmp, "Flow Games", det)
    r = resolve_layout(p, settings_tmp)
    assert r.layout == "split" and r.facecam == [0.84, 0.18, 0.13, 0.18] and r.gameplay == [0.02, 0.16, 0.8, 0.8]
    # 2) preset do streamer tem prioridade
    save_preset(settings_tmp.render.presets_dir, "Flow Games", LayoutPreset(layout="fallback"))
    assert resolve_layout(p, settings_tmp).layout == "fallback"
    # 3) sem câmera detectada → fallback; detecção roda se não houver
    calls = []
    monkeypatch.setattr("corteviral.presets.analyze_layout",
                        lambda video, duration, models_dir: calls.append(1) or {"cams": [], "gameplay": None, "source": [1280, 720]})
    p2 = _project(settings_tmp, "Sem Cam")
    r2 = resolve_layout(p2, settings_tmp)
    assert r2.layout == "fallback" and calls == [1]
    assert Project.load(p2.dir).data["layout_detect"]["source"] == [1280, 720]


def test_ingest_stores_streamer_from_channel(settings_tmp, monkeypatch):
    from corteviral.pipeline.ingest import ingest
    monkeypatch.setattr("corteviral.pipeline.ingest._probe_url", lambda url, settings: {
        "title": "Live", "id": "abc", "extractor_key": "Youtube", "channel": "Flow Games", "live_status": "was_live"})

    def fake_download(url, project, settings, on_progress):
        f = project.path("source.mp4")
        f.write_bytes(b"x")
        return f
    monkeypatch.setattr("corteviral.pipeline.ingest._download", fake_download)
    monkeypatch.setattr("corteviral.ffmpeg.run", lambda args: Path(args[-1]).write_bytes(b"RIFF"))
    monkeypatch.setattr("corteviral.ffmpeg.probe_duration", lambda p: 10.0)
    p = ingest("https://youtu.be/abc", settings_tmp)
    assert p.data["streamer"] == "Flow Games"
