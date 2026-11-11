"""Etapa — candidatos: trechos em volta dos picos combinados de áudio, reação e chat."""
from __future__ import annotations

import bisect

import numpy as np
from pydantic import BaseModel

from corteviral.config import Settings
from corteviral.models import Transcript
from corteviral.pipeline.signals import Signals

# janela inicial em volta do pico (antes, depois) por perfil — fronteiras finas vêm depois
PROFILE_WINDOWS = {"viral": (25, 12), "monetizacao": (60, 30)}
SIGNAL_WEIGHTS = {"audio": 0.30, "reaction": 0.55, "chat": 0.15}
MIN_PEAK_DISTANCE = 20  # s
MIN_WORDS = 8
PEAK_THRESHOLD = 0.12
TEXT_WINDOW = 45  # s, janelas "faladas" complementares


class Candidate(BaseModel):
    id: str
    peak: float
    start: float
    end: float
    parts: dict[str, float]
    text: str


def _norm(curve, smooth: int) -> np.ndarray:
    x = np.asarray(curve, dtype=float)
    if smooth > 1 and len(x):
        x = np.convolve(x, np.ones(smooth) / smooth, mode="same") * smooth ** 0.5
    ref = np.percentile(x, 99) if len(x) else 0
    ref = max(ref, x.max() * 0.5 if len(x) else 0, 1e-6)
    return np.clip(x / ref, 0, 1)


def weights_for(has_chat: bool) -> dict[str, float]:
    w = dict(SIGNAL_WEIGHTS)
    if not has_chat:
        extra = w.pop("chat")
        total = sum(w.values())
        w = {k: v + extra * v / total for k, v in w.items()}
        w["chat"] = 0.0
    return w


def find_candidates(signals: Signals, transcript: Transcript, settings: Settings) -> list[Candidate]:
    dur = signals.duration
    if dur == 0:
        return []
    curves = {
        "audio": _norm(signals.audio, 3),
        "reaction": _norm(signals.reaction, 5),
        "chat": _norm(signals.chat, 5) if signals.chat else np.zeros(dur),
    }
    w = weights_for(signals.chat is not None)
    combined = sum(w[k] * curves[k] for k in curves)

    pre, post = PROFILE_WINDOWS[settings.analysis.profile]
    starts = [wd.start for wd in transcript.words]
    limit = settings.analysis.max_candidates

    def words_in(a: float, b: float):
        i, j = bisect.bisect_left(starts, a), bisect.bisect_right(starts, b)
        return transcript.words[i:j]

    def make(peak: int) -> Candidate | None:
        a, b = max(0.0, peak - pre), min(float(dur), peak + post)
        ws = words_in(a, b)
        if len(ws) < MIN_WORDS:
            return None
        lo, hi = max(0, peak - 5), min(dur, peak + 6)
        parts = {k: round(float(curves[k][lo:hi].max()) * 100, 1) for k in curves}
        return Candidate(id=f"m{peak:05d}", peak=float(peak), start=a, end=b, parts=parts,
                         text=" ".join(x.w for x in ws))

    chosen: list[Candidate] = []
    taken: list[int] = []

    def far(p: int) -> bool:
        return all(abs(p - t) >= MIN_PEAK_DISTANCE for t in taken)

    for p in np.argsort(-combined, kind="stable"):
        if len(chosen) >= limit or combined[p] < PEAK_THRESHOLD:
            break
        p = int(p)
        if far(p) and (c := make(p)):
            chosen.append(c)
            taken.append(p)

    # complemento: trechos com muita fala (piadas/histórias sem pico de som)
    if len(chosen) < limit:
        dens = [(len(words_in(s, s + TEXT_WINDOW)), s) for s in range(0, max(1, dur - TEXT_WINDOW), 15)]
        for n, s in sorted(dens, reverse=True):
            if len(chosen) >= limit or n < MIN_WORDS * 3:
                break
            p = int(s + TEXT_WINDOW / 2)
            if far(p) and (c := make(p)):
                chosen.append(c)
                taken.append(p)
    return chosen
