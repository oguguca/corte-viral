"""Projeto em disco: uma pasta por vídeo, com project.json e cache por etapa."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from corteviral.utils import params_hash, slugify, write_json_atomic

STAGES = [
    "ingest", "transcribe", "chat", "signals", "scoring",
    "boundaries", "layout", "captions", "metadata", "export",
]
PROJECT_FILE = "project.json"


class Project:
    def __init__(self, dir: Path, data: dict):
        self.dir = Path(dir)
        self.data = data

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
        write_json_atomic(self.dir / PROJECT_FILE, self.data)

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
