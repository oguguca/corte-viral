"""Etapa 1 — ingest: baixa (yt-dlp) ou importa o vídeo e extrai o áudio para transcrição."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from corteviral import ffmpeg
from corteviral.config import Settings
from corteviral.project import Project

ProgressFn = Callable[[float, str], None]
AUDIO_RATE = 16000
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# (trecho da mensagem do yt-dlp, explicação em PT-BR)
_FRIENDLY = [
    (("confirm your age", "age-restricted", "age restricted"),
     "vídeo com restrição de idade: o YouTube exige login. Faça login no navegador e rode de novo com "
     "--cookies-from-browser firefox (ou edge/chrome), ou defina download.cookies_from_browser no config.yaml"),
    (("only available for subscribers", "members-only", "join this channel", "subscriber-only"),
     "vídeo exclusivo para inscritos/membros: é preciso login com uma conta que tenha acesso "
     "(use --cookies-from-browser <navegador>)"),
    (("private video", "sign in if you've been granted access"),
     "vídeo privado: é preciso login com uma conta que tenha acesso (use --cookies-from-browser <navegador>)"),
    (("could not copy chrome cookie database", "failed to decrypt with dpapi", "could not find chrome cookies"),
     "não foi possível ler os cookies do Chrome (ele bloqueia o acesso enquanto está aberto). "
     "Feche o Chrome e tente de novo, ou use --cookies-from-browser firefox"),
    (("http error 403",),
     "o site recusou o download (HTTP 403). Atualize o yt-dlp (pip install -U yt-dlp) e tente de novo"),
    (("video unavailable", "this video is not available", "does not exist"),
     "vídeo indisponível ou removido"),
]


class IngestError(Exception):
    pass


class _QuietLogger:
    """Silencia o yt-dlp no terminal; os erros chegam como exceção e são traduzidos."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def is_url(s: str) -> bool:
    return bool(_URL_RE.match(s.strip()))


def _friendly_error(raw: str) -> str:
    low = raw.lower()
    for needles, msg in _FRIENDLY:
        if any(n in low for n in needles):
            return msg
    return raw


def _ydl_base_opts(settings: Settings) -> dict:
    opts: dict = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": _QuietLogger()}
    if settings.download.cookies_from_browser:
        opts["cookiesfrombrowser"] = (settings.download.cookies_from_browser,)
    if settings.download.cookies_file:
        opts["cookiefile"] = str(settings.download.cookies_file)
    return opts


def _probe_url(url: str, settings: Settings) -> dict:
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({**_ydl_base_opts(settings), "skip_download": True}) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise IngestError(f"não foi possível acessar o link: {_friendly_error(str(e))}") from e


def _download(url: str, project: Project, settings: Settings, on_progress: ProgressFn | None) -> Path:
    import yt_dlp

    def hook(d: dict) -> None:
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                on_progress(min(d.get("downloaded_bytes", 0) / total, 1.0), "baixando")

    h = settings.download.max_height
    opts = {
        **_ydl_base_opts(settings),
        "format": f"bv*[height<={h}]+ba/b[height<={h}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(project.dir / "source.%(ext)s"),
        "progress_hooks": [hook],
        "ffmpeg_location": str(Path(ffmpeg.binary("ffmpeg")).parent),
        "concurrent_fragment_downloads": settings.download.concurrent_fragments,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise IngestError(f"falha no download: {_friendly_error(str(e))}") from e
    for d in info.get("requested_downloads") or []:
        fp = d.get("filepath")
        if fp and Path(fp).exists():
            return Path(fp)
    files = [p for p in project.dir.glob("source.*") if p.suffix in (".mp4", ".mkv", ".webm")]
    if not files:
        raise IngestError("download terminou mas o arquivo de vídeo não foi encontrado")
    return files[0]


def _link_local(src: Path, project: Project) -> Path:
    """Hardlink no projeto (mesmo disco); senão, referencia o arquivo original (sem copiar GBs)."""
    dst = project.dir / f"source{src.suffix.lower()}"
    if dst.exists():
        return dst
    try:
        os.link(src, dst)
        return dst
    except OSError:
        return src


def ingest(source: str, settings: Settings, on_progress: ProgressFn | None = None) -> Project:
    """Cria (ou reaproveita) o projeto da fonte e garante vídeo + audio.wav."""
    projects_dir = settings.paths.projects_dir
    info: dict | None = None
    if is_url(source):
        url = source.strip()
        info = _probe_url(url, settings)
        status = info.get("live_status")
        if info.get("is_live") or status in ("is_live", "is_upcoming"):
            raise IngestError("live em andamento: aguarde a live terminar e virar VOD")
        if status == "post_live":
            raise IngestError("a live acabou, mas o VOD ainda está sendo processado pela plataforma. "
                              "Tente de novo em alguns minutos")
        # Chave estável por vídeo: o mesmo VOD com &t=, &list= ou youtu.be cai no mesmo projeto
        if info.get("extractor_key") and info.get("id"):
            key = f"{info['extractor_key'].lower()}:{info['id']}"
        else:
            key = url
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise IngestError(f"arquivo não encontrado: {source}")
        path = path.resolve()
        key = str(path)

    params = {"source": key, "ar": AUDIO_RATE}
    project = Project.find_by_source(projects_dir, key)
    if project and project.is_stage_done("ingest", params) and project.path("audio.wav").exists():
        return project

    if info is not None:
        if project is None:
            project = Project.create(projects_dir, key, info.get("title") or "video")
            project.data["url"] = source.strip()
            project.save()
        video = _download(source.strip(), project, settings, on_progress)
    else:
        if project is None:
            project = Project.create(projects_dir, key, path.stem)
        video = _link_local(path, project)

    if on_progress:
        on_progress(0.0, "extraindo áudio")
    ffmpeg.run(["-y", "-i", str(video), "-vn", "-ac", "1", "-ar", str(AUDIO_RATE),
                "-c:a", "pcm_s16le", str(project.path("audio.wav"))])

    project.data["video_path"] = str(video)
    project.data["duration"] = round(ffmpeg.probe_duration(video), 3)
    project.data["ingest_params"] = params
    project.mark_stage("ingest", params)
    if on_progress:
        on_progress(1.0, "ingest concluído")
    return project
