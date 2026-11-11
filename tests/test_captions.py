import re

from corteviral.config import CaptionStyle
from corteviral.pipeline.captions import (ass_color, ass_escape, ass_time, build_ass, chunk_words,
                                          clip_words)


def W(w, s, e):
    return {"w": w, "start": s, "end": e}


def test_chunk_respects_max_words_and_pauses():
    words = [W("um", 0.0, 0.2), W("dois", 0.25, 0.4), W("três", 0.45, 0.6), W("quatro", 0.65, 0.8),
             W("cinco", 2.0, 2.2)]  # pausa longa antes de "cinco"
    chunks = chunk_words(words, max_words=3, max_chars=20)
    assert [[w["w"] for w in c] for c in chunks] == [["um", "dois", "três"], ["quatro"], ["cinco"]]


def test_chunk_breaks_on_sentence_end():
    words = [W("Olha", 0.0, 0.2), W("isso!", 0.25, 0.5), W("Meu", 0.55, 0.7), W("deus", 0.72, 0.9)]
    assert [[w["w"] for w in c] for c in chunk_words(words, 3, 20)] == [["Olha", "isso!"], ["Meu", "deus"]]


def test_long_word_alone():
    words = [W("oi", 0.0, 0.2), W("inconstitucionalissimamente", 0.25, 1.0), W("tchau", 1.05, 1.2)]
    chunks = chunk_words(words, 3, 20)
    assert ["inconstitucionalissimamente"] in [[w["w"] for w in c] for c in chunks]


def test_ass_time_format():
    assert ass_time(3725.456) == "1:02:05.46"
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(-1) == "0:00:00.00"


def test_ass_color_bgr():
    assert ass_color("#FFE600") == "&H0000E6FF"
    assert ass_color("#000000") == "&H00000000"


def test_ass_escape_braces_and_backslash():
    assert ass_escape("a{b}c\\d") == "a\\{b\\}c\\\\d"


def test_build_ass_one_event_per_word_with_highlight():
    style = CaptionStyle(uppercase=False)
    ass = build_ass([W("olha", 0.0, 0.3), W("isso", 0.35, 0.6), W("aqui", 0.65, 0.9)], style)
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == 3
    hl = ass_color(style.highlight)
    for i, ev in enumerate(events):
        text = ev.split(",", 9)[9]
        assert text.count(hl) == 1  # só a palavra ativa leva a cor de destaque
        active = ["olha", "isso", "aqui"][i]
        assert re.search(re.escape(hl) + r"[^}]*}" + active, text)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


def test_build_ass_position_clamped_to_safe_zone():
    for pos, ok in ((0.70, True), (0.95, False), (0.02, False)):
        ass = build_ass([W("oi", 0.0, 0.3)], CaptionStyle(position=pos))
        y = int(re.search(r"\\pos\(\d+,(\d+)\)", ass).group(1))
        assert 210 <= y <= 1440
        if ok:
            assert abs(y - 0.70 * 1920) < 2


def test_build_ass_uppercase_option():
    assert "OLHA" in build_ass([W("olha", 0, 0.3)], CaptionStyle(uppercase=True))
    assert "olha" in build_ass([W("olha", 0, 0.3)], CaptionStyle(uppercase=False))


def test_build_ass_empty_has_no_events():
    assert "Dialogue:" not in build_ass([], CaptionStyle())


def test_clip_words_relative_times():
    from corteviral.models import Word
    words = [Word(w="a", start=9.0, end=9.5, p=1), Word(w="b", start=10.2, end=10.6, p=1),
             Word(w="c", start=15.0, end=15.3, p=1), Word(w="d", start=21.0, end=21.2, p=1)]
    out = clip_words(words, 10.0, 20.0)
    assert [w["w"] for w in out] == ["b", "c"]
    assert out[0]["start"] == 0.2 and out[1]["end"] == 5.3
