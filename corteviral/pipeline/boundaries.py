"""Etapa — fronteiras naturais: começa e termina em pausas/fim de frase, dentro da duração do perfil."""
from __future__ import annotations

import bisect

from corteviral.models import Transcript

PAUSE = 0.3          # s de silêncio que conta como pausa natural
SEARCH = 10.0        # s que o início/fim pode andar para achar uma fronteira
PROFILE_RANGES = {"viral": (15, 60), "monetizacao": (61, 120)}
_SENTENCE_END = (".", "!", "?", "…")


def _cut_points(tr: Transcript) -> tuple[list[float], list[float]]:
    """Pontos onde dá para começar (após pausa) e terminar (fim de frase ou antes de pausa)."""
    words = tr.words
    starts, ends = [], []
    for i, w in enumerate(words):
        if i == 0 or w.start - words[i - 1].end >= PAUSE:
            starts.append(w.start)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if w.w.endswith(_SENTENCE_END) or nxt is None or nxt.start - w.end >= PAUSE:
            ends.append(w.end)
    return starts, ends


def _last_le(points: list[float], x: float, lo: float) -> float | None:
    i = bisect.bisect_right(points, x) - 1
    return points[i] if i >= 0 and points[i] >= lo else None


def _first_ge(points: list[float], x: float, hi: float) -> float | None:
    i = bisect.bisect_left(points, x)
    return points[i] if i < len(points) and points[i] <= hi else None


def snap_bounds(start: float, end: float, tr: Transcript, min_s: float, max_s: float) -> tuple[float, float]:
    dur = tr.duration or (tr.words[-1].end if tr.words else end)
    if dur <= min_s:
        return 0.0, round(dur, 3)
    starts, ends = _cut_points(tr)

    s = _last_le(starts, start, start - SEARCH)
    s = start if s is None else s
    e = _first_ge(ends, end, end + SEARCH)
    e = end if e is None else e

    if e - s < min_s:  # curto: estende o fim até um ponto natural após o mínimo
        target = s + min_s
        e2 = _first_ge(ends, target, s + max_s)
        e = e2 if e2 is not None else min(target, dur)
        if e - s < min_s:  # bateu no fim do vídeo: recua o início
            s2 = _last_le(starts, e - min_s, max(0.0, e - max_s))
            s = s2 if s2 is not None else max(0.0, e - min_s)
    if e - s > max_s:  # longo: corta no último ponto natural antes do máximo
        e2 = _last_le(ends, s + max_s, s + min_s)
        e = e2 if e2 is not None else s + max_s
    return round(max(0.0, s), 3), round(min(e, dur), 3)


def _overlap(a: dict, b: dict) -> float:
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    shorter = min(a["end"] - a["start"], b["end"] - b["start"]) or 1e-6
    return inter / shorter


def select(moments: list[dict], n: int, max_overlap: float = 0.3, priority=None) -> list[dict]:
    """Os N de maior nota, sem sobreposição relevante; `priority(m)` verdadeiro vem antes (ex.: assistidos)."""
    out: list[dict] = []
    for m in sorted(moments, key=lambda m: (not priority(m) if priority else False, -m["score"])):
        if len(out) >= n:
            break
        if all(_overlap(m, o) <= max_overlap for o in out):
            out.append(m)
    return out
