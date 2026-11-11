"""Mini-vídeo do trecho para a IA assistir (baixa resolução, 1 quadro por segundo, áudio mono)."""
from __future__ import annotations

from pathlib import Path

from corteviral import ffmpeg

MAX_BYTES = 4_000_000  # envio inline ao Gemini; acima disso refaz em 240p


def mini_window(moment: dict, duration: float, margin: float) -> tuple[float, float]:
    start = max(0.0, float(moment["start"]) - margin)
    end = min(float(duration) if duration else float(moment["end"]) + margin, float(moment["end"]) + margin)
    return round(start, 3), round(end, 3)


def _encode(video: Path, start: float, end: float, out: Path, height: int) -> None:
    ffmpeg.run(["-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(video),
                "-vf", f"fps=1,scale=-2:{height}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
                "-c:a", "aac", "-b:a", "48k", "-ac", "1", "-movflags", "+faststart", str(out)])


def make_mini_clip(video: Path, start: float, end: float, out: Path, height: int = 360) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _encode(video, max(0.0, start), end, out, height)
    if out.stat().st_size > MAX_BYTES and height > 240:
        _encode(video, max(0.0, start), end, out, 240)
    return out
