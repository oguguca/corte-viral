"""Orquestra a Fase 2: sinais → candidatos → nota → fronteiras → seleção → moments.json."""
from __future__ import annotations

import json
import time
from typing import Callable

from pathlib import Path

from corteviral.config import Settings
from corteviral.feedback import FeedbackStore
from corteviral.llm import DEFAULT_MODELS, make_client
from corteviral.models import Transcript
from corteviral.pipeline.boundaries import duration_range, select, snap_bounds
from corteviral.pipeline.candidates import find_candidates
from corteviral.pipeline.deep_review import deep_review
from corteviral.pipeline.scoring import score_candidates
from corteviral.pipeline.signals import compute_signals, signals_params
from corteviral.project import Project
from corteviral.utils import params_hash, write_json_atomic

ProgressFn = Callable[[float, str], None]
MOMENTS = "moments.json"
_AUTO = object()


def load_moments(project: Project) -> dict | None:
    f = project.path(MOMENTS)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def analyze(project: Project, settings: Settings, n: int | None = None, profile: str | None = None,
            on_progress: ProgressFn | None = None, client=_AUTO, examples: list[dict] | None = None) -> list[dict]:
    s = settings.model_copy(deep=True)
    if profile:
        s.analysis.profile = profile
    n = n or s.analysis.n_clips
    prog = on_progress or (lambda f, m: None)

    provider = s.llm.provider
    model = (s.llm.model or DEFAULT_MODELS.get(provider)) if provider != "none" else None
    params = {"v": 3, "profile": s.analysis.profile, "provider": provider, "model": model,
              "max_candidates": s.analysis.max_candidates, "batch": s.llm.batch_size,
              "signals": signals_params(project, s), "range": list(duration_range(s)),
              "deep": s.deep.model_dump() if provider == "gemini" else None}
    saved = load_moments(project)
    key = params_hash(params)
    if project.is_stage_done("scoring", params) and saved and "all" in saved:
        # mudar só a quantidade não precisa da IA: refaz a seleção sobre tudo que já foi pontuado
        if saved.get("n") != n:
            saved["moments"] = select(saved["all"], n)
            saved["n"] = n
            write_json_atomic(project.path(MOMENTS), saved)
        prog(1.0, "momentos já calculados")
        return saved["moments"]

    transcript = Transcript.load(project.path("transcript.json"))
    llm = make_client(s) if client is _AUTO else client
    resume = (saved and saved.get("params_hash") == key and saved.get("all") and not saved.get("complete")
              and not saved["stats"].get("rate_limited") and not saved["stats"].get("errors") and llm is not None)
    if resume:
        # execução anterior parou no meio da etapa de vídeo: reaproveita triagem e o que já foi assistido
        prog(0.6, "retomando de onde parou")
        moments, stats = saved["all"], {k: v for k, v in saved["stats"].items() if k != "deep"}
        has_chat, n_candidates = saved.get("has_chat", False), saved.get("candidates", len(moments))
    else:
        prog(0.02, "lendo sinais (áudio, reações, chat)")
        signals = compute_signals(project, s)
        prog(0.15, "procurando candidatos")
        candidates = find_candidates(signals, transcript, s)
        moments, stats = score_candidates(
            candidates, s, llm, has_chat=signals.chat is not None, language=transcript.language,
            on_progress=lambda f, m: prog(0.2 + 0.4 * f, m))
        has_chat, n_candidates = signals.chat is not None, len(candidates)
        prog(0.61, "ajustando início e fim")
        lo, hi = duration_range(s)
        for m in moments:
            m["start"], m["end"] = snap_bounds(m["start"], m["end"], transcript, lo, hi)
            m["text"] = " ".join(w.w for w in transcript.words if m["start"] <= w.start < m["end"])

    # etapa 2: a IA assiste os melhores candidatos (só quando o provedor tem visão)
    if examples is None:
        examples = FeedbackStore(Path(s.paths.data_dir) / "feedback.json").examples(
            project.data.get("streamer"), s.deep.max_examples)
    vision = None if stats["rate_limited"] else llm
    if vision is not None and provider == "gemini" and s.deep.model and client is _AUTO:
        vision = make_client(s, model=s.deep.model)
    moments, deep_stats = deep_review(project, moments, transcript, s, vision, examples=examples,
                                      on_progress=lambda f, m: prog(0.62 + 0.33 * f, m))
    stats["deep"] = deep_stats
    watched = any(m.get("ai") == "video" for m in moments)
    chosen = select(moments, n, priority=(lambda m: m.get("ai") == "video") if watched else None)

    # Só vira cache se a IA avaliou tudo que devia: senão a próxima execução tenta de novo
    complete = provider == "none" or (llm is not None and not stats["rate_limited"] and not stats["errors"]
                                      and not deep_stats["rate_limited"] and not deep_stats["errors"])
    write_json_atomic(project.path(MOMENTS), {
        "created_at": time.time(), "profile": s.analysis.profile, "n": n, "provider": provider,
        "model": model, "has_chat": has_chat, "candidates": n_candidates, "params_hash": key,
        "range": list(duration_range(s)),
        "stats": stats, "complete": complete, "ai_missing": provider != "none" and llm is None,
        "moments": chosen, "all": moments,
    })
    if complete:
        project.mark_stage("scoring", params)
    else:
        project.data["stages"].pop("scoring", None)
        project.save()
    prog(1.0, "momentos prontos")
    return chosen
