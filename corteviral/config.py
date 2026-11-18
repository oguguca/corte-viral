"""Configuração do Corte Viral: padrões + sobrescritas via config.yaml."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # auto: GPU se houver; senão Groq (grátis, GROQ_API_KEY no .env); senão CPU
    engine: Literal["auto", "local", "groq"] = "auto"
    groq_model: str = "whisper-large-v3-turbo"
    groq_chunk_seconds: int = Field(default=600, ge=60, le=1200)  # pedaços enviados ao Groq
    # Cota grátis aproximada (s de áudio/dia); confira em console.groq.com → Settings → Limits
    groq_daily_audio_seconds: int = Field(default=28800, ge=60)


class PathsSettings(_Strict):
    projects_dir: Path = Path("projects")
    models_dir: Path = Path("models")
    exports_dir: Path = Path("exports")  # clipes prontos para postar
    data_dir: Path = Path("data")  # sessão do YouTube do app, preferências locais
    n8n_dir: Path = Path("tools/n8n")  # n8n local (postagem automática); dados dele em data/n8n


class DownloadSettings(_Strict):
    # Navegador logado para vídeos com restrição de idade/login: firefox | edge | chrome | brave | opera
    cookies_from_browser: str | None = None
    # Alternativa: arquivo cookies.txt (formato Netscape) exportado do navegador
    cookies_file: Path | None = None
    max_height: int = 1080
    concurrent_fragments: int = 4
    # Só um trecho do vídeo (segundos no vídeo original): baixa/corta e transcreve apenas essa parte
    section_start: float | None = None
    section_end: float | None = None


class AnalysisSettings(_Strict):
    profile: Literal["viral", "monetizacao"] = "viral"
    n_clips: int | None = None   # None: a IA decide (todo momento com nota >= min_score)
    min_score: int = Field(default=70, ge=0, le=100)   # "vale postar" na régua da IA
    max_auto_clips: int = Field(default=30, ge=1, le=100)
    max_candidates: int = 60
    # termos extras de reação (regex -> peso), somados ao léxico padrão
    extra_reactions: dict[str, float] = Field(default_factory=dict)
    zscore_window: int = 60
    fetch_chat: bool = True
    # duração personalizada do clipe (s); vazio = faixa do perfil (viral 15–60, monetização 61–120)
    min_seconds: float | None = Field(default=None, ge=5, le=180)
    max_seconds: float | None = Field(default=None, ge=5, le=180)

    @model_validator(mode="after")
    def _check_range(self):
        if self.min_seconds is not None and self.max_seconds is not None:
            if self.max_seconds - self.min_seconds < 5:
                raise ValueError("a duração máxima precisa ser pelo menos 5 s maior que a mínima")
        return self


class LLMSettings(_Strict):
    provider: Literal["none", "gemini", "anthropic"] = "gemini"
    model: str | None = None  # None = padrão do provedor
    batch_size: int = 8
    timeout: int = 90
    # O Google não informa a cota grátis pela API: confira em AI Studio → Rate limits e ajuste aqui
    daily_request_limit: int = 1000
    daily_token_limit: int = 1_000_000


class DeepSettings(_Strict):
    enabled: bool = True          # IA assiste o vídeo dos melhores candidatos (só Gemini)
    top_k: int = 15
    margin: float = 15.0          # s de folga antes/depois do momento no mini-vídeo
    model: str | None = None      # None = mesmo modelo do llm
    weights: dict[str, float] = Field(default_factory=lambda: {
        "gancho": 0.25, "emocao": 0.25, "visual": 0.15, "final": 0.20, "contexto": 0.15})
    max_examples: int = 6
    workers: int = 3              # trechos assistidos em paralelo


class LiveSettings(_Strict):
    """Cortes de live ao vivo: grava em partes e processa janelas com sobreposição."""
    window_seconds: int = Field(default=300, ge=60, le=1800)
    overlap_seconds: int = Field(default=60, ge=0, le=300)
    max_retries: int = Field(default=20, ge=1, le=200)       # quedas seguidas sem gravar nada
    retry_wait: int = Field(default=10, ge=1, le=60)         # s; dobra até 60
    max_clips_per_window: int = Field(default=3, ge=1, le=20)
    review_per_window: int = Field(default=3, ge=0, le=20)   # trechos que a IA assiste por janela


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
    # Ritmo de Shorts (retenção do YouTube): corta pausas e alterna o enquadramento
    jump_cuts: bool = True
    silence_rel_db: float = Field(default=-8.0, ge=-20, le=-3)  # dB abaixo do volume médio do clipe = pausa
    silence_min: float = Field(default=0.35, ge=0.15, le=3.0)  # s: pausa mínima para cortar
    cut_pad: float = Field(default=0.1, ge=0.0, le=0.5)        # s de respiro em cada lado do corte
    punch_zoom: float = Field(default=1.12, ge=1.0, le=1.5)    # 1.0 desliga o zoom alternado
    zoom_every: float = Field(default=3.0, ge=2.0, le=4.0)     # s entre trocas de enquadramento


class CaptionStyle(_Strict):
    enabled: bool = True
    font: str = Field(default="Arial Black", pattern="^[A-Za-z0-9 ]{1,40}$")  # fonte do Windows
    size: int = Field(default=90, ge=30, le=160)     # px num vídeo 1080x1920
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight: str = Field(default="#FFE600", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline: int = Field(default=6, ge=0, le=20)
    shadow: int = Field(default=2, ge=0, le=10)
    uppercase: bool = True
    max_words: int = Field(default=3, ge=1, le=6)
    max_chars: int = Field(default=20, ge=6, le=40)
    position: float = Field(default=0.70, ge=0.0, le=1.0)  # altura do centro da legenda (fração)
    pop: bool = True
    hook: bool = True      # gancho escrito nos 3 primeiros segundos
    credit: bool = True    # "@streamer" discreto no canto (quando o preset tem credit_handle)


class Settings(_Strict):
    paths: PathsSettings = Field(default_factory=PathsSettings)
    download: DownloadSettings = Field(default_factory=DownloadSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    render: RenderSettings = Field(default_factory=RenderSettings)
    deep: DeepSettings = Field(default_factory=DeepSettings)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    whisper: WhisperSettings = Field(default_factory=WhisperSettings)
    live: LiveSettings = Field(default_factory=LiveSettings)


def load_settings(path: Path | None = None) -> Settings:
    """Lê o YAML de configuração; se não existir, devolve os padrões."""
    path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        return Settings()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(data)
