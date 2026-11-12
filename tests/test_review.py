import pytest
from fastapi.testclient import TestClient

from corteviral.models import Segment, Transcript, Word
from corteviral.pipeline.analyze import MOMENTS, load_moments
from corteviral.project import Project
from corteviral.review import ReviewError, apply_patch
from corteviral.server.app import create_app
from corteviral.utils import write_json_atomic


def _project(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "x.mp4", "X")
    p.data.update(duration=300.0, streamer="Flow")
    p.save()
    words = [Word(w=f"w{i}", start=float(i), end=i + 0.5, p=1) for i in range(0, 300, 2)]
    Transcript(language="pt", duration=300, model="t", device="cpu", words=words,
               segments=[Segment(start=0, end=300, text="x")]).save(p.path("transcript.json"))
    m = {"id": "m1", "start": 100.0, "end": 130.0, "score": 80, "reason": "r", "ai": True, "text": "x"}
    write_json_atomic(p.path(MOMENTS), {"moments": [m], "all": [dict(m)]})
    return p


def test_status_values(settings_tmp):
    p = _project(settings_tmp)
    apply_patch(p, "m1", {"status": "approved"}, settings_tmp)
    d = load_moments(p)
    assert d["moments"][0]["status"] == "approved" and d["all"][0]["status"] == "approved"
    with pytest.raises(ReviewError):
        apply_patch(p, "m1", {"status": "talvez"}, settings_tmp)


def test_trim_valid_recomputes_text(settings_tmp):
    p = _project(settings_tmp)
    m = apply_patch(p, "m1", {"start": 110.0, "end": 120.0}, settings_tmp)
    assert (m["start"], m["end"]) == (110.0, 120.0)
    assert m["text"].split() == [f"w{i}" for i in range(110, 120, 2)]


@pytest.mark.parametrize("start,end", [(130, 120), (-5, 20), (290, 310), (100, 102), (0, 600)])
def test_trim_invalid_rejected(settings_tmp, start, end):
    p = _project(settings_tmp)
    with pytest.raises(ReviewError):
        apply_patch(p, "m1", {"start": start, "end": end}, settings_tmp)
    assert load_moments(p)["moments"][0]["start"] == 100.0  # nada salvo


def test_meta_edit_normalized(settings_tmp):
    p = _project(settings_tmp)
    meta = {"hook": "  Olha isso  ", "posts": {"tiktok": {"title": "Título", "description": "d", "hashtags": ["gta6", "gta6"]}}}
    m = apply_patch(p, "m1", {"meta": meta}, settings_tmp)
    assert m["meta"]["hook"] == "Olha isso"
    assert m["meta"]["posts"]["tiktok"]["hashtags"] == ["#gta6"]


def test_unknown_moment(settings_tmp):
    with pytest.raises(KeyError):
        apply_patch(_project(settings_tmp), "zz", {"status": "approved"}, settings_tmp)


def test_api_patch_returns_422_on_invalid_trim(settings_tmp):
    p = _project(settings_tmp)
    c = TestClient(create_app(settings_tmp))
    r = c.put(f"/api/projects/{p.dir.name}/moments/m1", json={"start": 120, "end": 110})
    assert r.status_code == 422 and "início" in r.json()["detail"]
    r = c.put(f"/api/projects/{p.dir.name}/moments/m1", json={"status": "approved", "hook_on": False})
    assert r.status_code == 200 and r.json()["moment"]["hook_on"] is False
    assert c.put(f"/api/projects/{p.dir.name}/moments/zz", json={"status": "approved"}).status_code == 404


def test_caption_position_per_clip(settings_tmp):
    p = _project(settings_tmp)
    apply_patch(p, "m1", {"caption_pos": 0.9}, settings_tmp)
    assert load_moments(p)["moments"][0]["caption_pos"] == 0.9
    with pytest.raises(ReviewError):
        apply_patch(p, "m1", {"caption_pos": 1.5}, settings_tmp)
    apply_patch(p, "m1", {"reset_caption_pos": True}, settings_tmp)  # volta ao padrão de Ajustes
    assert "caption_pos" not in load_moments(p)["moments"][0]


def test_blank_caption_text_restores_original(settings_tmp):
    p = _project(settings_tmp)
    apply_patch(p, "m1", {"caption_text": "oi"}, settings_tmp)
    assert load_moments(p)["moments"][0]["caption_words"]
    apply_patch(p, "m1", {"caption_text": "   "}, settings_tmp)  # caixa vazia = volta ao texto original
    assert "caption_words" not in load_moments(p)["moments"][0]


def test_live_preview_spec_follows_clip_settings(settings_tmp):
    p = _project(settings_tmp)
    p.data["layout_detect"] = {"cams": [{"rect": [0.7, 0.05, 0.25, 0.3]}], "gameplay": None, "source": [1280, 720]}
    p.save()
    apply_patch(p, "m1", {"caption_pos": 0.4, "meta": {"hook": "Olha isso"}}, settings_tmp)
    c = TestClient(create_app(settings_tmp))
    s = c.get(f"/api/projects/{p.dir.name}/moments/m1/preview").json()
    assert s["layout"] == "split" and s["facecam"] == [0.7, 0.05, 0.25, 0.3]
    assert s["style"]["position"] == 0.4 and s["hook"] == "Olha isso" and s["hook_top"] > 0
    assert s["events"][0]["start"] == 0.0 and s["events"][0]["words"][0] == "W100"  # tempo relativo ao clipe
    assert s["duration"] == 30.0 and 0 < s["render"]["facecam_ratio"] < 1
    apply_patch(p, "m1", {"captions": False}, settings_tmp)
    assert c.get(f"/api/projects/{p.dir.name}/moments/m1/preview").json()["events"] == []
    assert c.get(f"/api/projects/{p.dir.name}/moments/nada/preview").status_code == 404
