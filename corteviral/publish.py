"""Publicação via n8n: o Corte Viral entrega cada clipe aprovado (MP4 + textos por rede) a um webhook do n8n,
onde ficam as contas conectadas (YouTube, TikTok, Instagram). O n8n devolve o resultado em /api/publish/callback.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field, field_validator

from corteviral.config import Settings
from corteviral.export import _approved, _meta
from corteviral.pipeline.render import render_clip
from corteviral.presets import load_preset
from corteviral.project import Project
from corteviral.utils import write_json_atomic

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]

# rede → (nome na tela, chave dos textos já gerados pela IA)
NETWORKS: dict[str, tuple[str, str]] = {
    "tiktok": ("TikTok", "tiktok"),
    "youtube": ("YouTube Shorts", "shorts"),
    "instagram": ("Instagram Reels", "reels"),
}
_LOCK = threading.Lock()


class PublishError(Exception):
    """Falha mostrada ao usuário como está."""


class PublishConfig(BaseModel):
    webhook_url: str = "http://127.0.0.1:5678/webhook/corte-viral"
    networks: dict[str, bool] = Field(default_factory=lambda: {k: False for k in NETWORKS})
    youtube_privacy: Literal["private", "unlisted", "public"] = "private"
    auto_send: bool = False       # envia logo depois de exportar
    token: str = ""               # segredo que o n8n devolve no retorno (gerado no primeiro uso)

    @field_validator("webhook_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")) or len(v) > 500:
            raise ValueError("endereço do n8n inválido: use http://… ou https://…")
        return v

    @field_validator("networks")
    @classmethod
    def _nets(cls, v: dict[str, bool]) -> dict[str, bool]:
        return {k: bool(v.get(k, False)) for k in NETWORKS}

    def enabled(self) -> list[str]:
        return [k for k in NETWORKS if self.networks.get(k)]


class PublishStore:
    """Configuração (data/publish.json) e registro dos envios (data/publish_log.json)."""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.cfg_path = self.dir / "publish.json"
        self.log_path = self.dir / "publish_log.json"

    def load(self) -> PublishConfig:
        try:
            cfg = PublishConfig.model_validate(json.loads(self.cfg_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            cfg = PublishConfig()
        if len(cfg.token) < 32:
            cfg.token = secrets.token_hex(24)
            self._write_cfg(cfg)
        return cfg

    def save(self, cfg: PublishConfig) -> PublishConfig:
        cfg = cfg.model_copy(update={"token": self.load().token})  # o segredo nunca vem da interface
        self._write_cfg(cfg)
        return cfg

    def _write_cfg(self, cfg: PublishConfig) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.cfg_path, cfg.model_dump())

    # --- registro ---
    def _entries(self) -> dict:
        try:
            return json.loads(self.log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def record(self, project: str, clip_id: str, network: str, **fields) -> None:
        with _LOCK:
            items = self._entries()
            key = f"{project}/{clip_id}/{network}"
            entry = {**items.get(key, {}), **fields, "project": project, "clip_id": clip_id,
                     "network": network, "ts": time.time()}
            if fields.get("status") != "error":
                entry.pop("error", None)
            if fields.get("status") == "sent":
                entry.pop("url", None)  # novo envio: o link antigo não vale mais
            items[key] = entry
            self.dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.log_path, items)

    def status(self, project: str) -> dict[str, dict[str, dict]]:
        out: dict[str, dict[str, dict]] = {}
        for e in self._entries().values():
            if e.get("project") == project:
                out.setdefault(e["clip_id"], {})[e["network"]] = e
        return out

    def recent(self, limit: int = 20) -> list[dict]:
        return sorted(self._entries().values(), key=lambda e: -e.get("ts", 0))[:limit]


# ---------------- payload ----------------
def _tags(hashtags: list[str]) -> list[str]:
    return [h.lstrip("#") for h in hashtags if h.strip("#")]


def _caption(post: dict, limit: int = 2200) -> str:
    parts = [post.get("title", ""), post.get("description", ""), " ".join(post.get("hashtags", []))]
    return "\n\n".join(p for p in parts if p).strip()[:limit]


def build_payload(project: str, moment: dict, meta: dict, networks: list[str], cfg: PublishConfig,
                  callback_url: str, send_id: str) -> dict:
    posts = meta.get("posts") or {}
    out = {"send_id": send_id, "project": project, "clip_id": moment["id"], "networks": networks,
           "hook": meta.get("hook", ""), "duration": round(moment["end"] - moment["start"], 2),
           "callback_url": callback_url, "token": cfg.token}
    for net in networks:
        post = posts.get(NETWORKS[net][1]) or {}
        if net == "youtube":
            desc = (post.get("description", "") + "\n\n" + " ".join(post.get("hashtags", []))).strip()
            if "#shorts" not in desc.lower():
                desc += " #Shorts"
            out[net] = {"title": (post.get("title") or meta.get("hook") or "Corte")[:100], "description": desc[:4900],
                        "tags": _tags(post.get("hashtags", []))[:15], "privacy": cfg.youtube_privacy}
        else:
            out[net] = {"caption": _caption(post), "title": post.get("title", "")}
    return out


# ---------------- HTTP ----------------
def _post(req: urllib.request.Request, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise PublishError("O n8n respondeu, mas o fluxo “Corte Viral” não está ativo. "
                               "Abra o n8n, importe o fluxo e ative-o.") from e
        detail = e.read()[:300].decode("utf-8", "replace")
        raise PublishError(f"O n8n recusou o envio (HTTP {e.code}): {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise PublishError("Não consegui falar com o n8n. Ele está ligado? (Contas conectadas › Iniciar n8n)") from e
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError:
        return {"raw": body[:300].decode("utf-8", "replace")}


def send_to_n8n(url: str, body: dict, timeout: float = 60) -> dict:
    """POST JSON ao webhook. O n8n roda neste PC e lê o MP4 direto do disco (`file_path`)."""
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    return _post(req, timeout)


def test_connection(url: str, token: str = "", timeout: float = 15) -> dict:
    """O fluxo responde {"ok": true, "accounts": {...}} dizendo quais contas já estão conectadas nele."""
    res = send_to_n8n(url, {"test": True, "token": token}, timeout)
    if res.get("ok") is False:
        raise PublishError(res.get("error") or "o n8n recusou o teste")
    return res


def setup_account(url: str, network: str, fields: dict, token: str = "", timeout: float = 60) -> dict:
    """Entrega ao n8n os dados de uma conta (TikTok/Instagram); o Corte Viral não guarda nada disso."""
    if network not in ("tiktok", "instagram"):
        raise PublishError("rede inválida")
    res = send_to_n8n(url, {**fields, "setup": network, "token": token}, timeout)
    if not res.get("ok"):
        raise PublishError(res.get("error") or "o n8n não confirmou a conexão")
    return res


test_connection.__test__ = False  # não é um teste do pytest


# ---------------- publicar ----------------
SENT_WINDOW = 30 * 60  # s: enviado há pouco = o n8n ainda está postando; não manda de novo (evita post duplicado)


def _busy_or_done(entry: dict | None) -> bool:
    if not entry:
        return False
    if entry.get("status") == "published":
        return True
    return entry.get("status") == "sent" and time.time() - entry.get("ts", 0) < SENT_WINDOW
def publish_clips(project: Project, settings: Settings, store: PublishStore, ids: list[str] | None,
                  callback_url: str, force: bool = False, on_progress: ProgressFn | None = None) -> dict:
    cfg = store.load()
    nets = cfg.enabled()
    if not nets:
        raise PublishError("Ative pelo menos uma rede em Contas conectadas.")
    streamer = project.data.get("streamer")
    preset = load_preset(settings.render.presets_dir, streamer)
    credit = preset.credit_handle if preset else None
    moments = [m for m in _approved(project) if ids is None or m["id"] in ids]
    if not moments:
        raise PublishError("Nenhum clipe aprovado para enviar.")
    done = store.status(project.dir.name)
    sent, skipped, errors = 0, [], []
    for i, m in enumerate(moments, 1):
        if on_progress:
            on_progress((i - 1) / len(moments), f"enviando clipe {i} de {len(moments)} ao n8n")
        todo = [n for n in nets if force or not _busy_or_done(done.get(m["id"], {}).get(n))]
        if not todo:
            skipped.append(m["id"])
            continue
        send_id = uuid.uuid4().hex[:16]
        try:
            mp4 = Path(render_clip(project, m, settings)).resolve()
            payload = build_payload(project.dir.name, m, _meta(m, streamer, credit), todo, cfg, callback_url, send_id)
            payload.update(file_path=str(mp4), file_name=mp4.name, file_size=mp4.stat().st_size)
            send_to_n8n(cfg.webhook_url, payload)
        except Exception as e:  # noqa: BLE001 — um clipe com problema não impede os outros
            msg = str(e)[:300]
            log.warning("Falha ao enviar %s: %s", m["id"], msg)
            for n in todo:
                store.record(project.dir.name, m["id"], n, status="error", error=msg, send_id=send_id)
            errors.append({"id": m["id"], "error": msg})
            continue
        for n in todo:
            store.record(project.dir.name, m["id"], n, status="sent", send_id=send_id)
        sent += 1
    if on_progress:
        on_progress(1.0, f"{sent} clipe(s) entregues ao n8n")
    return {"sent": sent, "skipped": skipped, "errors": errors, "networks": nets}


def apply_callback(store: PublishStore, body: dict) -> dict:
    """Resultado de uma rede vindo do n8n."""
    if not secrets.compare_digest(str(body.get("token", "")), store.load().token):
        raise PublishError("token inválido")
    net = body.get("network")
    if net not in NETWORKS:
        raise PublishError("rede desconhecida")
    project, clip = str(body.get("project", ""))[:200], str(body.get("clip_id", ""))[:100]
    if not project or not clip:
        raise PublishError("faltou project/clip_id")
    send_id = str(body.get("send_id", ""))[:40]
    current = store.status(project).get(clip, {}).get(net)
    if current and current.get("send_id") and current["send_id"] != send_id:
        return {"ignored": "resultado de um envio antigo"}  # não sobrescreve um envio mais novo
    status = "published" if body.get("status") == "published" else "error"
    fields = {"status": status, "send_id": send_id}
    url = str(body.get("url") or "")[:500]
    if url.startswith("https://"):  # só links https (nada de javascript: no href da tela)
        fields["url"] = url
    if status == "error":
        fields["error"] = str(body.get("error") or "falha sem detalhe")[:500]
    store.record(project, clip, net, **fields)
    return fields
