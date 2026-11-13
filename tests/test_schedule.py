import pytest

from corteviral.schedule import ScheduleStore, run_due

T0 = 1_000_000.0


def test_plan_spaces_clips_by_interval(tmp_path):
    s = ScheduleStore(tmp_path)
    items = s.plan("p1", ["a", "b", "c"], 15, now=T0)
    assert [i["at"] for i in items] == [T0, T0 + 900, T0 + 1800]


def test_new_batch_starts_after_the_last_one(tmp_path):
    s = ScheduleStore(tmp_path)
    s.plan("p1", ["a", "b"], 15, now=T0)                  # termina em T0+900
    items = s.plan("p2", ["x", "y"], 15, now=T0 + 60)     # nova leva continua depois
    assert [i["at"] for i in items] == [T0 + 1800, T0 + 2700]
    assert s.summary(now=T0)["ends_at"] == T0 + 2700


def test_new_batch_waits_interval_after_last_send(tmp_path):
    s = ScheduleStore(tmp_path)
    s.plan("p1", ["a"], 15, now=T0)
    run_due(s, lambda item: None, now=T0)                 # saiu agora; fila vazia
    items = s.plan("p1", ["b"], 15, now=T0 + 60)
    assert items[0]["at"] == T0 + 900


def test_same_clip_not_queued_twice(tmp_path):
    s = ScheduleStore(tmp_path)
    s.plan("p1", ["a"], 15, now=T0)
    assert s.plan("p1", ["a", "b"], 15, now=T0) and len(s.items("p1")) == 2


def test_overdue_items_leave_one_per_interval(tmp_path):
    s = ScheduleStore(tmp_path)
    s.plan("p1", ["a", "b", "c"], 15, now=T0)
    sent = []
    late = T0 + 3600                                       # app ficou fechado: todos atrasados
    assert run_due(s, lambda i: sent.append(i["clip_id"]), now=late)["status"] == "sent"
    assert run_due(s, lambda i: sent.append(i["clip_id"]), now=late + 30) is None  # não solta tudo de uma vez
    run_due(s, lambda i: sent.append(i["clip_id"]), now=late + 900)
    assert sent == ["a", "b"]


def test_error_is_recorded_and_queue_goes_on(tmp_path):
    s = ScheduleStore(tmp_path)
    s.plan("p1", ["a", "b"], 15, now=T0)
    res = run_due(s, lambda i: (_ for _ in ()).throw(RuntimeError("n8n desligado")), now=T0)
    assert res["status"] == "error" and "n8n" in res["error"]
    assert run_due(s, lambda i: None, now=T0 + 900)["clip_id"] == "b"


def test_cancel(tmp_path):
    s = ScheduleStore(tmp_path)
    a, b = s.plan("p1", ["a", "b"], 15, now=T0)
    s.plan("p2", ["c"], 15, now=T0)
    assert s.cancel(item_id=a["id"]) == 1
    assert s.cancel(project="p1") == 1 and [i["clip_id"] for i in s.items()] == ["c"]


def test_bad_interval(tmp_path):
    with pytest.raises(ValueError):
        ScheduleStore(tmp_path).plan("p1", ["a"], 0)


def test_api_schedule_job_and_cancel(settings_tmp, sample_video):
    from fastapi.testclient import TestClient

    from corteviral.publish import PublishConfig, PublishStore
    from corteviral.server.app import create_app
    from tests.test_export import _m, _project
    from tests.test_server import _wait
    rs = settings_tmp
    rs.render.presets_dir = rs.paths.data_dir / "presets"
    rs.render.encoder = "libx264"
    rs.captions.enabled = False
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0), _m(2, 4.0, 7.0, score=70), _m(3, 3.0, 6.0, status="pending")])
    c = TestClient(create_app(rs))
    job = _wait(c, c.post("/api/jobs", json={"kind": "schedule", "project": p.dir.name, "interval_min": 15}).json()["id"])
    assert job["status"] == "error" and "rede" in job["error"]  # nenhuma rede ligada
    PublishStore(rs.paths.data_dir).save(PublishConfig(networks={"youtube": True}))
    job = _wait(c, c.post("/api/jobs", json={"kind": "schedule", "project": p.dir.name, "interval_min": 15}).json()["id"],
                timeout=120)
    assert job["status"] == "done" and job["result"]["queued"] == 2  # só os aprovados
    assert job["result"]["last_at"] - job["result"]["first_at"] == 900
    q = c.get("/api/schedule", params={"project": p.dir.name}).json()
    assert [i["clip_id"] for i in q["items"]] == ["m1", "m2"] and q["summary"]["waiting"] == 2
    assert c.delete(f"/api/schedule/{q['items'][0]['id']}").json()["removed"] == 1
    assert c.delete("/api/schedule", params={"project": p.dir.name}).json()["removed"] == 1
    assert c.post("/api/jobs", json={"kind": "schedule", "project": p.dir.name, "interval_min": 0}).status_code == 422


def test_api_queue_lists_all_with_titles_and_retry(settings_tmp, sample_video):
    from fastapi.testclient import TestClient

    from corteviral.publish import PublishStore
    from corteviral.server.app import create_app
    from tests.test_export import _m, _project
    rs = settings_tmp
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0, title="Perseguição insana"), _m(2, 4.0, 7.0, score=70)])
    s = ScheduleStore(rs.paths.data_dir)
    a, b = s.plan(p.dir.name, ["m1", "m2"], 15)
    s.mark(a["id"], "sent")
    PublishStore(rs.paths.data_dir).record(p.dir.name, "m1", "youtube", status="published",
                                           url="https://youtube.com/shorts/x")
    c = TestClient(create_app(rs))
    q = c.get("/api/queue").json()
    first = next(i for i in q["items"] if i["clip_id"] == "m1")
    assert first["title"] == "Perseguição insana" and first["project_title"] == "Live Teste"
    assert first["publish"]["youtube"]["url"].endswith("/x")
    assert next(i for i in q["items"] if i["clip_id"] == "m2")["publish"] == {}  # ainda na fila
    assert c.post(f"/api/schedule/{b['id']}/retry").status_code == 409  # ainda esperando
    r = c.post(f"/api/schedule/{a['id']}/retry").json()
    assert r["item"]["clip_id"] == "m1" and r["item"]["at"] > b["at"]  # volta depois do último


def test_pause_holds_and_resume_reschedules_from_now(tmp_path):
    s = ScheduleStore(tmp_path)
    s.plan("p1", ["a", "b"], 15, now=T0)
    s.pause()
    assert s.summary(now=T0)["paused"] and run_due(s, lambda i: None, now=T0 + 3600) is None  # nada sai pausado
    s.resume(now=T0 + 7200)
    assert [i["at"] for i in s.items()] == [T0 + 7200, T0 + 8100]  # recomeça agora, mesmo intervalo
    assert run_due(s, lambda i: None, now=T0 + 7200)["clip_id"] == "a"


def test_api_pause_resume_and_delete_all(settings_tmp):
    from fastapi.testclient import TestClient

    from corteviral.server.app import create_app
    s = ScheduleStore(settings_tmp.paths.data_dir)
    s.plan("p1", ["a"], 15)
    s.plan("p2", ["b"], 15)
    c = TestClient(create_app(settings_tmp))
    assert c.post("/api/schedule/pause").json()["paused"] is True
    assert c.post("/api/schedule/resume").json()["paused"] is False
    assert c.delete("/api/schedule").json()["removed"] == 2 and s.items() == []


def test_export_with_auto_send_queues_instead_of_posting(settings_tmp, sample_video, monkeypatch):
    from fastapi.testclient import TestClient

    import corteviral.server.app as appmod
    from corteviral.publish import PublishConfig, PublishStore
    from corteviral.server.app import create_app
    from tests.test_export import _m, _project
    from tests.test_server import _wait
    rs = settings_tmp
    rs.render.presets_dir = rs.paths.data_dir / "presets"
    rs.render.encoder = "libx264"
    rs.paths.exports_dir = rs.paths.data_dir / "exports"
    rs.captions.enabled = False
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0), _m(2, 4.0, 7.0, score=70)])
    PublishStore(rs.paths.data_dir).save(PublishConfig(networks={"youtube": True}, auto_send=True))
    posted = []
    monkeypatch.setattr(appmod, "publish_clips", lambda *a, **k: posted.append(a) or {"sent": 9, "errors": []})
    c = TestClient(create_app(rs))
    job = _wait(c, c.post("/api/jobs", json={"kind": "export", "project": p.dir.name}).json()["id"], timeout=120)
    assert job["status"] == "done" and "publish" not in job["result"]  # chave ligada, mas sem o "sim": não posta
    assert ScheduleStore(rs.paths.data_dir).items() == []
    job = _wait(c, c.post("/api/jobs", json={"kind": "export", "project": p.dir.name, "post": True}).json()["id"],
                timeout=120)
    assert job["status"] == "done" and posted == []  # com o "sim": vai para a fila, nada postado de uma vez
    assert job["result"]["publish"]["queued"] == 2
    items = ScheduleStore(rs.paths.data_dir).items(p.dir.name)
    assert len(items) == 2 and items[1]["at"] - items[0]["at"] == 900
