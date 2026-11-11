import math
import struct
import wave

import numpy as np

from corteviral.models import Segment, Transcript, Word
from corteviral.pipeline.signals import (audio_loudness, compute_signals, reaction_curve,
                                         robust_z)
from corteviral.project import Project


def _write_wav(path, seconds_quiet=5, seconds_loud=1, rate=16000):
    t_q = np.zeros(seconds_quiet * rate)
    t_l = 0.8 * np.sin(2 * math.pi * 440 * np.arange(seconds_loud * rate) / rate)
    data = (np.concatenate([t_q + 0.001, t_l]) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def test_robust_z_flags_spike():
    values = [1.0] * 100
    values[50] = 10.0
    z = robust_z(values, window=60)
    assert z[50] > 3
    assert max(z[:45]) < 0.5 and all(v >= 0 for v in z)


def test_audio_loudness_one_value_per_second(tmp_path):
    wav = tmp_path / "a.wav"
    _write_wav(wav)
    db = audio_loudness(wav)
    assert len(db) == 6
    assert int(np.argmax(db)) == 5


def test_reaction_curve_counts_lexicon_at_word_time():
    words = [Word(w="kkkkkk", start=10.2, end=10.8, p=0.9), Word(w="normal", start=20.0, end=20.3, p=0.9),
             Word(w="MEU", start=30.5, end=30.7, p=0.9), Word(w="DEUS!", start=30.7, end=31.0, p=0.9)]
    curve = reaction_curve(words, duration=40)
    assert len(curve) == 40
    assert curve[10] > 0 and curve[30] > 0
    assert curve[20] == 0
    assert curve[30] > curve[10] * 0.5  # frase forte + caixa alta + "!"


def _project_with_audio(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "x.mp4", "x")
    _write_wav(p.path("audio.wav"))
    p.data["duration"] = 6.0
    p.data["ingest_params"] = {"source": "x.mp4"}
    p.save()
    Transcript(language="pt", duration=6.0, model="medium", device="cuda",
               words=[Word(w="nossa", start=5.1, end=5.5, p=0.9)],
               segments=[Segment(start=5.1, end=5.5, text="nossa")]).save(p.path("transcript.json"))
    return p


def test_chat_none_when_unavailable(settings_tmp, monkeypatch):
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat", lambda project, settings: None)
    s = compute_signals(_project_with_audio(settings_tmp), settings_tmp)
    assert s.chat is None and s.duration == 6 and len(s.audio) == 6


def test_signals_cached_by_params(settings_tmp, monkeypatch):
    calls = []
    monkeypatch.setattr("corteviral.pipeline.signals.fetch_chat",
                        lambda project, settings: calls.append(1) or None)
    p = _project_with_audio(settings_tmp)
    compute_signals(p, settings_tmp)
    compute_signals(p, settings_tmp)
    assert len(calls) == 1
    assert p.path("signals.json").exists()


def test_parse_youtube_live_chat(tmp_path):
    import json
    from corteviral.pipeline.chat import parse_chat_file
    f = tmp_path / "chat.live_chat.json"
    lines = [{"replayChatItemAction": {"videoOffsetTimeMsec": "1500", "actions": []}},
             {"replayChatItemAction": {"videoOffsetTimeMsec": "1900", "actions": []}},
             {"replayChatItemAction": {"videoOffsetTimeMsec": "4200", "actions": []}}]
    f.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
    assert parse_chat_file(f) == [1.5, 1.9, 4.2]


def test_parse_twitch_rechat(tmp_path):
    import json
    from corteviral.pipeline.chat import parse_chat_file, per_second
    f = tmp_path / "chat.rechat.json"
    f.write_text(json.dumps([{"content_offset_seconds": 2.5}, {"content_offset_seconds": 2.9},
                             {"content_offset_seconds": 7.0}]), encoding="utf-8")
    offsets = parse_chat_file(f)
    assert offsets == [2.5, 2.9, 7.0]
    assert per_second(offsets, duration=8) == [0, 0, 2, 0, 0, 0, 0, 1]


def test_corrupt_chat_file_returns_none(settings_tmp):
    from corteviral.pipeline.chat import fetch_chat
    p = Project.create(settings_tmp.paths.projects_dir, "https://youtu.be/q", "q")
    p.data.update(url="https://youtu.be/q", duration=100)
    p.save()
    p.path("chat.live_chat.json").write_text('[{"content_offset_seconds": 1}, {"trunc', encoding="utf-8")
    assert fetch_chat(p, settings_tmp) is None
