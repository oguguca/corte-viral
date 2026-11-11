"""Configuração do Corte Viral: padrões + sobrescritas via config.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_PATH = Path("config.yaml")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class WhisperSettings(_Strict):
    model: str = "medium"
    device: Literal["cuda", "cpu", "auto"] = "cuda"
    compute_type: str = "int8"
    language: str = "auto"
    beam_size: int = 5
    vad_filter: bool = True
    cpu_fallback_model: str = "small"


class PathsSettings(_Strict):
    projects_dir: Path = Path("projects")
    models_dir: Path = Path("models")


class DownloadSettings(_Strict):
    # Navegador logado para vídeos com restrição de idade/login: firefox | edge | chrome | brave | opera
    cookies_from_browser: str | None = None
    # Alternativa: arquivo cookies.txt (formato Netscape) exportado do navegador
    cookies_file: Path | None = None
    max_height: int = 1080
    concurrent_fragments: int = 4


class Settings(_Strict):
    paths: PathsSettings = Field(default_factory=PathsSettings)
    download: DownloadSettings = Field(default_factory=DownloadSettings)
    whisper: WhisperSettings = Field(default_factory=WhisperSettings)


def load_settings(path: Path | None = None) -> Settings:
    """Lê o YAML de configuração; se não existir, devolve os padrões."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        return Settings()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(data)
