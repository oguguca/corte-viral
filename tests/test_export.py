import pytest
from fastapi.testclient import TestClient

from corteviral.export import HistoryStore, export_approved, export_check, platform_texts
from corteviral.metadata import fallback_posts
from corteviral.pipeline.analyze import MOMENTS
from corteviral.presets import LayoutPreset, save_preset
from corteviral.project import Project
from corteviral.utils import write_json_atomic

CAM = {"rect": [0.70, 0.05, 0.25, 0.30], "face": [0.8, 0.1, 0.05, 0.08], "share": 1.0, "conf": 0.9}


def _meta(title):
    m = fallback_posts({"id": "x", "hook": title, "reason": "r", "text": "t"}, "Canal", None)
    for p in m["posts"].values():
        p["title"] = title
    return m


@pytest.fixture
def rs(settings_tmp):
    settings_tmp.render.presets_dir = settings_tmp.paths.data_dir / "presets"
    settings_tmp.render.encoder = "libx264"
    settings_tmp.paths.exports_dir = settings_tmp.paths.data_dir / "exports"
    settings_tmp.captions.enabled = False
    return settings_tmp


def _project(rs, sample_video, moments):
    p = Project.create(rs.paths.projects_dir, str(sample_video), "Live Teste")
    p.data.update(video_path=str(sample_video), duration=8.0, streamer="Canal",
                  layout_detect={"cams": [CAM], "gameplay": None, "source": [1280, 720]})
    p.save()
    write_json_atomic(p.path(MOMENTS), {"moments": moments, "all": [dict(m) for m in moments]})
    return p


def _m(i, start, end, status="approved", score=80, title="Olha isso"):
    return {"id": f"m{i}", "start": start, "end": end, "score": score, "reason": "r", "status": status,
            "meta": _meta(title)}


def test_one_text_file_per_platform_in_its_upload_format():
    files = platform_texts({"id": "m1", "meta": _meta("Perseguição insana")})
    assert list(files) == ["TikTok.txt", "YouTube Shorts.txt", "Instagram Reels.txt", "Kwai.txt"]
    yt = files["YouTube Shorts.txt"]
    assert "TÍTULO" in yt and "DESCRIÇÃO" in yt and "TAGS" in yt
    tags_line = yt.split("TAGS", 1)[1].splitlines()[1]
    assert "gta6" in tags_line and "#" not in tags_line and ", " in tags_line  # campo Tags: vírgulas, sem #
    for name in ("TikTok.txt", "Instagram Reels.txt", "Kwai.txt"):  # um campo só: tudo junto, pronto p/ colar
        txt = files[name]
        assert "Perseguição insana" in txt and "#gta6" in txt and "TAGS" not in txt


def test_export_names_unique_and_windows_safe(rs, sample_video):
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0, title='Qual é? "Insano": a|b'),
                                    _m(2, 4.0, 7.0, score=70, title='Qual é? "Insano": a|b')])
    res = export_approved(p, rs)
    names = [f["mp4"].name for f in res["files"]]
    assert len(set(names)) == 2 and names[0].startswith("01-") and names[1].startswith("02-")
    assert not any(c in "".join(names) for c in '?:"|<>*')


def test_export_only_approved_and_writes_txt(rs, sample_video):
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0), _m(2, 4.0, 7.0, status="discarded"), _m(3, 3.0, 6.0, status="pending")])
    res = export_approved(p, rs)
    assert [f["id"] for f in res["files"]] == ["m1"]
    f = res["files"][0]
    assert f["mp4"].exists() and f["mp4"].parent == f["dir"]
    assert sorted(x.name for x in f["dir"].iterdir() if x.suffix == ".txt") == sorted(
        ["TikTok.txt", "YouTube Shorts.txt", "Instagram Reels.txt", "Kwai.txt"])
    assert "#gta6" in (f["dir"] / "TikTok.txt").read_text(encoding="utf-8")
    assert f["dir"].parent == rs.paths.exports_dir / p.dir.name  # uma pasta por clipe


def test_second_export_reports_duplicate(rs, sample_video):
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0)])
    assert export_approved(p, rs)["duplicates"] == []
    again = export_approved(p, rs)
    assert again["duplicates"] and again["duplicates"][0]["id"] == "m1"
    assert export_check(p, rs)["duplicates"]


def test_failed_render_leaves_no_partial_files(rs, sample_video, monkeypatch):
    import corteviral.export as mod
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0), _m(2, 4.0, 7.0, score=70)])
    real = mod.render_clip

    def flaky(project, moment, settings, **kw):
        if moment["id"] == "m2":
            raise RuntimeError("ffmpeg caiu")
        return real(project, moment, settings, **kw)

    monkeypatch.setattr(mod, "render_clip", flaky)
    res = export_approved(p, rs)
    assert [f["id"] for f in res["files"]] == ["m1"] and res["errors"][0]["id"] == "m2"
    folder = rs.paths.exports_dir / p.dir.name
    assert [x.name[:3] for x in folder.iterdir()] == ["01-"]  # só a pasta do m1, nada pela metade


def test_unauthorized_streamer_flagged(rs, sample_video):
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0)])
    assert export_check(p, rs)["unauthorized"] is True
    save_preset(rs.render.presets_dir, "Canal", LayoutPreset(facecam=CAM["rect"], authorized=True))
    assert export_check(p, rs)["unauthorized"] is False


def test_history_overlap(tmp_path):
    h = HistoryStore(tmp_path / "history.json")
    h.add("src", 10.0, 40.0, "a.mp4")
    assert h.overlaps("src", 20.0, 45.0)        # 20 de 25 s sobrepostos (> 50 %)
    assert not h.overlaps("src", 38.0, 70.0)
    assert not h.overlaps("outra", 10.0, 40.0)


def test_api_export_job(rs, sample_video):
    from corteviral.server.app import create_app
    from tests.test_server import _wait
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0)])
    c = TestClient(create_app(rs))
    chk = c.get(f"/api/projects/{p.dir.name}/export-check").json()
    assert chk["approved"] == 1 and chk["unauthorized"] is True
    job = _wait(c, c.post("/api/jobs", json={"kind": "export", "project": p.dir.name}).json()["id"], timeout=120)
    assert job["status"] == "done" and job["result"]["files"][0]["mp4"].endswith(".mp4")


def test_cli_export(rs, sample_video, capsys, tmp_path, monkeypatch):
    from corteviral.cli import main
    import corteviral.cli as climod
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0)])
    monkeypatch.setattr(climod, "load_settings", lambda path=None: rs)
    assert main(["export", str(p.dir)]) == 0
    assert ".mp4" in capsys.readouterr().out


def test_export_job_writes_missing_texts_first(rs, sample_video, monkeypatch):
    import corteviral.server.app as appmod
    from corteviral.server.app import create_app
    from tests.test_server import _wait
    m1 = _m(1, 0.5, 3.0)
    m1.pop("meta")  # clipe aprovado ainda sem textos
    p = _project(rs, sample_video, [m1, _m(2, 4.0, 7.0, score=70)])
    asked = []
    monkeypatch.setattr(appmod, "generate_posts", lambda project, ids, s: asked.append(ids) or {"ai": 0})
    c = TestClient(create_app(rs))
    job = _wait(c, c.post("/api/jobs", json={"kind": "export", "project": p.dir.name}).json()["id"], timeout=120)
    assert job["status"] == "done" and asked == [["m1"]]  # só o que não tinha textos (não apaga edições)


def test_export_only_chosen_clip_even_if_not_approved(rs, sample_video):
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0), _m(2, 4.0, 7.0, status="pending", score=70)])
    res = export_approved(p, rs, ids=["m2"])
    assert [f["id"] for f in res["files"]] == ["m2"]  # só o clipe escolhido, mesmo ainda não aprovado
    assert (res["files"][0]["dir"] / "Kwai.txt").exists()


def test_api_export_one_clip(rs, sample_video):
    from corteviral.server.app import create_app
    from tests.test_server import _wait
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0), _m(2, 4.0, 7.0, score=70)])
    c = TestClient(create_app(rs))
    job = _wait(c, c.post("/api/jobs", json={"kind": "export", "project": p.dir.name, "ids": ["m2"]}).json()["id"],
                timeout=120)
    assert job["status"] == "done" and [f["id"] for f in job["result"]["files"]] == ["m2"]
