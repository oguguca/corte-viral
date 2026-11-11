from corteviral.models import Segment, Transcript, Word
from corteviral.pipeline.boundaries import select, snap_bounds


def _sentences(duration=300.0):
    """Frases de ~6 s: 'palavra palavra ... fim.' com pausa de 0.5 s entre frases."""
    words, t = [], 0.0
    while t < duration - 6:
        for k in range(10):
            last = k == 9
            words.append(Word(w="fim." if last else "fala", start=round(t, 2), end=round(t + 0.45, 2), p=0.9))
            t += 0.55
        t += 0.5  # pausa entre frases
    return Transcript(language="pt", duration=duration, words=words,
                      segments=[Segment(start=0, end=words[-1].end, text="x")])


def test_start_snaps_back_to_pause_before():
    tr = _sentences()
    start, _ = snap_bounds(103.0, 140.0, tr, 15, 60)
    assert start <= 103.0
    # o início escolhido é o começo de uma palavra que vem depois de uma pausa
    idx = next(i for i, w in enumerate(tr.words) if w.start == start)
    assert idx == 0 or tr.words[idx].start - tr.words[idx - 1].end >= 0.3


def test_end_snaps_forward_to_sentence_end():
    tr = _sentences()
    _, end = snap_bounds(100.0, 131.0, tr, 15, 60)
    assert end >= 131.0
    assert any(w.end == end and w.w.endswith(".") for w in tr.words)


def test_duration_clamped_to_profile():
    tr = _sentences(600.0)
    s, e = snap_bounds(100.0, 105.0, tr, 15, 60)
    assert 15 <= e - s <= 60
    s, e = snap_bounds(100.0, 300.0, tr, 15, 60)
    assert 15 <= e - s <= 60
    s, e = snap_bounds(100.0, 130.0, tr, 61, 120)
    assert 61 <= e - s <= 120


def test_short_vod_returns_what_fits():
    tr = _sentences(10.0)
    s, e = snap_bounds(2.0, 8.0, tr, 15, 60)
    assert s == 0.0 and e <= 10.0 and e > s


def test_select_drops_overlapping_lower_score():
    ms = [{"id": "a", "start": 0, "end": 30, "score": 90},
          {"id": "b", "start": 10, "end": 40, "score": 80},   # 67 % sobreposto com a
          {"id": "c", "start": 100, "end": 130, "score": 70},
          {"id": "d", "start": 25, "end": 55, "score": 60}]   # 17 % com a, 50 % com b (b caiu)
    out = select(ms, n=10)
    assert [m["id"] for m in out] == ["a", "c", "d"]
    assert [m["id"] for m in select(ms, n=2)] == ["a", "c"]


def test_select_with_priority_ranks_watched_first():
    ms = [{"id": "w", "start": 0, "end": 20, "score": 48, "ai": "video"},
          {"id": "u", "start": 100, "end": 120, "score": 55, "ai": True}]
    out = select(ms, n=2, priority=lambda m: m.get("ai") == "video")
    assert [m["id"] for m in out] == ["w", "u"]
