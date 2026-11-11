"""Etapa 2 da avaliação: a IA assiste os melhores candidatos e dá nota por critério e corte sugerido."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from corteviral.config import Settings
from corteviral.ffmpeg import FFmpegError
from corteviral.llm import CRITERIA, LLMError, LLMRateLimited
from corteviral.models import Transcript
from corteviral.pipeline.boundaries import duration_range, snap_bounds
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
    return {**moment, "triage_score": moment.get("triage_score", moment["score"]),
            "score": criteria_score(criteria, weights),
            "criteria": criteria, "description": review["descricao"].strip()[:300],
            "reason": review["motivo"].strip()[:300], "risk": review["risco"].strip()[:200],
            "start": start, "end": end, "ai": "video", "cut_from_ai": sane,
            "text": " ".join(w.w for w in transcript.words if start <= w.start < end)}


def deep_review(project: Project, moments: list[dict], transcript: Transcript, settings: Settings, client,
                examples: list[dict] | None = None, on_progress: ProgressFn | None = None) -> tuple[list[dict], dict]:
    stats = {"video": 0, "rate_limited": False, "errors": 0, "skipped": False, "reason": None}
    cfg = settings.deep
    if not cfg.enabled or client is None or not hasattr(client, "review_video"):
        stats.update(skipped=True, reason="IA sem visão ou etapa desligada")
        return moments, stats
    video = Path(project.data.get("video_path") or "")
    if not video.is_file():
        stats.update(skipped=True, reason="vídeo original não encontrado (foi movido ou apagado?)")
        return moments, stats

    lo_hi = duration_range(settings)
    duration = float(project.data.get("duration") or transcript.duration or 0)
    top = sorted(moments, key=lambda m: -m["score"])[:cfg.top_k]
    reviewed = {m["id"]: m for m in top if m.get("ai") == "video"}  # já assistidos (retomada)
    stats["video"] = len(reviewed)
    todo = [m for m in top if m["id"] not in reviewed]
    stop = threading.Event()
    deep_dir = project.path("deep")

    def work(m: dict):
        if stop.is_set():
            return m["id"], None, "skip"
        window = mini_window(m, duration, cfg.margin)
        words = [w.w for w in transcript.words if window[0] <= w.start < window[1]]
        clip = deep_dir / f"{m['id']}.mp4"
        try:
            make_mini_clip(video, window[0], window[1], clip)
            data = clip.read_bytes()
        except (FFmpegError, OSError) as e:
            log.warning("Mini-vídeo de %s falhou: %s", m["id"], e)
            return m["id"], None, "error"
        finally:
            clip.unlink(missing_ok=True)
        ctx = {"profile": settings.analysis.profile, "streamer": project.data.get("streamer"),
               "transcript": " ".join(words), "window": list(window), "examples": examples or [],
               "margin": cfg.margin, "pre": m["start"] - window[0], "post": window[1] - m["end"],
               "range": list(lo_hi)}
        for attempt in range(2):  # resposta inválida de modelo pequeno: tenta mais uma vez
            if stop.is_set():
                return m["id"], None, "skip"
            try:
                review = client.review_video(data, ctx)
                return m["id"], apply_review(m, review, window, transcript, lo_hi, cfg.weights), "ok"
            except LLMRateLimited as e:
                log.warning("%s — os demais trechos ficam com a nota da triagem.", e)
                stop.set()
                return m["id"], None, "rate"
            except LLMError as e:
                if attempt == 0:
                    continue
                log.warning("Revisão em vídeo falhou em %s: %s", m["id"], e)
                return m["id"], None, "error"

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, cfg.workers)) as pool:
        for fut in as_completed([pool.submit(work, m) for m in todo]):
            mid, result, status = fut.result()
            done += 1
            if status == "ok":
                reviewed[mid] = result
                stats["video"] += 1
            elif status == "rate":
                stats["rate_limited"] = True
            elif status == "error":
                stats["errors"] += 1
            if on_progress:
                on_progress(done / max(1, len(todo)), f"IA assistiu {done} de {len(todo)} trechos")
    try:
        deep_dir.rmdir()  # só some se estiver vazia
    except OSError:
        pass
    return [reviewed.get(m["id"], m) for m in moments], stats
