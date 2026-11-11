from corteviral.project import Project
from corteviral.utils import params_hash, slugify


def test_slugify_handles_title_with_emoji_and_symbols():
    assert slugify("🔴 LIVE | GTA 6: Assalto ao BANCO?! ção") == "live-gta-6-assalto-ao-banco-cao"
    assert slugify("???") == "video"


def test_slugify_respects_max_len():
    s = slugify("a" * 100, max_len=40)
    assert len(s) <= 40


def test_params_hash_is_order_independent():
    assert params_hash({"a": 1, "b": 2}) == params_hash({"b": 2, "a": 1})
    assert params_hash({"a": 1}) != params_hash({"a": 2})


def test_create_then_find_by_source_reuses(tmp_path):
    p = Project.create(tmp_path, "https://youtu.be/x", "Live GTA")
    assert p.dir.name.startswith("live-gta-")
    found = Project.find_by_source(tmp_path, "https://youtu.be/x")
    assert found is not None and found.dir == p.dir
    assert Project.find_by_source(tmp_path, "https://youtu.be/outro") is None


def test_same_title_different_source_gets_new_dir(tmp_path):
    a = Project.create(tmp_path, "a.mp4", "Live")
    b = Project.create(tmp_path, "b.mp4", "Live")
    assert a.dir != b.dir


def test_stage_cache_and_invalidation(tmp_path):
    p = Project.create(tmp_path, "a.mp4", "a")
    p.mark_stage("ingest", {"x": 1})
    p.mark_stage("transcribe", {"m": "medium"})
    assert p.is_stage_done("transcribe", {"m": "medium"})
    assert not p.is_stage_done("transcribe", {"m": "small"})
    p.invalidate_from("transcribe")
    assert p.is_stage_done("ingest", {"x": 1})
    assert not p.is_stage_done("transcribe", {"m": "medium"})


def test_project_json_roundtrip(tmp_path):
    p = Project.create(tmp_path, "a.mp4", "a")
    p.mark_stage("ingest", {})
    assert Project.load(p.dir).is_stage_done("ingest", {})


def test_stale_save_keeps_keys_changed_by_another_writer(tmp_path):
    p = Project.create(tmp_path, "a.mp4", "a")
    job = Project.load(p.dir)          # tarefa longa carregou antes
    ui = Project.load(p.dir)           # interface salva o streamer no meio
    ui.data["streamer"] = "Flow Games"
    ui.data["layout_detect"] = {"cams": []}
    ui.save()
    job.mark_stage("scoring", {"x": 1})  # tarefa termina e salva o que tinha
    final = Project.load(p.dir)
    assert final.data["streamer"] == "Flow Games" and final.data["layout_detect"] == {"cams": []}
    assert final.is_stage_done("scoring", {"x": 1})


def test_own_changes_still_win(tmp_path):
    p = Project.create(tmp_path, "a.mp4", "a")
    a, b = Project.load(p.dir), Project.load(p.dir)
    a.data["streamer"] = "A"
    a.save()
    b.data["streamer"] = "B"
    b.save()
    assert Project.load(p.dir).data["streamer"] == "B"
