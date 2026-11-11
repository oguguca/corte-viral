import pytest

from corteviral.config import Settings
from corteviral.llm import LLMError, LLMRateLimited
from corteviral.pipeline.candidates import Candidate
from corteviral.pipeline.scoring import score_candidates


def _cand(i, audio=50.0, reaction=50.0, chat=0.0, text="olha isso MEU DEUS kkkk"):
    return Candidate(id=f"m{i:05d}", peak=float(i * 100), start=float(i * 100 - 20), end=float(i * 100 + 10),
                     parts={"audio": audio, "reaction": reaction, "chat": chat}, text=text)


class FakeLLM:
    def __init__(self, scores=None, fail_after=None, exc=LLMRateLimited, garbage=False):
        self.calls = 0
        self.scores = scores or {}
        self.fail_after = fail_after
        self.exc = exc
        self.garbage = garbage

    def score(self, items, profile, language):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise self.exc("cota esgotada")
        out = [{"id": it["id"], "score": self.scores.get(it["id"], 70), "reason": "engraçado",
                "hook": "olha isso", "standalone": True} for it in items]
        if self.garbage:
            out[0]["score"] = 150          # fora da faixa
            out.append({"id": "inventado", "score": 99, "reason": "x", "hook": "", "standalone": True})
            del out[1]["reason"]           # incompleto
        return out


def test_no_llm_uses_signal_weights_and_builds_reason():
    s = Settings()
    moments, stats = score_candidates([_cand(1, audio=100, reaction=0), _cand(2, audio=0, reaction=100)], s, None)
    by = {m["id"]: m for m in moments}
    # sem chat: reação pesa mais que áudio
    assert by["m00002"]["score"] > by["m00001"]["score"]
    assert not by["m00001"]["ai"] and "áudio" in by["m00001"]["reason"]
    assert "reação" in by["m00002"]["reason"]
    assert stats["ai"] == 0


def test_llm_scores_merge_with_weights():
    s = Settings()
    llm = FakeLLM(scores={"m00001": 95, "m00002": 10})
    moments, stats = score_candidates([_cand(1), _cand(2)], s, llm)
    by = {m["id"]: m for m in moments}
    assert by["m00001"]["ai"] and by["m00001"]["parts"]["llm"] == 95
    assert by["m00001"]["score"] > by["m00002"]["score"]
    assert by["m00001"]["reason"] == "engraçado" and by["m00001"]["hook"] == "olha isso"
    assert stats["ai"] == 2


def test_llm_429_midway_keeps_remaining_with_signals():
    s = Settings()
    s.llm.batch_size = 2
    llm = FakeLLM(fail_after=1)
    moments, stats = score_candidates([_cand(i) for i in range(1, 7)], s, llm)
    assert len(moments) == 6
    assert sum(m["ai"] for m in moments) == 2
    assert stats["rate_limited"] is True and stats["no_ai"] == 4
    assert llm.calls == 2  # parou de chamar depois do 429


def test_transient_error_skips_only_that_batch():
    s = Settings()
    s.llm.batch_size = 2
    llm = FakeLLM(fail_after=0, exc=LLMError)
    moments, stats = score_candidates([_cand(i) for i in range(1, 5)], s, llm)
    assert llm.calls == 2 and stats["ai"] == 0 and not stats["rate_limited"]


def test_invalid_items_dropped_individually():
    s = Settings()
    moments, stats = score_candidates([_cand(1), _cand(2), _cand(3)], s, FakeLLM(garbage=True))
    by = {m["id"]: m for m in moments}
    assert not by["m00001"]["ai"]   # nota 150 descartada
    assert not by["m00002"]["ai"]   # sem reason
    assert by["m00003"]["ai"]
    assert "inventado" not in by


def test_missing_chat_redistributes_weights():
    s = Settings()
    a, _ = score_candidates([_cand(1, audio=80, reaction=80, chat=0)], s, None, has_chat=False)
    b, _ = score_candidates([_cand(1, audio=80, reaction=80, chat=0)], s, None, has_chat=True)
    assert a[0]["score"] == 80 and b[0]["score"] < 80


def test_gemini_request_shape(monkeypatch, tmp_path):
    import io
    import json

    from corteviral.llm import GeminiClient
    from corteviral.usage import UsageTracker
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
        payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(
            [{"id": "m1", "score": 77, "reason": "r", "hook": "h", "standalone": True}])}]}}],
                   "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30}}
        return Resp(json.dumps(payload).encode())

    monkeypatch.setattr("corteviral.llm.urllib.request.urlopen", fake_urlopen)
    tracker = UsageTracker(tmp_path / "u.json")
    c = GeminiClient(api_key="SECRET-KEY", model="gemini-3.5-flash-lite", timeout=30, tracker=tracker)
    out = c.score([{"id": "m1", "text": "oi", "signals": "áudio 10"}], "viral", "pt")
    assert out[0]["score"] == 77
    assert seen["url"].endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert "SECRET-KEY" not in seen["url"]
    assert seen["headers"].get("X-goog-api-key") == "SECRET-KEY"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseSchema" in seen["body"]["generationConfig"]
    t = tracker.today()
    assert t["requests"] == 1 and t["total_tokens"] == 150


def test_gemini_429_raises_rate_limited_and_records(monkeypatch, tmp_path):
    import urllib.error

    from corteviral.llm import GeminiClient
    from corteviral.usage import UsageTracker

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("corteviral.llm.urllib.request.urlopen", boom)
    tracker = UsageTracker(tmp_path / "u.json")
    c = GeminiClient(api_key="k", model="m", timeout=5, tracker=tracker)
    with pytest.raises(LLMRateLimited):
        c.score([{"id": "a", "text": "x", "signals": ""}], "viral", "pt")
    assert tracker.today()["rate_limited"] is True


def test_make_client_none_without_key(monkeypatch, tmp_path):
    from corteviral.llm import make_client
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    s = Settings()
    s.paths.data_dir = tmp_path
    assert make_client(s, env_file=tmp_path / "nao-existe.env") is None
    s.llm.provider = "none"
    assert make_client(s) is None


def test_gemini_retries_transient_503(monkeypatch, tmp_path):
    import io
    import json
    import urllib.error

    from corteviral.llm import GeminiClient
    attempts = []

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def flaky(req, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.HTTPError(req.full_url, 503, "Unavailable", {}, None)
        return Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(
            [{"id": "a", "score": 50, "reason": "r", "hook": "h", "standalone": True}])}]}}]}).encode())

    monkeypatch.setattr("corteviral.llm.urllib.request.urlopen", flaky)
    monkeypatch.setattr("corteviral.llm.time.sleep", lambda s: None)
    out = GeminiClient("k", "m", 5).score([{"id": "a", "text": "x", "signals": ""}], "viral", "pt")
    assert len(attempts) == 3 and out[0]["score"] == 50


def test_gemini_gives_up_after_retries(monkeypatch):
    import urllib.error

    from corteviral.llm import GeminiClient

    def down(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 503, "Unavailable", {}, None)

    monkeypatch.setattr("corteviral.llm.urllib.request.urlopen", down)
    monkeypatch.setattr("corteviral.llm.time.sleep", lambda s: None)
    with pytest.raises(LLMError, match="503"):
        GeminiClient("k", "m", 5).score([{"id": "a", "text": "x", "signals": ""}], "viral", "pt")
