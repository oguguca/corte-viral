"""Estilo das legendas (padrões do config + ajustes salvos pela interface) e texto editado por clipe."""
from __future__ import annotations

import json
from pathlib import Path

from corteviral.config import CaptionStyle, Settings
from corteviral.utils import write_json_atomic

STYLE_FILE = "caption_style.json"


def _file(settings: Settings) -> Path:
    return Path(settings.paths.data_dir) / STYLE_FILE


def load_style(settings: Settings) -> CaptionStyle:
    """Padrões do config.yaml; o que foi salvo pela interface sobrescreve campo a campo."""
    base = settings.captions.model_dump()
    f = _file(settings)
    if f.exists():
        try:
            base.update(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return CaptionStyle.model_validate(base)


def save_style(settings: Settings, style: CaptionStyle) -> None:
    f = _file(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(f, style.model_dump())


def realign(original: list[dict], text: str) -> list[dict]:
    """Encaixa o texto editado nos tempos originais.

    Mesmo número de palavras → troca 1:1 mantendo os tempos. Senão, distribui as palavras novas no
    intervalo [primeira palavra, última palavra] proporcionalmente ao tamanho de cada uma.
    """
    tokens = text.split()
    if not tokens or not original:
        return []
    if len(tokens) == len(original):
        return [{"w": t, "start": o["start"], "end": o["end"]} for t, o in zip(tokens, original)]
    start, end = original[0]["start"], original[-1]["end"]
    weights = [len(t) + 1 for t in tokens]
    total = sum(weights)
    out, t = [], start
    for tok, wgt in zip(tokens, weights):
        dur = (end - start) * wgt / total
        out.append({"w": tok, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
    out[-1]["end"] = end
    return out
