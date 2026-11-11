import re

import pytest

from corteviral.config import CaptionStyle
from corteviral.export import _approved, _meta, export_approved
from corteviral.metadata import PLATFORMS, normalize_hashtags, normalize_post
from corteviral.models import Segment, Transcript, Word
from corteviral.pipeline.analyze import MOMENTS, load_moments
from corteviral.pipeline.captions import build_ass
from corteviral.project import Project
from corteviral.review import ReviewError, apply_patch
from corteviral.utils import write_json_atomic


# --- I1: gancho no vídeo mesmo sem textos gerados -------------------------------------------
def test_render_hook_falls_back_without_meta(settings_tmp, monkeypatch):
    from corteviral.pipeline import render as mod
    seen = {}
    monkeypatch.setattr(mod, "build_ass", lambda words, style, **kw: seen.update(kw) or "x")
    monkeypatch.setattr(mod.ffmpeg, "run", lambda args: None)
    monkeypatch.setattr(mod, "resolve_layout", lambda p, s: __import__("corteviral.presets", fromlist=["x"]).LayoutPreset(layout="fallback"))
    p = Project.create(settings_tmp.paths.projects_dir, "x.mp4", "x")
    p.data.update(video_path="x.mp4", duration=60, layout_detect={"cams": [], "gameplay": None, "source": [1280, 720]})
    p.save()
    mod.render_clip(p, {"id": "m1", "start": 0, "end": 20, "hook": "Olha a perseguição", "text": "a b c"}, settings_tmp,
                    captions=False)
    assert seen["hook"] == "Olha a perseguição"


# --- I2: textos parciais completados com os textos simples ----------------------------------
def test_partial_meta_merged_with_fallback():
    m = {"id": "m1", "hook": "h", "reason": "motivo", "text": "fala",
         "meta": {"hook": "Meu gancho", "posts": {"tiktok": {"title": "", "description": "", "hashtags": []}}}}
    meta = _meta(m, "Flow", None)
    assert meta["hook"] == "Meu gancho"
    for p in PLATFORMS:
        assert meta["posts"][p]["title"].strip(" #shorts") and meta["posts"][p]["hashtags"]


# --- I3: reexportar não apaga nem sobrescreve exportação anterior ---------------------------
def _export_project(settings_tmp, sample_video):
    settings_tmp.render.presets_dir = settings_tmp.paths.data_dir / "presets"
    settings_tmp.render.encoder = "libx264"
    settings_tmp.paths.exports_dir = settings_tmp.paths.data_dir / "exports"
    settings_tmp.captions.enabled = False
    p = Project.create(settings_tmp.paths.projects_dir, str(sample_video), "Live")
    p.data.update(video_path=str(sample_video), duration=8.0,
                  layout_detect={"cams": [], "gameplay": None, "source": [1280, 720]})
    p.save()
    ms = [{"id": "m1", "start": 0.5, "end": 6.5, "score": 80, "reason": "r", "status": "approved", "hook": "olha"}]
    write_json_atomic(p.path(MOMENTS), {"moments": ms, "all": [dict(m) for m in ms]})
    return p


def test_failed_reexport_keeps_previous_files(settings_tmp, sample_video, monkeypatch):
    import corteviral.export as mod
    p = _export_project(settings_tmp, sample_video)
    first = export_approved(p, settings_tmp)["files"][0]
    monkeypatch.setattr(mod, "render_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg caiu")))
    res = export_approved(p, settings_tmp)
    assert res["errors"] and first["mp4"].exists() and first["txt"].exists()


def test_reexport_uses_new_name_instead_of_overwriting(settings_tmp, sample_video):
    p = _export_project(settings_tmp, sample_video)
    a = export_approved(p, settings_tmp)["files"][0]["mp4"]
    b = export_approved(p, settings_tmp)["files"][0]["mp4"]
    assert a != b and a.exists() and b.exists()


# --- I4: ajuste de corte mantém a legenda editada ------------------------------------------
def _review_project(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "x.mp4", "X")
    p.data.update(duration=300.0)
    p.save()
    words = [Word(w=f"w{i}", start=float(i), end=i + 0.5, p=1) for i in range(0, 300, 2)]
    Transcript(language="pt", duration=300, model="t", device="cpu", words=words,
               segments=[Segment(start=0, end=300, text="x")]).save(p.path("transcript.json"))
    m = {"id": "m1", "start": 100.0, "end": 110.0, "score": 80, "reason": "r", "text": "x"}
    write_json_atomic(p.path(MOMENTS), {"moments": [m], "all": [dict(m)]})
    return p


def test_trim_realigns_edited_caption(settings_tmp):
    p = _review_project(settings_tmp)
    apply_patch(p, "m1", {"caption_text": "um dois tres quatro cinco"}, settings_tmp)
    m = apply_patch(p, "m1", {"end": 120.0}, settings_tmp)
    cw = m["caption_words"]
    assert [w["w"] for w in cw] == ["um", "dois", "tres", "quatro", "cinco"]
    assert cw[0]["start"] >= 100.0 and cw[-1]["end"] <= 120.0 and cw[-1]["end"] > 110.0


def test_meta_posts_not_dict_is_422_error(settings_tmp):
    with pytest.raises(ReviewError):
        apply_patch(_review_project(settings_tmp), "m1", {"meta": {"posts": ["x"]}}, settings_tmp)


# --- M2: aprovados fora da seleção atual continuam exportáveis ------------------------------
def test_approved_outside_current_selection_still_exported(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "x.mp4", "X")
    a = {"id": "a", "start": 0, "end": 20, "score": 90, "status": "approved"}
    b = {"id": "b", "start": 30, "end": 50, "score": 70, "status": "approved"}
    write_json_atomic(p.path(MOMENTS), {"moments": [a], "all": [dict(a), dict(b)]})
    assert [m["id"] for m in _approved(p)] == ["a", "b"]


# --- M3 / M4 / M10: posições de gancho, crédito e legenda -----------------------------------
def test_credit_moves_out_of_the_caption_way():
    # a legenda fica onde o usuário pôs; o crédito "@canal" é que sai da frente
    for pos in (0.73, 0.75, 0.97):
        ass = build_ass([{"w": "oi", "start": 0, "end": 1}], CaptionStyle(position=pos, size=90),
                        credit="flow", duration=5)
        ev = next(l for l in ass.splitlines() if l.startswith("Dialogue: 0,"))
        bottom = int(re.search(r"\\pos\(\d+,(\d+)\)", ev).group(1))
        assert abs(bottom - min(pos * 1920 + 45, 1900)) < 2
        cr = next(l for l in ass.splitlines() if ",Credit," in l)
        cy = int(re.search(r"\\pos\(\d+,(\d+)\)", cr).group(1))
        assert cy <= bottom - 90 - 8 or cy - 36 >= bottom + 8, (pos, cy, bottom)  # sem sobrepor
        assert 36 <= cy <= 1900


def test_hook_top_follows_given_position():
    ass = build_ass([], CaptionStyle(), hook="oi", duration=5, hook_top=700)
    ev = next(l for l in ass.splitlines() if ",Hook," in l)
    assert re.search(r"\\pos\(\d+,700\)", ev)


def test_credit_newline_stripped():
    ass = build_ass([], CaptionStyle(), credit="flow\nDialogue: x", duration=5)
    assert sum(l.startswith("Dialogue:") for l in ass.splitlines()) == 1


def test_hook_top_for_layouts():
    from corteviral.config import RenderSettings
    from corteviral.pipeline.render import hook_top_for
    rs = RenderSettings()
    assert 582 < hook_top_for("split", True, rs) < 700          # logo abaixo da câmera (576 + 6)
    rs.facecam_ratio = 0.40
    assert hook_top_for("split", True, rs) > 768 + 6             # câmera maior → gancho desce junto
    assert 600 < hook_top_for("fallback", False, rs) < 760      # topo da faixa 16:9 centralizada
    assert hook_top_for("split", False, rs) == hook_top_for("fallback", False, rs)  # split sem câmera vira fallback


# --- M6 / M7: hashtags e título do Shorts ----------------------------------------------------
def test_hashtags_from_string_and_shorts_title_blank():
    assert normalize_hashtags("#gta6 #cortes, viral") == ["#gta6", "#cortes", "#viral"]
    p = normalize_post({"title": "", "description": "d", "hashtags": []}, "shorts", None)
    assert p["title"] == ""
    p2 = normalize_post({"title": "Fuga insana #Shorts", "description": "d", "hashtags": []}, "shorts", None)
    assert p2["title"].lower().count("#shorts") == 1
