"""Etapa 2 da avaliação: a IA assiste os melhores candidatos e dá nota por critério e corte sugerido."""
from __future__ import annotations

import logging
from typing import Callable

from corteviral.config import Settings
from corteviral.ffmpeg import FFmpegError
from corteviral.llm import CRITERIA, LLMError, LLMRateLimited
from corteviral.models import Transcript
from corteviral.pipeline.boundaries import PROFILE_RANGES, snap_bounds
from corteviral.pipeline.deep import make_mini_clip, mini_window
from corteviral.project import Project

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]
MIN_CUT = 3.0  # s: corte sugerido menor que isso é descartado


def criteria_score(criteria: dict, weights: dict) -> int:
    total = sum(weights.get(c, 0) for c in CRITERIA) or 1.0
    value = sum(weights.get(c, 0) * criteria.get(c, 0) for c in CRITERIA) / total
    return int(value + 0.5)  # arredonda meio para cima (72,5 → 73)


def apply_review(moment: dict, review: dict, window: tuple[float, float], transcript: Transcript,
                 profile_range: tuple[float, float], weights: dict) -> dict:
    w0, w1 = window
    lo, hi = profile_range
    ini, fim = float(review["inicio"]), float(review["fim"])
    sane = 0 <= ini < fim <= (w1 - w0) + 1 and fim - ini >= MIN_CUT
    start, end = (w0 + ini, w0 + fim) if sane else (moment["start"], moment["end"])
    start, end = snap_bounds(start, end, transcript, lo, hi)
    criteria = {c: review[c] for c in CRITERIA}
    return {**moment, "triage_score": moment["score"], "score": criteria_score(criteria, weights),
            "criteria": criteria, "description": review["descricao"].strip()[:300],
            "reason": review["motivo"].strip()[:300], "risk": review["risco"].strip()[:200],
            "start": start, "end": end, "ai": "video", "cut_from_ai": sane,
            "text": " ".join(w.w for w in transcript.words if start <= w.start < end)}


def deep_review(project: Project, moments: list[dict], transcript: Transcript, settings: Settings, client,
                examples: list[dict] | None = None, on_progress: ProgressFn | None = None) -> tuple[list[dict], dict]:
    stats = {"video": 0, "rate_limited": False, "errors": 0, "skipped": False}
    cfg = settings.deep
    if not cfg.enabled or client is None or not hasattr(client, "review_video"):
        stats["skipped"] = True
        return moments, stats
    lo_hi = PROFILE_RANGES[settings.analysis.profile]
    duration = float(project.data.get("duration") or transcript.duration or 0)
    top = sorted(moments, key=lambda m: -m["score"])[:cfg.top_k]
    reviewed: dict[str, dict] = {}
    for i, m in enumerate(top):
        if on_progress:
            on_progress(i / max(1, len(top)), f"IA assistindo trecho {i + 1} de {len(top)}")
        window = mini_window(m, duration, cfg.margin)
        words = [w.w for w in transcript.words if window[0] <= w.start < window[1]]
        try:
            clip = make_mini_clip(project.data["video_path"], window[0], window[1],
                                  project.path("deep") / f"{m['id']}.mp4")
            review = client.review_video(clip.read_bytes(), {
                "profile": settings.analysis.profile, "streamer": project.data.get("streamer"),
                "transcript": " ".join(words), "window": list(window), "margin": cfg.margin,
                "examples": examples or []})
        except LLMRateLimited as e:
            log.warning("%s — os demais trechos ficam com a nota da triagem.", e)
            stats["rate_limited"] = True
            break
        except (LLMError, FFmpegError, OSError) as e:
            log.warning("Revisão em vídeo falhou em %s: %s", m["id"], e)
            stats["errors"] += 1
            continue
        reviewed[m["id"]] = apply_review(m, review, window, transcript, lo_hi, cfg.weights)
        stats["video"] += 1
    if on_progress:
        on_progress(1.0, "revisão em vídeo concluída")
    return [reviewed.get(m["id"], m) for m in moments], stats
