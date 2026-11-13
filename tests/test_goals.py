import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from corteviral import goals
from corteviral.goals import GoalsStore, daily_counts, parse_channel, program_progress


def test_parse_channel_accepts_handle_id_and_url():
    assert parse_channel("@DavyJones") == ("forHandle", "@DavyJones")
    assert parse_channel("DavyJones") == ("forHandle", "@DavyJones")
    assert parse_channel("https://www.youtube.com/@DavyJones/shorts") == ("forHandle", "@DavyJones")
    cid = "UC" + "a" * 22
    assert parse_channel(cid) == ("id", cid)
    assert parse_channel(f"https://youtube.com/channel/{cid}") == ("id", cid)


def test_store_hides_key_and_keeps_it_on_save(tmp_path):
    s = GoalsStore(tmp_path)
    s.save({"youtube": {"api_key": "AIzaSECRET", "channel": "@canal"}, "daily_videos": 4})
    pub = s.public()
    assert "AIzaSECRET" not in json.dumps(pub) and pub["youtube"]["has_key"] is True
    s.save({"daily_videos": 5, "youtube": {"channel": "@outro"}})  # sem chave: mantém a salva
    assert s.load()["youtube"]["api_key"] == "AIzaSECRET" and s.load()["daily_videos"] == 5


def test_daily_counts_from_exports_and_posts(tmp_path):
    now = time.time()
    (tmp_path / "history.json").write_text(json.dumps(
        [{"ts": now}, {"ts": now}, {"ts": now - 86400}, {"ts": now - 30 * 86400}]))
    (tmp_path / "publish_log.json").write_text(json.dumps(
        {"a": {"status": "published", "ts": now, "clip_id": "x"}, "b": {"status": "error", "ts": now}}))
    d = daily_counts(tmp_path, goal=3)
    assert d["today"] == 3 and d["posted_today"] == 1  # 2 exportados + 1 postado
    assert len(d["last7"]) == 7 and d["last7"][-1]["count"] == 3 and d["last7"][-2]["count"] == 1
    assert d["goal"] == 3 and d["streak"] == 1  # hoje 3 de 3: bateu


def test_program_progress_uses_youtube_and_manual_numbers():
    yt = {"subscribers": 250, "shorts_views_90d": 2_500_000}
    out = {p["id"]: p for p in program_progress(yt, {"tiktok": {"followers": 5000, "views_30d": 150000}})}
    first = {r["key"]: r for r in out["youtube1"]["requirements"]}  # 1º nível: 500 inscritos + 3 mi de views
    assert first["subscribers"]["target"] == 500 and first["subscribers"]["percent"] == 50.0
    assert first["shorts_views_90d"]["target"] == 3_000_000 and first["shorts_90d"]["target"] == 3
    sub = next(r for r in out["youtube2"]["requirements"] if r["key"] == "subscribers")
    assert sub["current"] == 250 and sub["target"] == 1000 and sub["percent"] == 25.0
    views = next(r for r in out["tiktok"]["requirements"] if r["key"] == "views_30d")
    assert views["percent"] == 100.0 and views["done"] is True
    assert out["youtube1"]["auto"] is True and out["tiktok"]["auto"] is False


class _Resp:
    def __init__(self, data): self.data = json.dumps(data).encode()
    def read(self): return self.data
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_youtube_stats_sums_recent_shorts(monkeypatch):
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
    calls = []

    def fake(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        calls.append(url)
        assert "key=K" in url
        if "/channels?" in url:
            return _Resp({"items": [{"id": "UC1", "snippet": {"title": "Canal"},
                                     "statistics": {"subscriberCount": "321", "viewCount": "9000", "videoCount": "3"},
                                     "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        if "/playlistItems?" in url:
            return _Resp({"items": [{"contentDetails": {"videoId": v, "videoPublishedAt": t}}
                                    for v, t in (("a", recent), ("b", recent), ("c", old))]})
        if "/videos?" in url:
            return _Resp({"items": [
                {"id": "a", "statistics": {"viewCount": "1000"}, "contentDetails": {"duration": "PT45S"}},
                {"id": "b", "statistics": {"viewCount": "500"}, "contentDetails": {"duration": "PT12M3S"}}]})
        raise AssertionError(url)

    monkeypatch.setattr(goals.urllib.request, "urlopen", fake)
    st = goals.youtube_stats("K", "@canal")
    assert st["subscribers"] == 321 and st["views"] == 9000 and st["title"] == "Canal"
    assert st["shorts_views_90d"] == 1000 and st["shorts_90d"] == 1  # vídeo longo e vídeo antigo não contam


def test_api_goals_roundtrip(settings_tmp, monkeypatch):
    from corteviral.server.app import create_app
    monkeypatch.setattr(goals, "youtube_stats", lambda key, ch: {"subscribers": 10, "views": 99, "title": "C",
                                                                 "shorts_views_90d": 5, "shorts_90d": 1})
    c = TestClient(create_app(settings_tmp))
    assert c.get("/api/goals").json()["youtube"]["connected"] is False
    r = c.put("/api/goals", json={"daily_videos": 2, "youtube": {"api_key": "AIzaTESTKEY", "channel": "@canal"},
                                 "manual": {"tiktok": {"followers": 7}}})
    assert r.status_code == 200
    g = c.get("/api/goals?refresh=1").json()
    assert g["youtube"]["connected"] is True and g["youtube"]["stats"]["subscribers"] == 10
    assert g["daily"]["goal"] == 2 and "AIzaTESTKEY" not in json.dumps(g)
    tt = next(p for p in g["programs"] if p["network"] == "tiktok")
    assert tt["requirements"][0]["current"] == 7
    assert c.put("/api/goals", json={"daily_videos": 0}).status_code == 422


def test_daily_counts_queue_releases_once_per_clip(tmp_path):
    now = time.time()
    (tmp_path / "history.json").write_text(json.dumps([{"ts": now, "project": "p", "clip_id": "m1"}]))
    (tmp_path / "publish_queue.json").write_text(json.dumps({"items": [
        {"project": "p", "clip_id": "m1", "status": "sent", "sent_at": now},    # exportado e soltou: conta 1
        {"project": "p", "clip_id": "m2", "status": "sent", "sent_at": now},
        {"project": "p", "clip_id": "m3", "status": "waiting", "at": now + 900}]}))  # ainda na fila: não conta
    (tmp_path / "publish_log.json").write_text(json.dumps(
        {"k": {"project": "p", "clip_id": "m2", "status": "published", "ts": now}}))
    assert daily_counts(tmp_path, goal=3)["today"] == 2


def test_gained_today_counts_from_yesterdays_last_reading():
    from datetime import date

    from corteviral.goals import gained
    today = date(2026, 11, 13)
    daily = {"2026-11-12": {"first": {"subscribers": 3400, "views": 70000, "views_recent": 10000, "ts": 1},
                            "last": {"subscribers": 3410, "views": 76000, "views_recent": 15000, "ts": 2}},
             "2026-11-13": {"first": {"subscribers": 3420, "views": 76000, "views_recent": 18000, "ts": 3}}}
    now = {"subscribers": 3420, "views": 76000, "views_recent": 21000}  # total do canal parado; vídeos andando
    g = gained(now, daily, today)
    assert g["today_views"] == 6000 and g["today_from_yesterday"] and g["today_subscribers"] == 10
    old = {"2026-11-12": {"subscribers": 3410, "views": 76530}, "2026-11-13": {"subscribers": 3420, "views": 93898}}
    g = gained({"subscribers": 3420, "views": 94000}, old, today)  # formato antigo: conta desde a 1ª leitura de hoje
    assert g["today_views"] == 102 and not g["today_from_yesterday"]
    assert g["week_views"] == 94000 - 76530
