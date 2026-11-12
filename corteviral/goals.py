"""Metas do painel inicial: requisitos de monetização de cada rede, números do canal do YouTube (API grátis do
YouTube Data v3 com chave própria) e meta diária de clipes (exportados/postados por dia).

TikTok, Instagram e Kwai não têm API grátis para contas pessoais: o usuário digita os números no painel.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from corteviral.utils import write_json_atomic

API = "https://www.googleapis.com/youtube/v3"
CACHE_SECONDS = 30 * 60      # a cota grátis é de 10.000 unidades/dia; cada atualização gasta ~20
SHORT_MAX_SECONDS = 180       # Shorts podem ter até 3 minutos
WINDOW_DAYS = 90              # o YouTube conta as views de Shorts dos últimos 90 dias
MAX_PAGES = 10                # até 500 envios recentes
_LOCK = threading.Lock()

# Requisitos públicos de cada programa (conferidos em 2026; as redes mudam as regras — os textos avisam).
PROGRAMS = [
    {"id": "youtube1", "network": "youtube", "name": "YouTube — 1º nível",
     "program": "Parcerias: Super Thanks, clubes de canais e loja",
     "requirements": [("subscribers", "Inscritos", 500), ("shorts_90d", "Shorts publicados em 90 dias", 3),
                      ("shorts_views_90d", "Views de Shorts em 90 dias", 3_000_000)],
     "note": "Também vale 3.000 horas de exibição em vídeos longos (12 meses) no lugar das views de Shorts."},
    {"id": "youtube2", "network": "youtube", "name": "YouTube — anúncios",
     "program": "Parcerias: receita de anúncios dos Shorts",
     "requirements": [("subscribers", "Inscritos", 1_000), ("shorts_views_90d", "Views de Shorts em 90 dias", 10_000_000)],
     "note": "Também vale 4.000 horas de exibição em vídeos longos (12 meses) no lugar das views de Shorts."},
    {"id": "tiktok", "network": "tiktok", "name": "TikTok", "program": "Programa de Recompensas para Criadores",
     "requirements": [("followers", "Seguidores", 10_000), ("views_30d", "Views em 30 dias", 100_000)],
     "note": "Só paga vídeos com mais de 1 minuto. Precisa ter 18 anos ou mais."},
    {"id": "instagram", "network": "instagram", "name": "Instagram", "program": "Monetização (assinaturas e presentes)",
     "requirements": [("followers", "Seguidores", 10_000)],
     "note": "Alguns programas do Instagram são só por convite. Confira em Painel profissional › Monetização."},
    {"id": "kwai", "network": "kwai", "name": "Kwai", "program": "Meta de crescimento",
     "requirements": [("followers", "Seguidores", 10_000)],
     "note": "O Kwai não divulga uma meta fixa: use como meta de crescimento do perfil."},
]
MANUAL_KEYS: dict[str, list[str]] = {}
for _p in PROGRAMS:  # as duas metas do YouTube dividem os mesmos números
    MANUAL_KEYS.setdefault(_p["network"], [])
    MANUAL_KEYS[_p["network"]] += [k for k, _, _ in _p["requirements"] if k not in MANUAL_KEYS[_p["network"]]]


class GoalsError(Exception):
    """Falha mostrada ao usuário como está."""


# ---------------- configuração ----------------
class GoalsStore:
    """data/goals.json: meta diária, números digitados e a conta do YouTube (a chave nunca volta para a tela)."""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.path = self.dir / "goals.json"
        self.stats_path = self.dir / "youtube_stats.json"

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        return {"daily_videos": int(data.get("daily_videos") or 3),
                "youtube": {"api_key": "", "channel": "", **(data.get("youtube") or {})},
                "manual": data.get("manual") or {}}

    def save(self, patch: dict) -> dict:
        with _LOCK:
            cur = self.load()
            if "daily_videos" in patch:
                cur["daily_videos"] = int(patch["daily_videos"])
            yt = patch.get("youtube") or {}
            if yt.get("api_key"):
                cur["youtube"]["api_key"] = str(yt["api_key"]).strip()
            if yt.get("remove"):
                cur["youtube"] = {"api_key": "", "channel": ""}
                self.stats_path.unlink(missing_ok=True)
            if "channel" in yt:
                if yt["channel"] != cur["youtube"]["channel"]:
                    self.stats_path.unlink(missing_ok=True)  # outro canal: o histórico antigo não vale
                cur["youtube"]["channel"] = str(yt["channel"]).strip()
            for net, vals in (patch.get("manual") or {}).items():
                if net in MANUAL_KEYS and isinstance(vals, dict):
                    keep = cur["manual"].setdefault(net, {})
                    for k in MANUAL_KEYS[net]:
                        if k in vals:
                            keep[k] = max(0, int(vals[k] or 0))
            self.dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.path, cur)
            return cur

    def public(self) -> dict:
        cur = self.load()
        yt = cur["youtube"]
        return {"daily_videos": cur["daily_videos"], "manual": cur["manual"],
                "youtube": {"channel": yt["channel"], "has_key": bool(yt["api_key"])}}

    # --- números do YouTube: cache + primeira leitura de cada dia (para "ganhou hoje / na semana") ---
    def _stats(self) -> dict:
        try:
            return json.loads(self.stats_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def youtube(self, refresh: bool = False) -> dict:
        cfg = self.load()["youtube"]
        if not (cfg["api_key"] and cfg["channel"]):
            return {"connected": False}
        saved = self._stats()
        stats, err = saved.get("last"), None
        if refresh or not stats or time.time() - saved.get("ts", 0) > CACHE_SECONDS:
            try:
                stats = youtube_stats(cfg["api_key"], cfg["channel"])
                today = date.today().isoformat()
                daily = saved.get("daily") or {}
                daily.setdefault(today, {"subscribers": stats["subscribers"], "views": stats["views"]})
                cutoff = (date.today() - timedelta(days=40)).isoformat()
                daily = {d: v for d, v in daily.items() if d >= cutoff}
                saved = {"last": stats, "ts": time.time(), "daily": daily}
                self.dir.mkdir(parents=True, exist_ok=True)
                write_json_atomic(self.stats_path, saved)
            except GoalsError as e:
                err = str(e)
        out = {"connected": True, "channel": cfg["channel"], "error": err, "stats": stats,
               "updated": saved.get("ts")}
        if stats:
            daily = saved.get("daily") or {}
            first_today = daily.get(date.today().isoformat())
            week = daily.get((date.today() - timedelta(days=7)).isoformat()) or (
                daily[min(daily)] if daily else None)
            out["gained"] = {
                "today_views": stats["views"] - first_today["views"] if first_today else 0,
                "today_subscribers": stats["subscribers"] - first_today["subscribers"] if first_today else 0,
                "week_views": stats["views"] - week["views"] if week else 0,
                "since": min(daily) if daily else None}
        return out


# ---------------- YouTube Data API ----------------
def parse_channel(text: str) -> tuple[str, str]:
    """@handle, nome do handle, ID (UC…) ou link do canal → parâmetro do channels.list."""
    t = (text or "").strip()
    m = re.search(r"(UC[\w-]{22})", t)
    if m:
        return "id", m.group(1)
    m = re.search(r"youtube\.com/(@[\w.\-]+)", t)
    if m:
        return "forHandle", m.group(1)
    t = t.split("/")[0]
    if not re.fullmatch(r"@?[\w.\-]{3,100}", t):
        raise GoalsError("Canal inválido: use o @ do canal ou o link dele.")
    return "forHandle", t if t.startswith("@") else "@" + t


def _get(path: str, params: dict, key: str) -> dict:
    url = f"{API}/{path}?" + urllib.parse.urlencode({**params, "key": key})
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read())["error"]["errors"][0]["reason"]
        except Exception:  # noqa: BLE001
            reason = ""
        if reason in ("keyInvalid", "badRequest") or e.code == 400:
            raise GoalsError("Chave da API do YouTube inválida. Confira em Contas conectadas.") from e
        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            raise GoalsError("Cota grátis do YouTube acabou por hoje; amanhã volta.") from e
        if e.code == 403:
            raise GoalsError("A chave não tem a YouTube Data API v3 ativada no Google Cloud.") from e
        raise GoalsError(f"O YouTube respondeu erro {e.code}.") from e
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise GoalsError("Sem conexão com o YouTube agora.") from e


def _seconds(iso: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def youtube_stats(key: str, channel: str) -> dict:
    kind, value = parse_channel(channel)
    ch = (_get("channels", {"part": "snippet,statistics,contentDetails", kind: value}, key).get("items") or [None])[0]
    if not ch:
        raise GoalsError("Canal do YouTube não encontrado. Confira o @ do canal.")
    st = ch.get("statistics") or {}
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    recent, token = [], None
    for _ in range(MAX_PAGES):  # envios mais novos primeiro; para quando passa de 90 dias
        params = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
        if token:
            params["pageToken"] = token
        page = _get("playlistItems", params, key)
        old = False
        for it in page.get("items") or []:
            cd = it.get("contentDetails") or {}
            pub = cd.get("videoPublishedAt")
            if pub and datetime.fromisoformat(pub.replace("Z", "+00:00")) >= since:
                recent.append(cd["videoId"])
            else:
                old = True
        token = page.get("nextPageToken")
        if old or not token:
            break
    views, shorts = 0, 0
    for i in range(0, len(recent), 50):
        vids = _get("videos", {"part": "statistics,contentDetails", "id": ",".join(recent[i:i + 50])}, key)
        for v in vids.get("items") or []:
            if 0 < _seconds((v.get("contentDetails") or {}).get("duration", "")) <= SHORT_MAX_SECONDS:
                shorts += 1
                views += int((v.get("statistics") or {}).get("viewCount") or 0)
    return {"title": (ch.get("snippet") or {}).get("title", ""), "channel_id": ch.get("id"),
            "subscribers": int(st.get("subscriberCount") or 0), "views": int(st.get("viewCount") or 0),
            "videos": int(st.get("videoCount") or 0), "shorts_views_90d": views, "shorts_90d": shorts}


# ---------------- progresso ----------------
def program_progress(yt: dict | None, manual: dict) -> list[dict]:
    out = []
    for p in PROGRAMS:
        net = p["network"]
        auto = net == "youtube" and bool(yt)
        src = yt if auto else (manual.get(net) or {})
        reqs = []
        for key, label, target in p["requirements"]:
            cur = int(src.get(key) or 0)
            reqs.append({"key": key, "label": label, "target": target, "current": cur,
                         "percent": round(min(100.0, cur / target * 100), 1), "done": cur >= target})
        out.append({"id": p["id"], "network": net, "name": p["name"], "program": p["program"], "note": p["note"], "auto": auto,
                    "requirements": reqs, "percent": round(sum(r["percent"] for r in reqs) / len(reqs), 1),
                    "done": all(r["done"] for r in reqs)})
    return out


def daily_counts(data_dir: Path, goal: int, days: int = 7) -> dict:
    """Clipes soltos por dia: exportados (history.json), enviados pela fila (publish_queue.json) ou postados
    (publish_log.json). O mesmo clipe conta uma vez só por dia."""
    data_dir = Path(data_dir)

    def read(name, default):
        try:
            return json.loads((data_dir / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    seen: dict[str, set] = {}

    def add(ts, key) -> None:
        if ts:
            seen.setdefault(datetime.fromtimestamp(ts).date().isoformat(), set()).add(key)

    for i, e in enumerate(read("history.json", [])):
        key = (e["project"], e["clip_id"]) if e.get("project") and e.get("clip_id") else ("export", e.get("file") or i)
        add(e.get("ts"), key)
    queue = read("publish_queue.json", {})
    for e in (queue.get("items", []) if isinstance(queue, dict) else []):
        if e.get("status") == "sent":
            add(e.get("sent_at"), (e.get("project"), e.get("clip_id")))
    for e in read("publish_log.json", {}).values():
        if e.get("status") in ("sent", "published"):
            add(e.get("ts"), (e.get("project"), e.get("clip_id")))
    per_day = {d: len(keys) for d, keys in seen.items()}
    today = date.today()
    posted = {(e.get("project"), e.get("clip_id")) for e in read("publish_log.json", {}).values()
              if e.get("status") == "published" and datetime.fromtimestamp(e.get("ts", 0)).date() == today}
    last = [{"date": (today - timedelta(days=i)).isoformat(),
             "count": per_day.get((today - timedelta(days=i)).isoformat(), 0)} for i in range(days - 1, -1, -1)]
    streak, d = 0, today
    if per_day.get(d.isoformat(), 0) < goal:
        d -= timedelta(days=1)  # hoje ainda não acabou: a sequência conta até ontem
        streak_from_yesterday = True
    else:
        streak_from_yesterday = False
    while per_day.get(d.isoformat(), 0) >= goal:
        streak += 1
        d -= timedelta(days=1)
    return {"goal": goal, "today": per_day.get(today.isoformat(), 0), "posted_today": len(posted),
            "last7": last, "streak": streak, "streak_until_yesterday": streak_from_yesterday}
