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
    data_dir: Path = Path("data")  # sessão do YouTube do app, preferências locais


class DownloadSettings(_Strict):
    # Navegador logado para vídeos com restrição de idade/login: firefox | edge | chrome | brave | opera
    cookies_from_browser: str | None = None
    # Alternativa: arquivo cookies.txt (formato Netscape) exportado do navegador
    cookies_file: Path | None = None
    max_height: int = 1080
    concurrent_fragments: int = 4


class AnalysisSettings(_Strict):
    profile: Literal["viral", "monetizacao"] = "viral"
    n_clips: int = 10
    max_candidates: int = 60
    # termos extras de reação (regex -> peso), somados ao léxico padrão
    extra_reactions: dict[str, float] = Field(default_factory=dict)
    zscore_window: int = 60
    fetch_chat: bool = True


class LLMSettings(_Strict):
    provider: Literal["none", "gemini", "anthropic"] = "gemini"
    model: str | None = None  # None = padrão do provedor
    batch_size: int = 8
    timeout: int = 90
    # O Google não informa a cota grátis pela API: confira em AI Studio → Rate limits e ajuste aqui
    daily_request_limit: int = 1000
    daily_token_limit: int = 1_000_000


class RenderSettings(_Strict):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    facecam_ratio: float = 0.30
    divider_px: int = 6
    divider_color: str = "#000000"
    encoder: str = "h264_nvenc"   # cai para libx264 se a GPU não puder codificar
    cq: int = 23
    audio_bitrate: str = "160k"
    presets_dir: Path = Path("presets/streamers")


class Settings(_Strict):
    paths: PathsSettings = Field(default_factory=PathsSettings)
    download: DownloadSettings = Field(default_factory=DownloadSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    render: RenderSettings = Field(default_factory=RenderSettings)
    whisper: WhisperSettings = Field(default_factory=WhisperSettings)


def load_settings(path: Path | None = None) -> Settings:
    """Lê o YAML de configuração; se não existir, devolve os padrões."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        return Settings()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(data)
