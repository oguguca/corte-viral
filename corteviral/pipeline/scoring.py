"""Etapa — nota 0–100 e justificativa: sinais locais + (opcional) IA."""
from __future__ import annotations

import logging
import re
from typing import Callable

from corteviral.config import Settings
from corteviral.llm import LLMClient, LLMError, LLMRateLimited
from corteviral.pipeline.candidates import Candidate, weights_for
from corteviral.pipeline.signals import PHRASE_REACTIONS

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]
AI_WEIGHTS = {"llm": 0.60, "audio": 0.25, "chat": 0.15}
MAX_TEXT_CHARS = 1800  # por trecho enviado à IA


def _signal_score(parts: dict, has_chat: bool) -> float:
    w = weights_for(has_chat)
    return sum(w[k] * parts.get(k, 0.0) for k in ("audio", "reaction", "chat"))


def _ai_score(llm: float, parts: dict, has_chat: bool) -> float:
    w = dict(AI_WEIGHTS)
    if not has_chat:
        extra = w.pop("chat")
        total = sum(w.values())
        w = {k: v + extra * v / total for k, v in w.items()}
        w["chat"] = 0.0
    return w["llm"] * llm + w["audio"] * parts.get("audio", 0.0) + w["chat"] * parts.get("chat", 0.0)


def _signal_reason(c: Candidate) -> str:
    bits = []
    if c.parts.get("audio", 0) >= 50:
        bits.append("pico de áudio")
    if c.parts.get("reaction", 0) >= 40:
        low = c.text.lower()
        phrase = next((p for p in PHRASE_REACTIONS if p in low), None)
        if re.search(r"k{4,}|(ha){3,}", low):
            phrase = phrase or "risadas"
        bits.append(f"reação forte ('{phrase}')" if phrase else "reação forte")
    if c.parts.get("chat", 0) >= 50:
        bits.append("chat explodiu")
    return " + ".join(bits) or "trecho com muita fala"


def _valid(item: dict, ids: set[str]) -> bool:
    return (isinstance(item, dict) and item.get("id") in ids and isinstance(item.get("score"), int)
            and 0 <= item["score"] <= 100 and isinstance(item.get("reason"), str) and item["reason"].strip() != "")


def score_candidates(candidates: list[Candidate], settings: Settings, client: LLMClient | None,
                     has_chat: bool = False, language: str = "pt",
                     on_progress: ProgressFn | None = None) -> tuple[list[dict], dict]:
    ai: dict[str, dict] = {}
    stats = {"ai": 0, "no_ai": 0, "rate_limited": False, "errors": 0}
    if client and candidates:
        size = max(1, settings.llm.batch_size)
        batches = [candidates[i:i + size] for i in range(0, len(candidates), size)]
        for bi, batch in enumerate(batches):
            if on_progress:
                on_progress(bi / len(batches), f"IA avaliando {bi * size + 1}–{bi * size + len(batch)}"
                                                f" de {len(candidates)}")
            items = [{"id": c.id, "text": c.text[:MAX_TEXT_CHARS],
                      "signals": f"volume {c.parts['audio']:.0f}, reação {c.parts['reaction']:.0f}, "
                                 f"chat {c.parts['chat']:.0f}"} for c in batch]
            try:
                result = client.score(items, settings.analysis.profile, language)
            except LLMRateLimited as e:
                log.warning("%s — o restante fica só com sinais.", e)
                stats["rate_limited"] = True
                break
            except LLMError as e:
                log.warning("IA falhou neste lote: %s", e)
                stats["errors"] += 1
                continue
            ids = {c.id for c in batch}
            for item in result:
                if _valid(item, ids):
                    ai[item["id"]] = item

    moments = []
    for c in candidates:
        r = ai.get(c.id)
        parts = {**c.parts, "llm": r["score"] if r else None}
        if r:
            score = _ai_score(r["score"], c.parts, has_chat)
            reason, hook, standalone = r["reason"].strip()[:140], (r.get("hook") or "").strip()[:60], bool(
                r.get("standalone", True))
            stats["ai"] += 1
        else:
            score = _signal_score(c.parts, has_chat)
            reason, hook, standalone = _signal_reason(c), "", True
            stats["no_ai"] += 1
        moments.append({"id": c.id, "peak": c.peak, "start": c.start, "end": c.end, "score": int(round(score)),
                        "reason": reason, "hook": hook, "standalone": standalone, "parts": parts,
                        "ai": r is not None, "text": c.text, "status": "pending"})
    if on_progress:
        on_progress(1.0, "pontuação concluída")
    return moments, stats
