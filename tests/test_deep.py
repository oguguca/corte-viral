import io
import json
import subprocess

import pytest

from corteviral import ffmpeg
from corteviral.llm import GeminiClient, LLMError
from corteviral.pipeline.deep import make_mini_clip, mini_window
from corteviral.usage import UsageTracker
from corteviral.vision.frames import video_size


def test_mini_window_clamped_to_vod():
    assert mini_window({"start": 5.0, "end": 30.0}, duration=100.0, margin=15) == (0.0, 45.0)
    assert mini_window({"start": 80.0, "end": 98.0}, duration=100.0, margin=15) == (65.0, 100.0)


def _probe(path):
    out = subprocess.run([ffmpeg.binary("ffprobe"), "-v", "error", "-show_entries",
                          "stream=codec_type,avg_frame_rate,channels", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def test_make_mini_clip_is_1fps_360p_with_audio(sample_video, tmp_path):
    out = make_mini_clip(sample_video, 1.0, 7.0, tmp_path / "mini.mp4")
    assert video_size(out)[1] == 360
    s = _probe(out)
    assert s["video"]["avg_frame_rate"] == "1/1"
    assert s["audio"]["channels"] == 1


def test_mini_clip_too_big_falls_back_to_240p(sample_video, tmp_path, monkeypatch):
    monkeypatch.setattr("corteviral.pipeline.deep.MAX_BYTES", 1)
    out = make_mini_clip(sample_video, 1.0, 7.0, tmp_path / "mini.mp4")
    assert video_size(out)[1] == 240


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


REVIEW = {"descricao": "explode o carro", "motivo": "reação forte", "risco": "nenhum", "gancho": 90,
          "emocao": 80, "visual": 95, "final": 70, "contexto": 85, "inicio": 12.0, "fim": 40.0}


def test_review_video_request_shape(monkeypatch, tmp_path):
    seen = {}

    def fake(req, timeout):
        seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        return _Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(REVIEW)}]}}],
                                 "usageMetadata": {"promptTokenCount": 6400, "candidatesTokenCount": 120}}).encode())

    monkeypatch.setattr("corteviral.llm.urllib.request.urlopen", fake)
    tracker = UsageTracker(tmp_path / "u.json")
    c = GeminiClient("SEGREDO", "gemini-3.5-flash-lite", 30, tracker)
    out = c.review_video(b"\x00\x01video", {"profile": "viral", "streamer": "Flow", "transcript": "MEU DEUS",
                                            "window": [10.0, 80.0], "examples": []})
    assert out["gancho"] == 90 and out["inicio"] == 12.0
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "video/mp4"
    assert parts[0]["inline_data"]["data"] == "AAF2aWRlbw=="
    assert "MEU DEUS" in parts[1]["text"]
    cfg = seen["body"]["generationConfig"]
    assert cfg["mediaResolution"] == "MEDIA_RESOLUTION_LOW" and "responseSchema" in cfg
    assert "SEGREDO" not in seen["url"] and seen["headers"]["X-goog-api-key"] == "SEGREDO"
    assert tracker.today()["total_tokens"] == 6520


def test_review_video_invalid_json_is_llm_error(monkeypatch):
    monkeypatch.setattr("corteviral.llm.urllib.request.urlopen", lambda req, timeout: _Resp(json.dumps(
        {"candidates": [{"content": {"parts": [{"text": "não é json"}]}}]}).encode()))
    with pytest.raises(LLMError):
        GeminiClient("k", "m", 5).review_video(b"x", {"profile": "viral", "transcript": "", "window": [0, 1], "examples": []})


def test_review_video_missing_fields_is_llm_error(monkeypatch):
    monkeypatch.setattr("corteviral.llm.urllib.request.urlopen", lambda req, timeout: _Resp(json.dumps(
        {"candidates": [{"content": {"parts": [{"text": json.dumps({"gancho": 50})}]}}]}).encode()))
    with pytest.raises(LLMError):
        GeminiClient("k", "m", 5).review_video(b"x", {"profile": "viral", "transcript": "", "window": [0, 1], "examples": []})
