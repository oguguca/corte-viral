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
        lambda url, settings: {"title": "x", "is_live": True, "live_status": "is_live"},
    )
    with pytest.raises(IngestError, match="live em andamento"):
        ingest("https://www.twitch.tv/fulano", settings_tmp)


def test_missing_local_file(settings_tmp):
    with pytest.raises(IngestError, match="não encontrado"):
        ingest(r"C:\nao\existe.mp4", settings_tmp)


def test_ydl_opts_include_browser_cookies(settings_tmp):
    from corteviral.pipeline.ingest import _ydl_base_opts
    assert "cookiesfrombrowser" not in _ydl_base_opts(settings_tmp)
    settings_tmp.download.cookies_from_browser = "firefox"
    assert _ydl_base_opts(settings_tmp)["cookiesfrombrowser"] == ("firefox",)


def test_age_restricted_error_is_friendly():
    from corteviral.pipeline.ingest import _friendly_error
    raw = "ERROR: [youtube] PhUWBpUeCjk: Sign in to confirm your age. This video may be inappropriate"
    msg = _friendly_error(raw)
    assert "restrição de idade" in msg and "Login do navegador" in msg and "--cookies-from-browser" in msg


def test_private_or_members_error_is_friendly():
    from corteviral.pipeline.ingest import _friendly_error
    assert "login" in _friendly_error("ERROR: [twitch:vod] 123: This video is only available for subscribers")
    assert _friendly_error("ERROR: algo inesperado") == "ERROR: algo inesperado"


def test_post_live_rejected(monkeypatch, settings_tmp):
    monkeypatch.setattr("corteviral.pipeline.ingest._probe_url",
                        lambda url, settings: {"title": "x", "live_status": "post_live"})
    with pytest.raises(IngestError, match="processado"):
        ingest("https://www.youtube.com/watch?v=abc", settings_tmp)


def test_same_video_with_different_url_params_reuses_project(monkeypatch, settings_tmp):
    info = {"title": "Live", "id": "abc", "extractor_key": "Youtube", "live_status": "was_live"}
    monkeypatch.setattr("corteviral.pipeline.ingest._probe_url", lambda url, settings: info)
    downloads = []

    def fake_download(url, project, settings, on_progress):
        downloads.append(url)
        dst = project.path("source.mp4")
        dst.write_bytes(b"x")
        return dst

    monkeypatch.setattr("corteviral.pipeline.ingest._download", fake_download)
    monkeypatch.setattr("corteviral.ffmpeg.run", lambda args: Path(args[-1]).write_bytes(b"RIFF"))
    monkeypatch.setattr("corteviral.ffmpeg.probe_duration", lambda p: 10.0)
    p1 = ingest("https://www.youtube.com/watch?v=abc&list=PL1", settings_tmp)
    p2 = ingest("https://youtu.be/abc?t=123", settings_tmp)
    assert p1.dir == p2.dir and len(downloads) == 1
    assert p1.data["source"] == "youtube:abc"


def test_probe_does_not_follow_playlists(monkeypatch, settings_tmp):
    from corteviral.pipeline import ingest as mod
    seen = {}

    class FakeYDL:
        def __init__(self, opts): seen.update(opts)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False): return {"title": "t"}

    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    mod._probe_url("https://www.youtube.com/watch?v=abc&list=PL1", settings_tmp)
    assert seen.get("noplaylist") is True


def test_locked_chromium_cookie_db_message_is_browser_agnostic():
    from corteviral.pipeline.ingest import _friendly_error
    msg = _friendly_error("ERROR: Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271")
    assert "Brave" in msg and "cookies.txt" in msg


def test_ydl_opts_include_cookie_file(settings_tmp, tmp_path):
    from corteviral.pipeline.ingest import _ydl_base_opts
    f = tmp_path / "cookies.txt"
    settings_tmp.download.cookies_file = f
    assert _ydl_base_opts(settings_tmp)["cookiefile"] == str(f)
