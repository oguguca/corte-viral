import logging

import pytest

from corteviral.models import Transcript
from corteviral.pipeline.transcribe import transcribe
from tests.conftest import FakeModel


def test_transcript_has_word_timestamps(project_with_audio, settings_tmp):
    t = transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.ok)
    assert t.language == "pt"
    assert [w.w for w in t.words] == ["Olha", "isso", "aqui!"]
    assert t.words[0].start == 0.5 and t.words[2].end == 1.9
    assert t.segments[0].text == "Olha isso aqui!"
    assert project_with_audio.path("transcript.json").exists()
    assert Transcript.load(project_with_audio.path("transcript.json")) == t


def test_words_are_monotonic_and_rounded(project_with_audio, settings_tmp):
    t = transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.ok)
    assert all(a.start <= b.start for a, b in zip(t.words, t.words[1:]))
    assert all(round(w.start, 3) == w.start for w in t.words)


def test_cuda_failure_falls_back_to_cpu_small(project_with_audio, settings_tmp, caplog):
    created = []
    with caplog.at_level(logging.WARNING):
        t = transcribe(project_with_audio, settings_tmp,
                       model_factory=FakeModel.cuda_fails_on_first_inference(created))
    assert created == [("medium", "cuda"), ("small", "cpu")]
    assert "CPU" in caplog.text and len(t.words) == 3


def test_cached_second_run_does_not_load_model(project_with_audio, settings_tmp):
    transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.ok)
    t = transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.explode)
    assert len(t.words) == 3


def test_changed_model_invalidates_cache(project_with_audio, settings_tmp):
    transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.ok)
    settings_tmp.whisper.model = "small"
    with pytest.raises(AssertionError, match="não deveria"):
        transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.explode)


@pytest.mark.slow
def test_real_model_on_sample(sample_video, settings_tmp):
    from corteviral.pipeline.ingest import ingest
    settings_tmp.whisper.model = "tiny"
    p = ingest(str(sample_video), settings_tmp)
    t = transcribe(p, settings_tmp)
    assert t.duration > 0


def test_fallback_result_is_not_cached_as_gpu_result(project_with_audio, settings_tmp):
    transcribe(project_with_audio, settings_tmp,
               model_factory=FakeModel.cuda_fails_on_first_inference([]))
    t = Transcript.load(project_with_audio.path("transcript.json"))
    assert (t.model, t.device) == ("small", "cpu")
    # GPU voltou: a próxima execução deve tentar de novo com o modelo pedido
    created = []

    def factory(model, device="cuda", **kw):
        created.append((model, device))
        return FakeModel.ok(model, device=device, **kw)

    t2 = transcribe(project_with_audio, settings_tmp, model_factory=factory)
    assert created == [("medium", "cuda")]
    assert (t2.model, t2.device) == ("medium", "cuda")


def test_gpu_failure_midway_resumes_on_cpu_from_checkpoint(project_with_audio, settings_tmp, monkeypatch):
    from types import SimpleNamespace
    trimmed = []
    monkeypatch.setattr("corteviral.ffmpeg.run", lambda args: trimmed.append(args))

    def seg(s, e, text):
        return SimpleNamespace(start=s, end=e, text=text,
                               words=[SimpleNamespace(word=text, start=s, end=e, probability=0.9)])

    def factory(model, device="cuda", **kw):
        def transcribe_(audio, **opts):
            def gen():
                if device == "cuda":
                    yield seg(0.0, 1.0, "um")
                    yield seg(1.0, 2.0, "dois")
                    raise RuntimeError("CUDA failed with error out of memory")
                # na CPU, o áudio recebido já começa no ponto de retomada (tempos relativos)
                yield seg(0.5, 1.5, "tres")
            return gen(), SimpleNamespace(language="pt", duration=8.0)
        return SimpleNamespace(transcribe=transcribe_)

    t = transcribe(project_with_audio, settings_tmp, model_factory=factory)
    assert [w.w for w in t.words] == ["um", "dois", "tres"]
    assert t.words[2].start == 2.5  # 2.0 (retomada) + 0.5
    assert any("-ss" in a for a in trimmed)
    assert not project_with_audio.path("transcript.partial.json").exists()


def test_interrupted_run_resumes_from_checkpoint(project_with_audio, settings_tmp, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("corteviral.ffmpeg.run", lambda args: None)
    monkeypatch.setattr("corteviral.pipeline.transcribe.CHECKPOINT_EVERY", 1)

    def seg(s, e, text):
        return SimpleNamespace(start=s, end=e, text=text,
                               words=[SimpleNamespace(word=text, start=s, end=e, probability=0.9)])

    def crashing(model, device="cuda", **kw):
        def tr(audio, **opts):
            def gen():
                yield seg(0.0, 1.0, "um")
                raise KeyboardInterrupt
            return gen(), SimpleNamespace(language="pt", duration=8.0)
        return SimpleNamespace(transcribe=tr)

    with pytest.raises(KeyboardInterrupt):
        transcribe(project_with_audio, settings_tmp, model_factory=crashing)
    assert project_with_audio.path("transcript.partial.json").exists()

    def rest(model, device="cuda", **kw):
        def tr(audio, **opts):
            return iter([seg(0.2, 0.9, "dois")]), SimpleNamespace(language="pt", duration=8.0)
        return SimpleNamespace(transcribe=tr)

    t = transcribe(project_with_audio, settings_tmp, model_factory=rest)
    assert [w.w for w in t.words] == ["um", "dois"] and t.words[1].start == 1.2


def test_gpu_preflight_failure_goes_straight_to_cpu(project_with_audio, settings_tmp, monkeypatch):
    created = []

    def factory(model, device="cuda", **kw):
        created.append((model, device))
        return FakeModel.ok(model, device=device, **kw)

    monkeypatch.setattr("corteviral.pipeline.transcribe.WhisperModel", factory)
    monkeypatch.setattr("corteviral.pipeline.transcribe._gpu_preflight", lambda settings: False)
    t = transcribe(project_with_audio, settings_tmp)
    assert created == [("small", "cpu")] and t.device == "cpu"


def test_gpu_still_broken_reuses_existing_cpu_transcript(project_with_audio, settings_tmp):
    transcribe(project_with_audio, settings_tmp,
               model_factory=FakeModel.cuda_fails_on_first_inference([]))
    created = []
    t = transcribe(project_with_audio, settings_tmp,
                   model_factory=FakeModel.cuda_fails_on_first_inference(created))
    assert created == [("medium", "cuda")]  # tentou a GPU, não refez na CPU
    assert t.device == "cpu" and len(t.words) == 3


def test_transcribe_invalidates_downstream_stages(project_with_audio, settings_tmp):
    project_with_audio.mark_stage("signals", {"x": 1})
    project_with_audio.mark_stage("scoring", {"y": 1})
    transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.ok)
    assert "signals" not in project_with_audio.data["stages"]
    assert "scoring" not in project_with_audio.data["stages"]


# --- Groq (transcrição na nuvem) ---

from corteviral.pipeline.groq_transcribe import GroqAuthError, GroqQuotaExceeded  # noqa: E402


@pytest.fixture
def groq_setup(project_with_audio, settings_tmp, tmp_path, monkeypatch):
    env = tmp_path / "groq.env"
    env.write_text("GROQ_API_KEY=k\n", encoding="utf-8")
    settings_tmp.whisper.engine = "groq"
    settings_tmp.whisper.groq_chunk_seconds = 60
    project_with_audio.data["duration"] = 130.0
    project_with_audio.save()
    monkeypatch.setattr("corteviral.pipeline.transcribe.encode_chunk",
                        lambda audio, off, length, dest: dest.write_bytes(b"OggS"))
    monkeypatch.setattr("corteviral.ffmpeg.run", lambda args: None)
    return project_with_audio, settings_tmp, env


def _groq_ok(calls=None):
    def http(path, key, model, language):
        if calls is not None:
            calls.append((path, key, model, language))
        return {"language": "portuguese", "segments": [{"start": 1.0, "end": 1.5, "text": " oi"}],
                "words": [{"word": " oi", "start": 1.0, "end": 1.5}]}
    return http


def _cpu_factory(created):
    from types import SimpleNamespace

    def factory(model, device="cuda", **kw):
        created.append((model, device))

        def tr(audio, **opts):
            s = SimpleNamespace(start=0.5, end=1.5, text="cpu",
                                words=[SimpleNamespace(word="cpu", start=0.5, end=1.5, probability=0.9)])
            return iter([s]), SimpleNamespace(language="pt", duration=130.0)
        return SimpleNamespace(transcribe=tr)
    return factory


def test_groq_engine_produces_words_with_offsets(groq_setup):
    p, s, env = groq_setup
    calls, msgs = [], []
    t = transcribe(p, s, on_progress=lambda f, m: msgs.append(m), model_factory=FakeModel.explode,
                   groq_http=_groq_ok(calls), env_file=env)
    assert [w.start for w in t.words] == [1.0, 61.0, 121.0]
    assert [seg.start for seg in t.segments] == [1.0, 61.0, 121.0]
    assert (t.model, t.device, t.language) == ("whisper-large-v3-turbo", "groq", "pt")
    assert len(calls) == 3 and calls[0][1] == "k"
    assert "transcrevendo na nuvem (Groq)" in msgs
    assert not p.path("groq_chunk.ogg").exists()
    assert not p.path("transcript.partial.json").exists()


def test_groq_quota_falls_back_to_cpu_from_checkpoint(groq_setup):
    p, s, env = groq_setup
    n, created, msgs = [], [], []

    def http(path, key, model, language):
        n.append(1)
        if len(n) == 2:
            raise GroqQuotaExceeded("429")
        return _groq_ok()(path, key, model, language)

    t = transcribe(p, s, on_progress=lambda f, m: msgs.append(m), model_factory=_cpu_factory(created),
                   groq_http=http, env_file=env)
    assert created == [("small", "cpu")]
    assert [(w.w, w.start) for w in t.words] == [("oi", 1.0), ("cpu", 60.5)]
    assert "Cota do Groq esgotada, usando CPU" in msgs
    assert t.device == "cpu"


def test_groq_auth_error_falls_back_to_cpu(groq_setup):
    p, s, env = groq_setup
    created, msgs = [], []

    def http(*a):
        raise GroqAuthError("401")

    t = transcribe(p, s, on_progress=lambda f, m: msgs.append(m), model_factory=_cpu_factory(created),
                   groq_http=http, env_file=env)
    assert created == [("small", "cpu")]
    assert [w.w for w in t.words] == ["cpu"]
    assert "Groq indisponível, usando CPU" in msgs


def test_groq_resume_skips_done_chunks(groq_setup):
    p, s, env = groq_setup
    n = []

    def crashing(path, key, model, language):
        n.append(1)
        if len(n) == 2:
            raise KeyboardInterrupt
        return _groq_ok()(path, key, model, language)

    with pytest.raises(KeyboardInterrupt):
        transcribe(p, s, model_factory=FakeModel.explode, groq_http=crashing, env_file=env)
    calls = []
    t = transcribe(p, s, model_factory=FakeModel.explode, groq_http=_groq_ok(calls), env_file=env)
    assert len(calls) == 2
    assert [w.start for w in t.words] == [1.0, 61.0, 121.0]


def test_auto_without_key_uses_local(project_with_audio, settings_tmp, tmp_path):
    settings_tmp.whisper.engine = "auto"
    settings_tmp.whisper.device = "cpu"

    def http(*a):
        raise AssertionError("não deveria chamar o Groq")

    t = transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.ok, groq_http=http,
                   env_file=tmp_path / "vazio.env")
    assert t.device == "cpu"


def test_auto_with_key_and_cpu_device_uses_groq(groq_setup):
    p, s, env = groq_setup
    s.whisper.engine = "auto"
    s.whisper.device = "cpu"
    t = transcribe(p, s, model_factory=FakeModel.explode, groq_http=_groq_ok(), env_file=env)
    assert t.device == "groq"


def test_auto_with_key_and_failed_gpu_uses_groq(groq_setup, monkeypatch):
    p, s, env = groq_setup
    s.whisper.engine = "auto"
    monkeypatch.setattr("corteviral.pipeline.transcribe.WhisperModel", FakeModel.explode)
    monkeypatch.setattr("corteviral.pipeline.transcribe._gpu_preflight", lambda settings: False)
    t = transcribe(p, s, groq_http=_groq_ok(), env_file=env)
    assert t.device == "groq"


def test_groq_result_is_cached(groq_setup):
    p, s, env = groq_setup
    t1 = transcribe(p, s, model_factory=FakeModel.explode, groq_http=_groq_ok(), env_file=env)

    def http(*a):
        raise AssertionError("não deveria chamar o Groq de novo")

    t2 = transcribe(p, s, model_factory=FakeModel.explode, groq_http=http, env_file=env)
    assert t2 == t1


def test_groq_unknown_duration_probes_audio(groq_setup, monkeypatch):
    p, s, env = groq_setup
    p.data["duration"] = 0
    p.save()
    monkeypatch.setattr("corteviral.pipeline.transcribe.ffmpeg.probe_duration", lambda path: 130.0)
    t = transcribe(p, s, model_factory=FakeModel.explode, groq_http=_groq_ok(), env_file=env)
    assert t.device == "groq" and len(t.words) == 3


def test_groq_duration_still_zero_uses_local(groq_setup, monkeypatch):
    p, s, env = groq_setup
    p.data["duration"] = 0
    p.save()
    s.whisper.device = "cpu"
    monkeypatch.setattr("corteviral.pipeline.transcribe.ffmpeg.probe_duration", lambda path: 0.0)

    def http(*a):
        raise AssertionError("não deveria chamar o Groq")

    t = transcribe(p, s, model_factory=FakeModel.ok, groq_http=http, env_file=env)
    assert t.device == "cpu"
