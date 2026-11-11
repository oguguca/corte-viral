"""Etapa — sinais por segundo: volume do áudio, palavras de reação, ritmo de fala e chat."""
from __future__ import annotations

import json
import re
import unicodedata
import wave
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from corteviral.config import Settings
from corteviral.models import Transcript, Word
from corteviral.pipeline.chat import fetch_chat
from corteviral.project import Project
from corteviral.utils import write_json_atomic

# regex (sobre palavra normalizada, sem acento/pontuação) -> peso
WORD_REACTIONS: dict[str, float] = {
    r"k{3,}|(ka){2,}|(ha){2,}h?|(he){3,}|(hue){2,}|rs(rs)+|kkk\w*": 1.5,   # risada
    r"caralho|porra|krl|pqp|vsf|puta|merda|carai|caraio": 1.0,          # palavrão = reação forte
    r"mds|socorro|jesus": 1.2,
    r"absurdo|insano|bizarro|surreal|inacreditavel|incrivel|brabo|monstro|lendario": 0.8,
    r"corre|foge|cuidado|morreu|morri|matou|explodiu": 0.7,
    r"nossa|gente|uau|wow": 0.5,
    r"mano|cara|velho|vei": 0.25,
}
# frases (sequência de palavras normalizadas) -> peso
PHRASE_REACTIONS: dict[str, float] = {
    "meu deus": 1.5, "puta que pariu": 1.5, "nao acredito": 1.3, "nao e possivel": 1.2,
    "olha isso": 1.0, "que isso": 1.0, "ai meu": 0.8, "olha so": 0.6, "vai vai": 0.8,
    "oh my god": 1.3, "no way": 1.0, "what the": 1.0,
}
CAPS_BONUS = 0.5
EXCLAMATION_BONUS = 0.3


class Signals(BaseModel):
    duration: int
    audio: list[float]
    reaction: list[float]
    speech: list[float]
    chat: list[float] | None = None


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9 ]+", "", t).strip()


def robust_z(values, window: int = 60) -> list[float]:
    """Quanto cada ponto se destaca da vizinhança (mediana/MAD móveis). Negativos viram 0."""
    x = np.asarray(values, dtype=float)
    n = len(x)
    if n == 0:
        return []
    half = max(1, window // 2)
    padded = np.pad(x, half, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)[:n]
    med = np.median(win, axis=1)
    mad = np.median(np.abs(win - med[:, None]), axis=1)
    scale = 1.4826 * mad
    floor = np.maximum(0.05 * np.abs(med), 1e-3)
    z = (x - med) / np.maximum(scale, floor)
    return np.clip(z, 0, None).round(3).tolist()


def audio_loudness(wav_path: Path) -> list[float]:
    """Volume (dB RMS) por segundo, lendo o WAV em blocos (não carrega horas na memória)."""
    out: list[float] = []
    with wave.open(str(wav_path), "rb") as w:
        rate = w.getframerate()
        width = w.getsampwidth()
        if width != 2:
            raise ValueError("esperado WAV PCM 16 bits")
        while True:
            frames = w.readframes(rate)
            if not frames:
                break
            a = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(a * a))) if a.size else 0.0
            out.append(round(20 * np.log10(rms + 1e-6), 2))
    return out


def reaction_curve(words: list[Word], duration: int, extra: dict[str, float] | None = None) -> list[float]:
    """Soma, por segundo, o peso das palavras/frases de reação ditas naquele segundo."""
    curve = [0.0] * duration
    pats = [(re.compile(rf"^(?:{p})$"), w) for p, w in {**WORD_REACTIONS, **(extra or {})}.items()]
    normed = [_norm(w.w) for w in words]
    for i, w in enumerate(words):
        sec = int(w.start)
        if not 0 <= sec < duration:
            continue
        token = normed[i]
        score = 0.0
        for pat, weight in pats:
            if token and pat.match(token):
                score = max(score, weight)
        for phrase, weight in PHRASE_REACTIONS.items():
            parts = phrase.split()
            if normed[i:i + len(parts)] == parts:
                score = max(score, weight)
        if score or token:
            raw = w.w.strip()
            letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", raw)
            if score and len(letters) >= 2 and letters.isupper():
                score += CAPS_BONUS
            if score and raw.endswith("!"):
                score += EXCLAMATION_BONUS
        curve[sec] += score
    return [round(v, 3) for v in curve]


def speech_rate(words: list[Word], duration: int) -> list[float]:
    counts = [0.0] * duration
    for w in words:
        s = int(w.start)
        if 0 <= s < duration:
            counts[s] += 1
    return counts


def compute_signals(project: Project, settings: Settings) -> Signals:
    a = settings.analysis
    params = {"v": 1, "window": a.zscore_window, "extra": a.extra_reactions, "chat": a.fetch_chat}
    out = project.path("signals.json")
    if project.is_stage_done("signals", params) and out.exists():
        return Signals.model_validate(json.loads(out.read_text(encoding="utf-8")))

    transcript = Transcript.load(project.path("transcript.json"))
    loud = audio_loudness(project.path("audio.wav"))
    duration = len(loud)
    chat_raw = fetch_chat(project, settings)
    signals = Signals(
        duration=duration,
        audio=robust_z(loud, a.zscore_window),
        reaction=reaction_curve(transcript.words, duration, a.extra_reactions),
        speech=robust_z(speech_rate(transcript.words, duration), a.zscore_window),
        chat=robust_z(chat_raw[:duration] + [0] * max(0, duration - len(chat_raw)), a.zscore_window)
        if chat_raw else None,
    )
    write_json_atomic(out, signals.model_dump())
    project.mark_stage("signals", params)
    return signals
