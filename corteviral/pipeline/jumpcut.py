"""Cortes de ritmo para Shorts (regras de retenção do YouTube): tira pausas e alterna o enquadramento.

- Pausas: detectadas no ÁUDIO (silencedetect), não na transcrição, para não cortar risada ou grito que
  não viram palavra. Silêncio no começo some inteiro (gancho imediato); no fim fica só um respiro.
- Enquadramento: os pedaços que sobram são divididos em trechos de 2–4 s e alternam entre o quadro
  normal e um zoom leve (punch-in), o que dá a sensação de corte de câmera.
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

from corteviral import ffmpeg

MIN_KEEP_TOTAL = 1.0   # s: menos que isso sobrando = algo errado; mantém o clipe inteiro
MAX_PIECE = 4.0        # s: trecho máximo sem trocar de enquadramento

Segment = tuple[float, float]


def parse_silences(log: str, offset: float, end: float) -> list[Segment]:
    """Silêncios (tempos absolutos) a partir da saída do silencedetect de um trecho que começa em offset."""
    out, cur = [], None
    for kind, val in re.findall(r"silence_(start|end): (-?[\d.]+)", log):
        t = offset + float(val)
        if kind == "start":
            cur = max(offset, t)
        elif cur is not None:
            out.append((round(cur, 3), round(t, 3)))
            cur = None
    if cur is not None:
        out.append((round(cur, 3), round(end, 3)))
    return out


def _af(audio: Path, start: float, end: float, af: str) -> str | None:
    cmd = [ffmpeg.binary("ffmpeg"), "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
           "-i", str(audio), "-vn", "-af", af, "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.stderr if proc.returncode == 0 else None


def silences(audio: Path, start: float, end: float, rel_db: float, min_len: float) -> list[Segment]:
    """Silêncios do trecho, relativos ao volume médio do próprio clipe (live tem som de fundo constante:
    um limite fixo em dB não acha as pausas). Sem análise de áudio, devolve [] e o clipe sai sem cortes."""
    vol = _af(audio, start, end, "volumedetect")
    found = re.search(r"mean_volume: (-?[\d.]+) dB", vol or "")
    if not found:
        return []
    noise = float(found.group(1)) + rel_db
    log = _af(audio, start, end, f"silencedetect=noise={noise:.1f}dB:d={min_len}")
    return parse_silences(log, start, end) if log else []


def keep_segments(start: float, end: float, silent: list[Segment], pad: float, min_silence: float,
                  tail: float = 0.3) -> list[Segment]:
    """Trechos mantidos, em segundos desde o início do clipe."""
    dur = end - start
    cuts: list[Segment] = []
    for s, e in silent:
        s, e = max(0.0, s - start), min(dur, e - start)
        if e - s < min_silence:
            continue
        if s <= 0.01:            # silêncio no começo: corta tudo (gancho imediato)
            cuts.append((0.0, max(0.0, e - pad)))
        elif e >= dur - 0.01:    # silêncio no fim: deixa só um respiro
            cuts.append((s + tail, dur))
        else:
            cuts.append((s + pad, e - pad))
    keep, pos = [], 0.0
    for s, e in sorted(cuts):
        if s > pos:
            keep.append((round(pos, 3), round(s, 3)))
        pos = max(pos, e)
    if pos < dur:
        keep.append((round(pos, 3), round(dur, 3)))
    keep = [(s, e) for s, e in keep if e - s > 0.05]
    if out_duration(keep) < MIN_KEEP_TOTAL:
        return [(0.0, round(dur, 3))]
    return keep


def out_duration(segs: list[Segment]) -> float:
    return sum(e - s for s, e in segs)


def select_expr(segs: list[Segment]) -> str:
    return "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in segs)


def remap_words(words: list[dict], segs: list[Segment]) -> list[dict]:
    """Palavras (tempos relativos ao clipe) no tempo do vídeo já cortado; as que caem num corte saem."""
    out = []
    for w in words:
        cum = 0.0
        for s, e in segs:
            if s <= w["start"] < e:
                out.append({**w, "start": round(cum + w["start"] - s, 3), "end": round(cum + min(w["end"], e) - s, 3)})
                break
            cum += e - s
    return out


def zoom_windows(segs: list[Segment], every: float) -> list[Segment]:
    """Janelas (tempo do vídeo cortado) com zoom: trechos de até 4 s, alternando normal/zoom."""
    pieces, cum = [], 0.0
    for s, e in segs:
        length = e - s
        n = max(1, round(length / every), math.ceil(length / MAX_PIECE))
        for i in range(n):
            pieces.append((cum + length * i / n, cum + length * (i + 1) / n))
        cum += length
    return [(round(a, 3), round(b, 3)) for i, (a, b) in enumerate(pieces) if i % 2 == 1]


def remap_points(points: list[tuple[float, float]], segs: list[Segment]) -> list[tuple[float, float]]:
    """Pontos (t relativo ao clipe, valor) no tempo do vídeo já cortado; os que caem num corte saem."""
    out = []
    for t, v in points:
        cum = 0.0
        for s, e in segs:
            if s <= t < e:
                out.append((round(cum + t - s, 3), v))
                break
            cum += e - s
    return out
