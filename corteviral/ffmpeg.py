"""Wrapper fino sobre os executáveis ffmpeg/ffprobe."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


@lru_cache(maxsize=None)
def binary(name: str = "ffmpeg") -> str:
    """Localiza ffmpeg/ffprobe: variável FFMPEG_DIR, PATH ou atalho do winget.

    O fallback do winget cobre o caso de o app ser aberto por um processo com PATH
    antigo (ex.: logo após a instalação, ou iniciado pelo Electron).
    """
    exe = f"{name}.exe" if os.name == "nt" else name
    env_dir = os.environ.get("FFMPEG_DIR")
    if env_dir and (Path(env_dir) / exe).exists():
        return str(Path(env_dir) / exe)
    found = shutil.which(name)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        link = Path(local) / "Microsoft" / "WinGet" / "Links" / exe
        if link.exists():
            return str(link)
        for cand in (Path(local) / "Microsoft" / "WinGet" / "Packages").glob(f"Gyan.FFmpeg*/*/bin/{exe}"):
            return str(cand)
    raise FFmpegError(f"{name} não encontrado. Instale com: winget install -e --id Gyan.FFmpeg")


def run(args: list[str]) -> None:
    """Executa o ffmpeg com os argumentos dados; levanta FFmpegError com o stderr."""
    cmd = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.strip() or f"ffmpeg saiu com código {proc.returncode}")


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [binary("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.strip() or "ffprobe falhou")
    return float(json.loads(proc.stdout)["format"]["duration"])
