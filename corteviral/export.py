"""Exportação em lote: MP4 final de cada clipe aprovado + .txt com os textos de cada rede."""
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
LABELS = {"tiktok": "TIKTOK", "shorts": "YOUTUBE SHORTS", "reels": "INSTAGRAM REELS", "kwai": "KWAI"}
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
    return moment.get("meta") or fallback_posts(moment, streamer, credit)


def posts_txt(moment: dict, streamer: str | None = None, credit: str | None = None) -> str:
    meta = _meta(moment, streamer, credit)
    parts = [f"GANCHO (texto no vídeo): {meta.get('hook', '')}", ""]
    for plat, label in LABELS.items():
        post = meta["posts"].get(plat) or {}
        parts += [f"=== {label} ===", f"Título: {post.get('title', '')}", "",
                  post.get("description", ""), "", " ".join(post.get("hashtags", [])), ""]
    return "\n".join(parts).strip() + "\n"


def _history(settings: Settings) -> HistoryStore:
    return HistoryStore(Path(settings.paths.data_dir) / "history.json")


def _approved(project: Project) -> list[dict]:
    from corteviral.pipeline.analyze import load_moments
    data = load_moments(project) or {}
    return sorted([m for m in data.get("moments", []) if m.get("status") == "approved"], key=lambda m: -m["score"])


def _source(project: Project) -> str:
    return project.data.get("source") or str(project.dir)


def export_check(project: Project, settings: Settings) -> dict:
    """O que vai acontecer ao exportar (sem renderizar nada)."""
    preset = load_preset(settings.render.presets_dir, project.data.get("streamer"))
    hist = _history(settings)
    moments = _approved(project)
    dups = [{"id": m["id"], "previous_file": hit[0]["file"]}
            for m in moments if (hit := hist.overlaps(_source(project), m["start"], m["end"]))]
    return {"approved": len(moments), "unauthorized": not (preset and preset.authorized),
            "duplicates": dups, "folder": str(Path(settings.paths.exports_dir) / project.dir.name)}


def export_approved(project: Project, settings: Settings, on_progress: ProgressFn | None = None) -> dict:
    check = export_check(project, settings)
    preset = load_preset(settings.render.presets_dir, project.data.get("streamer"))
    credit = preset.credit_handle if preset else None
    streamer = project.data.get("streamer")
    folder = Path(check["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    moments = _approved(project)
    files, errors, used = [], [], set()
    for i, m in enumerate(moments, 1):
        if on_progress:
            on_progress((i - 1) / max(1, len(moments)), f"exportando clipe {i} de {len(moments)}")
        meta = _meta(m, streamer, credit)
        title = (meta["posts"].get("tiktok") or {}).get("title") or meta.get("hook") or m["id"]
        base = f"{i:02d}-{slugify(title, 50)}"
        name, n = base, 2
        while name in used:
            name, n = f"{base}-{n}", n + 1
        used.add(name)
        mp4, txt = folder / f"{name}.mp4", folder / f"{name}.txt"
        tmp = folder / f".{name}.part.mp4"
        try:
            clip = render_clip(project, m, settings)
            shutil.copyfile(clip, tmp)
            os.replace(tmp, mp4)
            txt.write_text(posts_txt(m, streamer, credit), encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — um clipe com problema não impede os outros
            log.warning("Falha ao exportar %s: %s", m["id"], e)
            for f in (tmp, mp4, txt):
                f.unlink(missing_ok=True)
            errors.append({"id": m["id"], "error": str(e)[:300]})
            continue
        _history(settings).add(_source(project), m["start"], m["end"], str(mp4))
        files.append({"id": m["id"], "mp4": mp4, "txt": txt})
    if on_progress:
        on_progress(1.0, f"{len(files)} clipes exportados")
    return {"folder": folder, "files": files, "errors": errors, "duplicates": check["duplicates"],
            "unauthorized": check["unauthorized"]}
