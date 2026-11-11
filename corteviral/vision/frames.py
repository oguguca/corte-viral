"""Captura de quadros do vídeo via ffmpeg → arrays numpy BGR."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from corteviral import ffmpeg


def video_size(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        [ffmpeg.binary("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    st = json.loads(out)["streams"][0]
    return int(st["width"]), int(st["height"])


def grab(video: Path, times: list[float], width: int = 640) -> list[np.ndarray]:
    """Um quadro por tempo (redimensionado para `width`); tempos fora do vídeo são ignorados."""
    sw, sh = video_size(video)
    height = max(2, round(sh * width / sw / 2) * 2)
    frames = []
    for t in times:
        raw = subprocess.run(
            [ffmpeg.binary("ffmpeg"), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
            capture_output=True,
        ).stdout
        if len(raw) == width * height * 3:
            frames.append(np.frombuffer(raw, np.uint8).reshape(height, width, 3).copy())
    return frames
