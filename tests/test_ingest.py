import os
from pathlib import Path

import pytest

from corteviral.pipeline.ingest import IngestError, ingest, is_url
from tests.conftest import probe_audio


def test_is_url():
    assert is_url("https://www.twitch.tv/videos/123")
    assert is_url("https://kick.com/x/videos/abc")
    assert is_url("https://youtu.be/abc")
    assert not is_url(r"C:\Users\x\Desktop\live.mp4")
    assert not is_url("live.mp4")


def test_ingest_local_file_extracts_16k_mono_wav(sample_video, settings_tmp):
    p = ingest(str(sample_video), settings_tmp)
    wav = p.path("audio.wav")
    assert wav.exists()
    assert probe_audio(wav) == {"sample_rate": 16000, "channels": 1}
    assert abs(p.data["duration"] - 8.0) < 0.2
    assert p.is_stage_done("ingest", p.data["ingest_params"])


def test_ingest_local_does_not_copy_large_file(sample_video, settings_tmp):
    p = ingest(str(sample_video), settings_tmp)
    vp = Path(p.data["video_path"])
    assert vp.samefile(sample_video) or os.stat(vp).st_nlink > 1


def test_ingest_twice_is_cached(sample_video, settings_tmp, monkeypatch):
    p1 = ingest(str(sample_video), settings_tmp)
    calls = []
    monkeypatch.setattr("corteviral.ffmpeg.run", lambda args: calls.append(args))
    p2 = ingest(str(sample_video), settings_tmp)
    assert p1.dir == p2.dir
    assert calls == []


def test_live_in_progress_rejected(monkeypatch, settings_tmp):
    monkeypatch.setattr(
        "corteviral.pipeline.ingest._probe_url",
        lambda url: {"title": "x", "is_live": True, "live_status": "is_live"},
    )
    with pytest.raises(IngestError, match="live em andamento"):
        ingest("https://www.twitch.tv/fulano", settings_tmp)


def test_missing_local_file(settings_tmp):
    with pytest.raises(IngestError, match="não encontrado"):
        ingest(r"C:\nao\existe.mp4", settings_tmp)
