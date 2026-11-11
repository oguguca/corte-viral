from corteviral.config import Settings
from corteviral.models import Segment, Transcript, Word
from corteviral.pipeline.candidates import find_candidates
from corteviral.pipeline.signals import Signals


def _talk(start, end, step=0.5, word="fala"):
    words, t = [], start
    while t < end:
        words.append(Word(w=word, start=round(t, 2), end=round(t + 0.3, 2), p=0.9))
        t += step
    return words


def _transcript(words, duration):
    return Transcript(language="pt", duration=duration, words=words,
                      segments=[Segment(start=words[0].start, end=words[-1].end, text="x")] if words else [])


def _signals(duration, audio_peaks=(), reaction_peaks=(), chat=None):
    audio = [0.0] * duration
    reaction = [0.0] * duration
    for p in audio_peaks:
        audio[p] = 8.0
    for p in reaction_peaks:
        reaction[p] = 1.5
    return Signals(duration=duration, audio=audio, reaction=reaction, speech=[0.0] * duration, chat=chat)


def test_peak_becomes_candidate_around_it():
    s = Settings()
    cands = find_candidates(_signals(300, audio_peaks=[150]), _transcript(_talk(0, 300), 300), s)
    top = cands[0]
    assert top.start <= 150 <= top.end
    assert top.parts["audio"] > 50 and top.parts["chat"] == 0


def test_peaks_closer_than_min_distance_merge():
    s = Settings()
    cands = find_candidates(_signals(300, audio_peaks=[150, 158]), _transcript(_talk(0, 300), 300), s)
    peaks = [c.peak for c in cands if 130 <= c.peak <= 170]
    assert len(peaks) == 1


def test_window_without_speech_is_dropped():
    s = Settings()
    words = _talk(200, 290)  # silêncio até 200 s
    cands = find_candidates(_signals(300, audio_peaks=[60, 250]), _transcript(words, 300), s)
    assert all(not (c.start <= 60 <= c.end) for c in cands)
    assert any(c.start <= 250 <= c.end for c in cands)


def test_respects_max_candidates():
    s = Settings()
    s.analysis.max_candidates = 5
    peaks = list(range(30, 3000, 40))
    cands = find_candidates(_signals(3000, audio_peaks=peaks), _transcript(_talk(0, 3000), 3000), s)
    assert len(cands) == 5


def test_text_is_words_inside_window():
    s = Settings()
    words = _talk(0, 140) + [Word(w="MEU", start=150.0, end=150.2, p=1), Word(w="DEUS", start=150.2, end=150.5, p=1)] \
        + _talk(151, 300)
    cands = find_candidates(_signals(300, reaction_peaks=[150]), _transcript(words, 300), s)
    top = next(c for c in cands if c.start <= 150 <= c.end)
    assert "MEU DEUS" in top.text
    assert top.parts["reaction"] > 50


def test_monetizacao_profile_uses_longer_windows():
    s = Settings()
    s.analysis.profile = "monetizacao"
    cands = find_candidates(_signals(600, audio_peaks=[300]), _transcript(_talk(0, 600), 600), s)
    top = next(c for c in cands if c.start <= 300 <= c.end)
    assert top.end - top.start >= 61
