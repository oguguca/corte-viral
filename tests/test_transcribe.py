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
