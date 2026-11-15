import pytest

from corteviral.pipeline.jumpcut import (
    keep_segments, out_duration, remap_words, select_expr, zoom_windows, parse_silences,
)


def test_keep_segments_cuts_long_silences_with_padding():
    # clipe de 100 a 110 s; silêncios (absolutos) de 102–103 e 106–106.2 (curto demais para cortar)
    segs = keep_segments(100.0, 110.0, [(102.0, 103.0), (106.0, 106.2)], pad=0.1, min_silence=0.35)
    assert segs == [(0.0, 2.1), (2.9, 10.0)]


def test_keep_segments_trims_leading_and_keeps_tail_short():
    # silêncio no começo some inteiro (gancho imediato); no fim fica só o respiro
    segs = keep_segments(0.0, 10.0, [(0.0, 1.5), (8.0, 10.0)], pad=0.1, min_silence=0.35, tail=0.3)
    assert segs == [(1.4, 8.3)]


def test_keep_segments_no_silence_keeps_all():
    assert keep_segments(5.0, 15.0, [], pad=0.1, min_silence=0.35) == [(0.0, 10.0)]


def test_keep_segments_never_returns_empty():
    assert keep_segments(0.0, 5.0, [(0.0, 5.0)], pad=0.1, min_silence=0.35) == [(0.0, 5.0)]


def test_out_duration():
    assert out_duration([(0.0, 2.1), (2.9, 10.0)]) == pytest.approx(9.2)


def test_remap_words_shifts_and_drops_cut_words():
    segs = [(0.0, 2.0), (3.0, 5.0)]
    words = [{"w": "a", "start": 0.5, "end": 0.9}, {"w": "b", "start": 2.2, "end": 2.6},
             {"w": "c", "start": 3.5, "end": 4.0}]
    assert remap_words(words, segs) == [{"w": "a", "start": 0.5, "end": 0.9}, {"w": "c", "start": 2.5, "end": 3.0}]


def test_select_expr():
    assert select_expr([(0.0, 2.1), (2.9, 10.0)]) == "between(t,0.000,2.100)+between(t,2.900,10.000)"


def test_zoom_windows_alternate_and_split_long_pieces():
    # saída: pedaço 0–2.1 (normal), 2.1–9.2 vira 2.1–5.6 (zoom) e 5.6–9.2 (normal), cada um ≤ 4 s
    w = zoom_windows([(0.0, 2.1), (2.9, 10.0)], every=3.5)
    assert w == [(2.1, 5.65)]


def test_zoom_windows_single_short_segment():
    assert zoom_windows([(0.0, 3.0)], every=3.5) == []


def test_parse_silences_from_ffmpeg_log():
    log = ("[silencedetect @ 0x1] silence_start: 1.25\n"
           "[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 1.25\n"
           "[silencedetect @ 0x1] silence_start: 7\n")
    assert parse_silences(log, offset=100.0, end=110.0) == [(101.25, 102.5), (107.0, 110.0)]


def test_remap_points_follows_cuts():
    from corteviral.pipeline.jumpcut import remap_points
    segs = [(0.0, 2.0), (3.0, 5.0)]
    assert remap_points([(0.5, 10), (2.5, 20), (3.5, 30)], segs) == [(0.5, 10), (2.5, 30)]
