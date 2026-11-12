"""Exportação em lote: uma pasta por clipe aprovado, com o MP4 final e um .txt por rede (TikTok, YouTube
Shorts, Instagram Reels, Kwai) no formato do formulário de envio de cada uma."""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from corteviral.config import Settings
from corteviral.metadata import fallback_posts
from corteviral.pipeline.render import render_clip
from corteviral.presets import load_preset
from corteviral.project import Project
from corteviral.utils import slugify, write_json_atomic

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]
FILES = {"tiktok": ("TikTok.txt", "TIKTOK"), "shorts": ("YouTube Shorts.txt", "YOUTUBE SHORTS"),
         "reels": ("Instagram Reels.txt", "INSTAGRAM REELS"), "kwai": ("Kwai.txt", "KWAI")}
OVERLAP = 0.5
_LOCK = threading.Lock()


class HistoryStore:
    """Trechos já exportados (para avisar sobre duplicatas)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    def overlaps(self, source: str, start: float, end: float) -> list[dict]:
        out = []
        for e in self._load():
            if e["source"] != source:
                continue
            inter = max(0.0, min(end, e["end"]) - max(start, e["start"]))
            shorter = min(end - start, e["end"] - e["start"]) or 1e-6
            if inter / shorter > OVERLAP:
                out.append(e)
        return out

    def add(self, source: str, start: float, end: float, file: str) -> None:
        with _LOCK:
            items = self._load()
            items.append({"source": source, "start": start, "end": end, "file": file, "ts": time.time()})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.path, items)


def _meta(moment: dict, streamer: str | None, credit: str | None) -> dict:
    """Textos salvos, completando o que estiver vazio com os textos simples (nunca exporta título em branco)."""
    fb = fallback_posts(moment, streamer, credit)
    meta = moment.get("meta") or {}
    posts = {}
    for plat, fpost in fb["posts"].items():
        post = (meta.get("posts") or {}).get(plat) or {}
        posts[plat] = {k: (post.get(k) or v) for k, v in fpost.items()}  # inclui as tags do YouTube
    return {"hook": meta.get("hook") or fb["hook"], "ai": bool(meta.get("ai")), "posts": posts}


def _block(*parts: str) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def platform_texts(moment: dict, streamer: str | None = None, credit: str | None = None) -> dict[str, str]:
    """Um .txt por rede, no formato do formulário de envio de cada uma (nome do arquivo → texto)."""
    meta = _meta(moment, streamer, credit)
    out = {}
    for plat, (fname, label) in FILES.items():
        post = meta["posts"].get(plat) or {}
        title, desc = post.get("title", ""), post.get("description", "")
        tags = " ".join(post.get("hashtags", []))
        if plat == "shorts":  # YouTube: título, descrição e tags são campos separados
            body = (f"{label}\n\n"
                    f"TÍTULO (campo \"Título\", até 100 caracteres):\n{title}\n\n"
                    f"DESCRIÇÃO (campo \"Descrição\"; as 3 primeiras hashtags aparecem em cima do título):\n"
                    f"{_block(desc, tags)}\n\n"
                    f"TAGS (em \"Mostrar mais\" › \"Tags\", já separadas por vírgula):\n"
                    f"{', '.join(post.get('tags', []))}\n")
        else:  # TikTok, Reels e Kwai: um campo só, então vai tudo junto e pronto para colar
            field = "Legenda" if plat == "reels" else "Descrição"
            body = f"{label} — copie tudo abaixo da linha e cole no campo \"{field}\"\n{'-' * 40}\n{_block(title, desc, tags)}\n"
        out[fname] = body
    return out


def _history(settings: Settings) -> HistoryStore:
    return HistoryStore(Path(settings.paths.data_dir) / "history.json")


def _approved(project: Project) -> list[dict]:
    from corteviral.pipeline.analyze import load_moments
    data = load_moments(project) or {}
    by_id = {}
    for m in data.get("moments", []) + data.get("all", []):  # aprovados fora da seleção atual também
        if m.get("status") == "approved" and m["id"] not in by_id:
            by_id[m["id"]] = m
    return sorted(by_id.values(), key=lambda m: -m["score"])


def _chosen(project: Project, ids: list[str] | None) -> list[dict]:
    """Aprovados (exportação em lote) ou só os clipes pedidos, aprovados ou não (exportar um clipe direto)."""
    if ids is None:
        return _approved(project)
    from corteviral.pipeline.analyze import load_moments
    data = load_moments(project) or {}
    by_id = {}
    for m in data.get("moments", []) + data.get("all", []):
        if m["id"] in ids and m["id"] not in by_id:
            by_id[m["id"]] = m
    return sorted(by_id.values(), key=lambda m: -m["score"])


def _source(project: Project) -> str:
    return project.data.get("source") or str(project.dir)


def export_check(project: Project, settings: Settings, ids: list[str] | None = None) -> dict:
    """O que vai acontecer ao exportar (sem renderizar nada)."""
    preset = load_preset(settings.render.presets_dir, project.data.get("streamer"))
    hist = _history(settings)
    moments = _chosen(project, ids)
    dups = [{"id": m["id"], "previous_file": hit[0]["file"]}
            for m in moments if (hit := hist.overlaps(_source(project), m["start"], m["end"]))]
    return {"approved": len(moments), "unauthorized": not (preset and preset.authorized),
            "duplicates": dups, "folder": str(Path(settings.paths.exports_dir) / project.dir.name)}


def export_approved(project: Project, settings: Settings, on_progress: ProgressFn | None = None,
                    ids: list[str] | None = None) -> dict:
    check = export_check(project, settings, ids)
    preset = load_preset(settings.render.presets_dir, project.data.get("streamer"))
    credit = preset.credit_handle if preset else None
    streamer = project.data.get("streamer")
    folder = Path(check["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    moments = _chosen(project, ids)
    files, errors, used = [], [], set()
    for i, m in enumerate(moments, 1):
        if on_progress:
            on_progress((i - 1) / max(1, len(moments)), f"exportando clipe {i} de {len(moments)}")
        meta = _meta(m, streamer, credit)
        title = (meta["posts"].get("tiktok") or {}).get("title") or meta.get("hook") or m["id"]
        base = f"{i:02d}-{slugify(title, 50)}"
        name, n = base, 2
        while name in used or (folder / name).exists() or (folder / f"{name}.mp4").exists():
            name, n = f"{base}-{n}", n + 1  # nunca sobrescreve uma exportação anterior
        used.add(name)
        # uma pasta por clipe: o MP4 + um .txt por rede. Montada numa pasta temporária e renomeada no fim
        # (ou tudo, ou nada: falha no meio não deixa pasta pela metade).
        clip_dir, tmp_dir = folder / name, folder / f".{name}.part"
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir.mkdir()
            clip = render_clip(project, m, settings)
            shutil.copyfile(clip, tmp_dir / f"{name}.mp4")
            for fname, text in platform_texts(m, streamer, credit).items():
                (tmp_dir / fname).write_text(text, encoding="utf-8")
            os.replace(tmp_dir, clip_dir)
        except Exception as e:  # noqa: BLE001 — um clipe com problema não impede os outros
            log.warning("Falha ao exportar %s: %s", m["id"], e)
            shutil.rmtree(tmp_dir, ignore_errors=True)  # só apaga o que esta tentativa criou
            errors.append({"id": m["id"], "error": str(e)[:300]})
            continue
        mp4 = clip_dir / f"{name}.mp4"
        _history(settings).add(_source(project), m["start"], m["end"], str(mp4))
        files.append({"id": m["id"], "mp4": mp4, "dir": clip_dir})
    if on_progress:
        on_progress(1.0, f"{len(files)} clipes exportados")
    return {"folder": folder, "files": files, "errors": errors, "duplicates": check["duplicates"],
            "unauthorized": check["unauthorized"]}
