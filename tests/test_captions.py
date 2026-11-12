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
    assert ass_escape("a{b}c") == "a\\{b\\}c"


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


def test_build_ass_position_is_free_but_inside_the_frame():
    for pos in (0.70, 0.75, 0.95, 0.02, 0.0, 1.0):
        for size in (60, 90, 140):
            ass = build_ass([W("oi", 0.0, 0.3)], CaptionStyle(position=pos, size=size))
            assert r"\an2" in ass  # ancorada pela base: se quebrar linha, cresce para cima
            bottom = int(re.search(r"\\pos\(\d+,(\d+)\)", ass).group(1))
            assert bottom <= 1900 and bottom - size >= 20  # sem trava de zona segura, mas nunca sai do vídeo
    ass = build_ass([W("oi", 0.0, 0.3)], CaptionStyle(position=0.90, size=90))
    bottom = int(re.search(r"\\pos\(\d+,(\d+)\)", ass).group(1))
    assert abs(bottom - (0.90 * 1920 + 45)) < 2  # embaixo de verdade, onde o usuário pediu
    ass = build_ass([W("oi", 0.0, 0.3)], CaptionStyle(position=0.70, size=90))
    bottom = int(re.search(r"\\pos\(\d+,(\d+)\)", ass).group(1))
    assert abs(bottom - (0.70 * 1920 + 45)) < 2  # centro da linha na altura escolhida


def test_long_word_is_shrunk_to_fit_width():
    ass = build_ass([W("inconstitucionalissimamente", 0.0, 1.0)], CaptionStyle(size=90))
    m = re.search(r"\\fscx(\d+)\\fscy(\d+)", ass.splitlines()[-1])
    assert m and int(m.group(1)) < 100 and m.group(1) == m.group(2)
    short = build_ass([W("oi", 0.0, 1.0)], CaptionStyle(size=90, pop=False))
    assert r"\fscx" not in short.splitlines()[-1]


def test_backslash_shown_without_breaking_line():
    out = ass_escape("a\\Nb")
    assert "\\N" not in out and "\\" not in out and out.startswith("a") and out.endswith("Nb")


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


def test_hook_event_first_3s_boxed_in_safe_zone():
    ass = build_ass([], CaptionStyle(), hook="Ele não esperava por isso", duration=20.0, layout="split")
    ev = [l for l in ass.splitlines() if l.startswith("Dialogue:") and ",Hook," in l]
    assert len(ev) == 1 and ev[0].split(",")[1] == "0:00:00.00" and ev[0].split(",")[2] == "0:00:03.00"
    assert "ELE NÃO ESPERAVA POR ISSO" in ev[0] or "Ele não esperava por isso" in ev[0]
    style_line = next(l for l in ass.splitlines() if l.startswith("Style: Hook,"))
    assert style_line.split(",")[15] == "3"  # BorderStyle 3 = caixa opaca atrás do texto
    y = int(re.search(r"\\pos\(\d+,(\d+)\)", ev[0]).group(1))
    assert 582 <= y <= 800  # topo da área da gameplay (abaixo da câmera) e dentro da zona segura


def test_hook_escaped_and_shrunk():
    ass = build_ass([], CaptionStyle(), hook=r"{\b1}" + "palavra " * 12, duration=10.0)
    ev = next(l for l in ass.splitlines() if ",Hook," in l)
    assert r"\{" in ev and r"{\b1}" not in ev  # chave escapada: não vira comando ASS
    assert re.search(r"\\fscx\d+", ev)  # longo demais para 2 linhas: encolhe


def test_credit_event_whole_clip():
    ass = build_ass([], CaptionStyle(), credit="flowgames", duration=42.5)
    ev = next(l for l in ass.splitlines() if ",Credit," in l)
    assert ev.split(",")[1] == "0:00:00.00" and ev.split(",")[2] == "0:00:42.50" and "@flowgames" in ev


def test_ass_without_words_but_with_hook_still_has_events():
    assert "Dialogue:" in build_ass([], CaptionStyle(), hook="oi", duration=5.0)
    assert "Dialogue:" not in build_ass([], CaptionStyle(), hook=None, credit=None, duration=5.0)


def test_clip_caption_position_overrides_style():
    from corteviral.pipeline.render import caption_style_for
    style = CaptionStyle(position=0.70)
    assert caption_style_for({"caption_pos": 0.9}, style).position == 0.9
    assert caption_style_for({}, style).position == 0.70
    assert style.position == 0.70  # o estilo global não muda


def test_caption_events_match_the_ass():
    from corteviral.pipeline.captions import caption_events
    words = [{"w": "olha", "start": 0.0, "end": 0.4}, {"w": "isso", "start": 0.4, "end": 0.8},
             {"w": "mano", "start": 3.0, "end": 3.5}]
    ev = caption_events(words, CaptionStyle(max_words=3))
    assert [(e["words"], e["hl"]) for e in ev] == [(["OLHA", "ISSO"], 0), (["OLHA", "ISSO"], 1), (["MANO"], 0)]
    assert ev[0]["start"] == 0.0 and ev[1]["end"] == 0.8 + 0.3  # último do grupo fica HOLD s na tela
    assert len(ev) == build_ass(words, CaptionStyle(max_words=3)).count("Default,,")
