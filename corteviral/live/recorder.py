"""Gravador da live: yt-dlp em subprocesso gravando do agora em partes MPEG-TS (legíveis enquanto crescem)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from corteviral import ffmpeg
from corteviral.config import Settings


class Recorder:
    def __init__(self, url: str, live_dir: Path, settings: Settings, popen=subprocess.Popen):
        self.url = url
        self.dir = Path(live_dir)
        self.settings = settings
        self._popen = popen
        self.proc = None

    def command(self, out: Path) -> list[str]:
        d = self.settings.download
        cmd = [sys.executable, "-m", "yt_dlp", "--quiet", "--no-warnings", "--no-part", "--hls-use-mpegts",
               "-f", f"bv*[height<={d.max_height}]+ba/b[height<={d.max_height}]/b", "--js-runtimes", "node",
               "--ffmpeg-location", str(Path(ffmpeg.binary("ffmpeg")).parent), "-o", str(out)]
        if d.cookies_from_browser:
            cmd += ["--cookies-from-browser", d.cookies_from_browser]
        if d.cookies_file:
            cmd += ["--cookies", str(d.cookies_file)]
        return cmd + [self.url]  # sem --live-from-start: grava do agora

    def start(self, n: int) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        out = self.dir / f"part-{n:03d}.ts"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = self._popen(self.command(out), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=flags)
        return out

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if not self.running():
            return
        if os.name == "nt":  # o yt-dlp abre um ffmpeg filho: sem /T ele fica órfão gravando para sempre
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
        else:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            self.proc.kill()

    @staticmethod
    def resolve(path: Path) -> Path:
        """Arquivo real da parte: com vídeo e áudio separados o yt-dlp acrescenta a extensão (.ts.mp4);
        o conteúdo continua MPEG-TS."""
        path = Path(path)
        if path.exists():
            return path
        found = sorted(path.parent.glob(path.name + ".*"))
        return found[0] if found else path

    @staticmethod
    def recorded(path: Path) -> float:
        """Quanto da parte já foi gravado (s); 0 se ainda não dá para ler."""
        path = Recorder.resolve(path)
        if not path.exists() or path.stat().st_size < 50_000:
            return 0.0
        try:
            return ffmpeg.probe_duration(path)
        except Exception:  # noqa: BLE001 — arquivo crescendo pode estar no meio de um pacote
            return 0.0
