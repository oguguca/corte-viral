import json

import pytest
from pydantic import ValidationError

from corteviral.caption_style import load_style, realign, save_style
from corteviral.config import CaptionStyle


def test_style_override_file_wins_over_config(settings_tmp):
    settings_tmp.captions.size = 70
    assert load_style(settings_tmp).size == 70
    settings_tmp.paths.data_dir.mkdir(parents=True)
    (settings_tmp.paths.data_dir / "caption_style.json").write_text(json.dumps({"size": 90, "font": "Impact"}),
                                                                    encoding="utf-8")
    s = load_style(settings_tmp)
    assert s.size == 90 and s.font == "Impact" and s.highlight == "#FFE600"


def test_save_style_validates(settings_tmp):
    save_style(settings_tmp, CaptionStyle(highlight="#00FF00"))
    assert load_style(settings_tmp).highlight == "#00FF00"
    with pytest.raises(ValidationError):
        CaptionStyle(highlight="amarelo")


def W(w, s, e):
    return {"w": w, "start": s, "end": e}


ORIG = [W("olha", 0.0, 0.3), W("iso", 0.35, 0.6), W("aqui", 0.7, 1.0)]


def test_realign_same_count_keeps_times():
    out = realign(ORIG, "Olha isso aqui")
    assert [w["w"] for w in out] == ["Olha", "isso", "aqui"]
    assert [(w["start"], w["end"]) for w in out] == [(0.0, 0.3), (0.35, 0.6), (0.7, 1.0)]


def test_realign_different_count_distributes_monotonic():
    out = realign(ORIG, "olha só isso aqui mano")
    assert len(out) == 5 and out[0]["start"] == 0.0 and abs(out[-1]["end"] - 1.0) < 1e-6
    for a, b in zip(out, out[1:]):
        assert a["start"] < a["end"] <= b["start"] + 1e-9
    out2 = realign(ORIG, "olhaaqui")
    assert len(out2) == 1 and out2[0]["start"] == 0.0 and out2[0]["end"] == 1.0


def test_realign_empty_text_returns_empty():
    assert realign(ORIG, "   ") == []
    assert realign([], "algo") == []


def test_invalid_saved_style_falls_back_to_defaults(settings_tmp):
    settings_tmp.paths.data_dir.mkdir(parents=True)
    (settings_tmp.paths.data_dir / "caption_style.json").write_text(json.dumps({"size": 999, "lixo": 1}),
                                                                    encoding="utf-8")
    assert load_style(settings_tmp).size == settings_tmp.captions.size


def test_font_name_validated():
    with pytest.raises(ValidationError):
        CaptionStyle(font="Arial,Bold")
    with pytest.raises(ValidationError):
        CaptionStyle(font="Arial\nDialogue: x")
    assert CaptionStyle(font="Segoe UI Black").font == "Segoe UI Black"
