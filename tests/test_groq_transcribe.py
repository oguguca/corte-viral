import io
import json
import urllib.error

import pytest

from corteviral.pipeline import groq_transcribe as gt
from corteviral.pipeline.groq_transcribe import (
    GroqAuthError, GroqError, GroqQuotaExceeded, chunk_plan, parse_response, post_chunk,
)


def test_chunk_plan_last_partial():
    assert chunk_plan(0, 1300, 600) == [(0, 600), (600, 600), (1200, 100)]


def test_chunk_plan_short_video_and_resume():
    assert chunk_plan(0, 8, 600) == [(0, 8)]
    assert chunk_plan(600, 1300, 600) == [(600, 600), (1200, 100)]
    assert chunk_plan(0, 600.3, 600) == [(0, 600)]
    assert chunk_plan(1300, 1300, 600) == []


def test_parse_response_offsets_words_and_segments():
    data = {"language": "Portuguese",
            "segments": [{"start": 0.0, "end": 1.5, "text": " olá pessoal"}],
            "words": [{"word": " olá", "start": 0.1, "end": 0.5}, {"word": "pessoal", "start": 0.6, "end": 1.4},
                      {"word": " ", "start": 1.4, "end": 1.4}]}
    segs, words, lang = parse_response(data, 600.0)
    assert lang == "pt"
    assert segs[0].start == 600.0 and segs[0].end == 601.5 and segs[0].text == "olá pessoal"
    assert [(w.w, w.start, w.end, w.p) for w in words] == [("olá", 600.1, 600.5, 1.0),
                                                          ("pessoal", 600.6, 601.4, 1.0)]


def test_parse_response_silent_chunk():
    assert parse_response({"language": "pt", "segments": [], "words": []}, 0) == ([], [], "pt")


def test_parse_response_language_codes():
    assert parse_response({"language": "english"}, 0)[2] == "en"
    assert parse_response({"language": "es"}, 0)[2] == "es"
    assert parse_response({"language": "klingon"}, 0)[2] is None


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _http_error(code, headers=None):
    return urllib.error.HTTPError("u", code, "x", headers or {}, io.BytesIO(b"{}"))


@pytest.fixture
def chunk(tmp_path):
    p = tmp_path / "c.ogg"
    p.write_bytes(b"OggS")
    return p


def test_post_chunk_sends_word_granularity(chunk, monkeypatch):
    seen = {}

    def fake(req, timeout=None):
        seen["req"] = req
        return _Resp(json.dumps({"text": "oi"}).encode())

    monkeypatch.setattr(gt.urllib.request, "urlopen", fake)
    assert post_chunk(chunk, "k", "whisper-large-v3-turbo", "pt") == {"text": "oi"}
    req = seen["req"]
    assert req.get_header("Authorization") == "Bearer k"
    body = req.data
    assert b'name="timestamp_granularities[]"' in body and b"word" in body and b"segment" in body
    assert b"verbose_json" in body and b"whisper-large-v3-turbo" in body and b"OggS" in body
    assert b'name="language"' in body


def test_post_chunk_429_short_retry_then_ok(chunk, monkeypatch):
    calls = []

    def fake(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, {"retry-after": "2"})
        return _Resp(b'{"ok": 1}')

    slept = []
    monkeypatch.setattr(gt.urllib.request, "urlopen", fake)
    assert post_chunk(chunk, "k", "m", None, sleep=slept.append) == {"ok": 1}
    assert slept == [2.0]


def test_post_chunk_429_long_raises_quota(chunk, monkeypatch):
    def fake(req, timeout=None):
        raise _http_error(429, {"retry-after": "3600"})

    slept = []
    monkeypatch.setattr(gt.urllib.request, "urlopen", fake)
    with pytest.raises(GroqQuotaExceeded):
        post_chunk(chunk, "k", "m", None, sleep=slept.append)
    assert slept == []


def test_post_chunk_429_keeps_failing_raises_quota(chunk, monkeypatch):
    def fake(req, timeout=None):
        raise _http_error(429, {"retry-after": "1"})

    slept = []
    monkeypatch.setattr(gt.urllib.request, "urlopen", fake)
    with pytest.raises(GroqQuotaExceeded):
        post_chunk(chunk, "k", "m", None, sleep=slept.append)
    assert len(slept) == 2


def test_post_chunk_401_raises_auth(chunk, monkeypatch):
    def fake(req, timeout=None):
        raise _http_error(401)

    monkeypatch.setattr(gt.urllib.request, "urlopen", fake)
    with pytest.raises(GroqAuthError):
        post_chunk(chunk, "k", "m", None)


def test_post_chunk_network_error_raises_groq_error(chunk, monkeypatch):
    def fake(req, timeout=None):
        raise urllib.error.URLError("sem internet")

    slept = []
    monkeypatch.setattr(gt.urllib.request, "urlopen", fake)
    with pytest.raises(GroqError):
        post_chunk(chunk, "k", "m", None, sleep=slept.append)
    assert slept == [2.0, 5.0]


def test_post_chunk_transient_errors_retry_then_ok(chunk, monkeypatch):
    errors = [_http_error(503), urllib.error.URLError("wifi caiu")]

    def fake(req, timeout=None):
        if errors:
            raise errors.pop(0)
        return _Resp(b'{"ok": 1}')

    slept = []
    monkeypatch.setattr(gt.urllib.request, "urlopen", fake)
    assert post_chunk(chunk, "k", "m", None, sleep=slept.append) == {"ok": 1}
    assert slept == [2.0, 5.0]


def test_parse_response_drops_no_speech_segments():
    data = {"language": "pt",
            "segments": [{"start": 0.0, "end": 2.0, "text": " Obrigado.", "no_speech_prob": 0.9},
                         {"start": 2.0, "end": 3.0, "text": " oi", "no_speech_prob": 0.1}],
            "words": [{"word": "Obrigado.", "start": 0.5, "end": 1.0}, {"word": "oi", "start": 2.1, "end": 2.5}]}
    segs, words, _ = parse_response(data, 0)
    assert [s.text for s in segs] == ["oi"]
    assert [w.w for w in words] == ["oi"]
