"""Layout do vertical por streamer: regiões da câmera e da gameplay (normalizadas 0–1)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator

from corteviral.config import Settings
from corteviral.project import Project
from corteviral.utils import slugify, write_json_atomic
from corteviral.vision.detect import analyze_layout

Layout = Literal["split", "face-follow", "fallback"]


def _region(v):
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        raise ValueError("região precisa de 4 números [x, y, largura, altura]")
    x, y, w, h = (float(a) for a in v)
    if w <= 0.01 or h <= 0.01:
        raise ValueError("região muito pequena")
    w, h = min(w, 1.0), min(h, 1.0)
    x = min(max(x, 0.0), 1.0 - w)
    y = min(max(y, 0.0), 1.0 - h)
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


class LayoutPreset(BaseModel):
    layout: Layout = "split"
    facecam: list[float] | None = None
    gameplay: list[float] | None = None
    credit_handle: str = ""
    authorized: bool = False

    @field_validator("facecam", "gameplay", mode="before")
    @classmethod
    def _check_region(cls, v):
        return _region(v)


def _file(presets_dir: Path, streamer: str) -> Path:
    return Path(presets_dir) / f"{slugify(streamer)}.json"


def load_preset(presets_dir: Path, streamer: str | None) -> LayoutPreset | None:
    if not streamer:
        return None
    f = _file(presets_dir, streamer)
    if not f.exists():
        return None
    return LayoutPreset.model_validate(json.loads(f.read_text(encoding="utf-8")))


def save_preset(presets_dir: Path, streamer: str, preset: LayoutPreset) -> Path:
    f = _file(presets_dir, streamer)
    f.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(f, preset.model_dump())
    return f


def detected_layout(project: Project, settings: Settings) -> dict:
    """Detecção automática (roda uma vez por projeto e fica salva)."""
    det = project.data.get("layout_detect")
    if det is None:
        det = analyze_layout(Path(project.data["video_path"]), float(project.data.get("duration", 0)),
                             Path(settings.paths.models_dir))
        project.data["layout_detect"] = det
        project.save()
    return det


def resolve_layout(project: Project, settings: Settings) -> LayoutPreset:
    preset = load_preset(settings.render.presets_dir, project.data.get("streamer"))
    if preset:
        return preset
    det = detected_layout(project, settings)
    if det.get("cams"):
        return LayoutPreset(layout="split", facecam=det["cams"][0]["rect"], gameplay=det.get("gameplay"))
    return LayoutPreset(layout="fallback", gameplay=det.get("gameplay"))
