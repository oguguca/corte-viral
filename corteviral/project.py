"""Projeto em disco: uma pasta por vídeo, com project.json e cache por etapa."""
from __future__ import annotations

import copy
import json
import threading
from datetime import date
from pathlib import Path

from corteviral.utils import params_hash, slugify, write_json_atomic

STAGES = [
    "ingest", "transcribe", "chat", "signals", "scoring",
    "boundaries", "layout", "captions", "metadata", "export",
]
PROJECT_FILE = "project.json"
_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def _lock(dir: Path) -> threading.Lock:
    key = str(Path(dir).resolve()).lower()
    with _GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class Project:
    def __init__(self, dir: Path, data: dict):
        self.dir = Path(dir)
        self.data = data
        self._base = copy.deepcopy(data)  # como estava no disco quando carregamos

    # --- criação / busca -------------------------------------------------
    @classmethod
    def create(cls, projects_dir: Path, source: str, title: str) -> "Project":
        projects_dir = Path(projects_dir)
        projects_dir.mkdir(parents=True, exist_ok=True)
        base = f"{slugify(title)}-{date.today():%Y%m%d}"
        dir = projects_dir / base
        n = 2
        while dir.exists():
            dir = projects_dir / f"{base}-{n}"
            n += 1
        dir.mkdir()
        project = cls(dir, {"source": source, "title": title, "stages": {}})
        project.save()
        return project

    @classmethod
    def find_by_source(cls, projects_dir: Path, source: str) -> "Project | None":
        projects_dir = Path(projects_dir)
        if not projects_dir.exists():
            return None
        for pj in sorted(projects_dir.glob(f"*/{PROJECT_FILE}")):
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("source") == source:
                return cls(pj.parent, data)
        return None

    @classmethod
    def load(cls, dir: Path) -> "Project":
        dir = Path(dir)
        data = json.loads((dir / PROJECT_FILE).read_text(encoding="utf-8"))
        return cls(dir, data)

    def save(self) -> None:
        """Salva sem apagar o que outro escritor (ex.: a interface) salvou enquanto isto estava aberto.

        Merge de 3 vias por chave: se esta instância não mexeu numa chave e o disco mudou, vale o disco.
        """
        f = self.dir / PROJECT_FILE
        with _lock(self.dir):
            try:
                disk = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                disk = {}
            merged = dict(self.data)
            for key, value in disk.items():
                mine_changed = key in self.data and self.data[key] != self._base.get(key, object())
                deleted_by_me = key not in self.data and key in self._base
                if key not in self.data and not deleted_by_me:
                    merged[key] = value
                elif key in self.data and not mine_changed and value != self._base.get(key):
                    merged[key] = value
            self.data = merged
            write_json_atomic(f, self.data)
            self._base = copy.deepcopy(self.data)

    # --- cache por etapa -------------------------------------------------
    def is_stage_done(self, stage: str, params: dict) -> bool:
        return self.data["stages"].get(stage) == params_hash(params)

    def mark_stage(self, stage: str, params: dict) -> None:
        if stage not in STAGES:
            raise ValueError(f"etapa desconhecida: {stage}")
        self.data["stages"][stage] = params_hash(params)
        self.save()

    def invalidate_from(self, stage: str) -> None:
        """Remove a etapa dada e todas as seguintes do cache."""
        for s in STAGES[STAGES.index(stage):]:
            self.data["stages"].pop(s, None)
        self.save()

    def path(self, name: str) -> Path:
        return self.dir / name
