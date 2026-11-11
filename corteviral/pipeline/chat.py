"""Chat gravado da live (YouTube live_chat / Twitch rechat) via yt-dlp → mensagens por segundo."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from corteviral.config import Settings
from corteviral.project import Project

log = logging.getLogger(__name__)


def parse_chat_file(path: Path) -> list[float]:
    """Offsets (s) de cada mensagem. Aceita JSON-lines do YouTube e JSON da Twitch."""
    text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    offsets: list[float] = []
    if not text:
        return offsets
    if text[0] == "[" or (text[0] == "{" and '"comments"' in text[:2000]):
        data = json.loads(text)
        items = data.get("comments", []) if isinstance(data, dict) else data
        for c in items:
            off = c.get("content_offset_seconds")
            if off is not None:
                offsets.append(float(off))
        return sorted(offsets)
    for line in text.splitlines():  # YouTube: uma ação por linha
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ms = (obj.get("replayChatItemAction") or {}).get("videoOffsetTimeMsec")
        if ms is not None:
            offsets.append(int(ms) / 1000)
    return sorted(offsets)


def per_second(offsets: list[float], duration: int) -> list[float]:
    counts = [0] * duration
    for o in offsets:
        i = int(o)
        if 0 <= i < duration:
            counts[i] += 1
    return counts


def fetch_chat(project: Project, settings: Settings) -> list[float] | None:
    """Baixa o chat gravado (se houver) e devolve mensagens por segundo; None se indisponível."""
    url = project.data.get("url")
    if not url or not settings.analysis.fetch_chat:
        return None
    duration = int(project.data.get("duration", 0))
    existing = [p for p in project.dir.glob("chat.*.json")]
    if not existing:
        try:
            import yt_dlp

            from corteviral.pipeline.ingest import _ydl_base_opts

            opts = {**_ydl_base_opts(settings), "skip_download": True, "writesubtitles": True,
                    "subtitleslangs": ["live_chat", "rechat"], "outtmpl": str(project.dir / "chat.%(ext)s")}
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as e:  # noqa: BLE001 — chat é opcional
            log.warning("Chat indisponível: %s", str(e)[:200])
            return None
        existing = [p for p in project.dir.glob("chat.*.json")]
    if not existing:
        return None
    offsets = parse_chat_file(existing[0])
    if len(offsets) < 20:  # chat praticamente vazio não ajuda
        return None
    return per_second(offsets, duration)
