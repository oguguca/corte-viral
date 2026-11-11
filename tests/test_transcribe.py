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
