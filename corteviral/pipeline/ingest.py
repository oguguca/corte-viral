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


class IngestError(Exception):
    pass


def is_url(s: str) -> bool:
    return bool(_URL_RE.match(s.strip()))


def _probe_url(url: str) -> dict:
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise IngestError(f"não foi possível acessar o link: {e}") from e


def _download(url: str, project: Project, on_progress: ProgressFn | None) -> Path:
    import yt_dlp

    def hook(d: dict) -> None:
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                on_progress(min(d.get("downloaded_bytes", 0) / total, 1.0), "baixando")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(project.dir / "source.%(ext)s"),
        "progress_hooks": [hook],
        "ffmpeg_location": str(Path(ffmpeg.binary("ffmpeg")).parent),
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        raise IngestError(f"falha no download: {e}") from e
    files = [p for p in project.dir.glob("source.*") if p.suffix not in (".part", ".ytdl")]
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
    if is_url(source):
        key = source.strip()
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

    if is_url(key):
        if project is None:
            info = _probe_url(key)
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise IngestError("live em andamento: aguarde a live terminar e virar VOD")
            project = Project.create(projects_dir, key, info.get("title") or "video")
        video = _download(key, project, on_progress)
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
