"""Envio dos clipes ao n8n (contas conectadas ficam no n8n)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from corteviral.metadata import fallback_posts
from corteviral.pipeline.analyze import MOMENTS
from corteviral.project import Project
from corteviral.publish import (NETWORKS, PublishConfig, PublishError, PublishStore, apply_callback,
                                build_payload, publish_clips, send_to_n8n, setup_account,
                                test_connection)
from corteviral.utils import write_json_atomic


class _Fake(BaseHTTPRequestHandler):
    received: list = []
    status = 200
    reply = {"ok": True}

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        item = {"path": self.path, "json": json.loads(body)}
        item["payload"] = item["json"]
        type(self).received.append(item)
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(type(self).reply).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def n8n():
    _Fake.received, _Fake.status, _Fake.reply = [], 200, {"ok": True}
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/webhook/corte-viral"
    srv.shutdown()


@pytest.fixture
def store(settings_tmp):
    return PublishStore(settings_tmp.paths.data_dir)


def _moment(i=1, status="approved", score=80):
    m = {"id": f"m{i}", "start": 1.0, "end": 6.0, "score": score, "reason": "r", "status": status,
         "text": "olha isso mano", "hook": "Olha isso"}
    m["meta"] = fallback_posts(m, "Canal", None)
    m["meta"]["posts"]["shorts"]["title"] = "Título do Shorts"
    m["meta"]["posts"]["reels"]["description"] = "Descrição do Reels"
    return m


def _project(settings, moments):
    p = Project.create(settings.paths.projects_dir, "fake.mp4", "Live Teste")
    p.data.update(streamer="Canal")
    p.save()
    write_json_atomic(p.path(MOMENTS), {"moments": moments, "all": [dict(m) for m in moments]})
    return p


# ---------- configuração ----------
def test_config_defaults_and_token(store):
    cfg = store.load()
    assert cfg.webhook_url == "http://127.0.0.1:5678/webhook/corte-viral"
    assert set(cfg.networks) == set(NETWORKS) and not any(cfg.networks.values())
    assert cfg.youtube_privacy == "private" and cfg.auto_send is False
    assert len(cfg.token) >= 32
    assert store.load().token == cfg.token  # gerado uma vez e guardado


def test_config_save_keeps_token(store):
    token = store.load().token
    store.save(PublishConfig(webhook_url="http://127.0.0.1:9999/webhook/x", networks={"youtube": True}))
    cfg = store.load()
    assert cfg.token == token and cfg.networks == {"youtube": True, "tiktok": False, "instagram": False}


def test_config_rejects_bad_url():
    with pytest.raises(ValueError):
        PublishConfig(webhook_url="file:///c:/x")


# ---------- payload ----------
def test_payload_per_network():
    m = _moment()
    cfg = PublishConfig(networks={"youtube": True, "instagram": True}, youtube_privacy="unlisted", token="t" * 32)
    p = build_payload("proj", m, m["meta"], ["youtube", "instagram"], cfg, "http://127.0.0.1:1/cb", "s1")
    assert p["networks"] == ["youtube", "instagram"] and p["send_id"] == "s1"
    assert p["callback_url"] == "http://127.0.0.1:1/cb" and p["token"] == "t" * 32
    yt = p["youtube"]
    assert yt["title"].startswith("Título do Shorts") and len(yt["title"]) <= 100
    assert yt["privacy"] == "unlisted" and "#Shorts" in yt["description"]
    assert all(not t.startswith("#") for t in yt["tags"])
    assert "Descrição do Reels" in p["instagram"]["caption"]
    assert "tiktok" not in p


# ---------- envio ----------
def test_send_json(n8n):
    res = send_to_n8n(n8n, {"a": "ção"})
    assert res == {"ok": True}
    assert _Fake.received[0]["json"] == {"a": "ção"} and _Fake.received[0]["path"] == "/webhook/corte-viral"


def test_send_errors_are_friendly(n8n):
    _Fake.status = 404
    with pytest.raises(PublishError, match="ativo"):
        send_to_n8n(n8n, {})
    with pytest.raises(PublishError, match="n8n"):
        send_to_n8n("http://127.0.0.1:1/webhook/corte-viral", {})


def test_setup_account_forwards_and_checks(n8n):
    _Fake.reply = {"ok": True}
    setup_account(n8n, "instagram", {"ig_user_id": "1", "access_token": "x"})
    assert _Fake.received[0]["json"] == {"setup": "instagram", "ig_user_id": "1", "access_token": "x", "token": ""}
    _Fake.reply = {"ok": False, "error": "token recusado"}
    with pytest.raises(PublishError, match="recusado"):
        setup_account(n8n, "tiktok", {"code": "c"})
    with pytest.raises(PublishError):
        setup_account(n8n, "youtube", {})


def test_connection_test(n8n):
    _Fake.reply = {"ok": True, "message": "pronto"}
    assert test_connection(n8n)["ok"] is True
    assert _Fake.received[0]["json"] == {"test": True, "token": ""}


# ---------- publicar ----------
def test_publish_sends_enabled_networks_and_skips_published(n8n, settings_tmp, store, tmp_path, monkeypatch):
    mp4 = tmp_path / "final.mp4"
    mp4.write_bytes(b"clip")
    monkeypatch.setattr("corteviral.publish.render_clip", lambda project, m, settings: mp4)
    p = _project(settings_tmp, [_moment(1), _moment(2, status="pending"), _moment(3)])
    store.save(PublishConfig(webhook_url=n8n, networks={"youtube": True, "tiktok": True}))

    res = publish_clips(p, settings_tmp, store, None, "http://cb")
    assert res["sent"] == 2 and not res["errors"]
    assert [r["payload"]["clip_id"] for r in _Fake.received] == ["m1", "m3"]
    first = _Fake.received[0]["payload"]
    assert first["networks"] == ["tiktok", "youtube"]
    assert first["file_path"] == str(mp4.resolve()) and first["file_size"] == 4
    st = store.status(p.dir.name)
    assert st["m1"]["youtube"]["status"] == "sent"

    sid = _Fake.received[0]["payload"]["send_id"]
    apply_callback(store, {"token": store.load().token, "send_id": sid, "project": p.dir.name, "clip_id": "m1",
                           "network": "youtube", "status": "published", "url": "https://youtu.be/x"})
    _Fake.received.clear()
    monkeypatch.setattr("corteviral.publish.SENT_WINDOW", 0)  # o envio do TikTok já é antigo (sem resposta)
    res = publish_clips(p, settings_tmp, store, ["m1"], "http://cb")
    assert [r["payload"]["networks"] for r in _Fake.received] == [["tiktok"]]  # YouTube já publicado
    res = publish_clips(p, settings_tmp, store, ["m1"], "http://cb", force=True)
    assert _Fake.received[-1]["payload"]["networks"] == ["tiktok", "youtube"]


def test_publish_needs_a_network(settings_tmp, store):
    p = _project(settings_tmp, [_moment(1)])
    with pytest.raises(PublishError, match="rede"):
        publish_clips(p, settings_tmp, store, None, "http://cb")


def test_publish_records_send_errors(settings_tmp, store, tmp_path, monkeypatch):
    mp4 = tmp_path / "final.mp4"
    mp4.write_bytes(b"clip")
    monkeypatch.setattr("corteviral.publish.render_clip", lambda project, m, settings: mp4)
    p = _project(settings_tmp, [_moment(1)])
    store.save(PublishConfig(webhook_url="http://127.0.0.1:1/webhook/x", networks={"instagram": True}))
    res = publish_clips(p, settings_tmp, store, None, "http://cb")
    assert res["sent"] == 0 and res["errors"][0]["id"] == "m1"
    assert store.status(p.dir.name)["m1"]["instagram"]["status"] == "error"


# ---------- retorno do n8n ----------
def test_callback_requires_token(store):
    store.load()
    with pytest.raises(PublishError):
        apply_callback(store, {"token": "errado", "project": "p", "clip_id": "m1", "network": "youtube",
                               "status": "published"})
    with pytest.raises(PublishError):
        apply_callback(store, {"token": store.load().token, "project": "p", "clip_id": "m1",
                               "network": "orkut", "status": "published"})


def test_callback_records_error_and_recent(store):
    t = store.load().token
    apply_callback(store, {"token": t, "project": "p", "clip_id": "m1", "network": "tiktok",
                           "status": "error", "error": "token expirado"})
    st = store.status("p")["m1"]["tiktok"]
    assert st["status"] == "error" and st["error"] == "token expirado"
    assert store.recent()[0]["network"] == "tiktok"


# ---------- rotas ----------
def test_routes_config_callback_and_job(settings_tmp, n8n, tmp_path, monkeypatch):
    import time as _t

    from fastapi.testclient import TestClient

    from corteviral.server.app import create_app

    mp4 = tmp_path / "final.mp4"
    mp4.write_bytes(b"clip")
    monkeypatch.setattr("corteviral.publish.render_clip", lambda project, m, settings: mp4)
    p = _project(settings_tmp, [_moment(1)])
    c = TestClient(create_app(settings_tmp))

    st = c.get("/api/publish").json()
    assert "token" not in st["config"] and st["n8n"]["installed"] in (True, False)
    assert c.put("/api/publish/config", json={"webhook_url": "ftp://x", "networks": {}}).status_code == 422
    r = c.put("/api/publish/config", json={"webhook_url": n8n, "networks": {"tiktok": True}})
    assert r.json()["config"]["networks"]["tiktok"] is True and "token" not in r.json()["config"]

    assert c.post("/api/publish/test").json()["ok"] is True

    job = c.post("/api/jobs", json={"kind": "publish", "project": p.dir.name,
                                    "callback_url": "http://malicioso"}).json()
    for _ in range(100):
        job = c.get(f"/api/jobs/{job['id']}").json()
        if job["status"] != "running":
            break
        _t.sleep(0.05)
    assert job["status"] == "done", job
    sent = _Fake.received[-1]["payload"]
    assert sent["callback_url"].endswith("/api/publish/callback") and "malicioso" not in sent["callback_url"]

    bad = c.post("/api/publish/callback", json={"token": "x", "project": p.dir.name, "clip_id": "m1",
                                                 "network": "tiktok", "status": "published"})
    assert bad.status_code == 403
    ok = c.post("/api/publish/callback", json={"token": sent["token"], "project": p.dir.name, "clip_id": "m1",
                                                "send_id": sent["send_id"], "network": "tiktok",
                                                "status": "published", "url": "https://t/x"})
    assert ok.status_code == 200
    assert c.get(f"/api/projects/{p.dir.name}/publish").json()["m1"]["tiktok"]["url"] == "https://t/x"


# ---------- revisão de segurança ----------
def test_recently_sent_is_not_resent_without_force(n8n, settings_tmp, store, tmp_path, monkeypatch):
    mp4 = tmp_path / "final.mp4"
    mp4.write_bytes(b"clip")
    monkeypatch.setattr("corteviral.publish.render_clip", lambda project, m, settings: mp4)
    p = _project(settings_tmp, [_moment(1)])
    store.save(PublishConfig(webhook_url=n8n, networks={"tiktok": True}))
    publish_clips(p, settings_tmp, store, None, "http://cb")
    res = publish_clips(p, settings_tmp, store, None, "http://cb")  # clique duplo: ainda aguardando o n8n
    assert res["sent"] == 0 and res["skipped"] == ["m1"] and len(_Fake.received) == 1
    publish_clips(p, settings_tmp, store, None, "http://cb", force=True)
    assert len(_Fake.received) == 2


def test_callback_rejects_non_https_url_and_stale_send(store):
    t = store.load().token
    store.record("p", "m1", "tiktok", status="sent", send_id="novo")
    apply_callback(store, {"token": t, "project": "p", "clip_id": "m1", "network": "tiktok", "send_id": "velho",
                           "status": "error", "error": "atrasado"})
    assert store.status("p")["m1"]["tiktok"]["status"] == "sent"  # resultado de um envio antigo é ignorado
    apply_callback(store, {"token": t, "project": "p", "clip_id": "m1", "network": "tiktok", "send_id": "novo",
                           "status": "published", "url": "javascript:alert(1)"})
    e = store.status("p")["m1"]["tiktok"]
    assert e["status"] == "published" and "url" not in e


def test_resend_drops_old_url(store):
    store.record("p", "m1", "youtube", status="published", url="https://youtu.be/x", send_id="a")
    store.record("p", "m1", "youtube", status="sent", send_id="b")
    assert "url" not in store.status("p")["m1"]["youtube"]


def test_test_and_setup_send_the_app_token(n8n):
    test_connection(n8n, "segredo")
    setup_account(n8n, "instagram", {"ig_user_id": "1", "access_token": "x"}, "segredo")
    assert [r["json"]["token"] for r in _Fake.received] == ["segredo", "segredo"]
