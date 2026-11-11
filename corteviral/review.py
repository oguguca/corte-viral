"""Revisão de um momento: status, ajuste de corte, textos de postagem, legenda e gancho."""
from __future__ import annotations

from corteviral.caption_style import realign
from corteviral.config import Settings
from corteviral.metadata import MOMENTS_LOCK, PLATFORMS, fit, normalize_post
from corteviral.models import Transcript
from corteviral.presets import load_preset
from corteviral.project import Project
from corteviral.utils import write_json_atomic

STATUSES = ("pending", "approved", "discarded")
MIN_CLIP, MAX_CLIP = 5.0, 180.0


class ReviewError(ValueError):
    """Pedido de revisão inválido (vira HTTP 422 com a mensagem)."""


def _words(project: Project, start: float, end: float) -> list[dict]:
    tpath = project.path("transcript.json")
    if not tpath.exists():
        return []
    return [{"w": w.w, "start": w.start, "end": w.end}
            for w in Transcript.load(tpath).words if start <= w.start < end]


def _validate_cut(project: Project, start: float, end: float) -> None:
    duration = float(project.data.get("duration") or 0)
    if not start < end:
        raise ReviewError("o início precisa vir antes do fim")
    if start < 0 or (duration and end > duration + 0.01):
        raise ReviewError(f"o corte precisa ficar dentro do vídeo (0 a {duration:.0f} s)")
    if not MIN_CLIP <= end - start <= MAX_CLIP:
        raise ReviewError(f"o clipe precisa ter entre {MIN_CLIP:.0f} e {MAX_CLIP:.0f} segundos")


def _normalize_meta(meta: dict, credit: str | None, current: dict | None) -> dict:
    out = dict(current or {})
    if "hook" in meta:
        out["hook"] = fit(str(meta["hook"] or ""), 60)
    posts = dict(out.get("posts") or {})
    for plat, post in (meta.get("posts") or {}).items():
        if plat in PLATFORMS and isinstance(post, dict):
            posts[plat] = normalize_post(post, plat, credit)
    out["posts"] = posts
    return out


def apply_patch(project: Project, moment_id: str, patch: dict, settings: Settings) -> dict:
    """Valida e aplica a mudança no momento (em `moments` e `all`). KeyError se não existir."""
    from corteviral.pipeline.analyze import MOMENTS, load_moments

    with MOMENTS_LOCK:
        data = load_moments(project) or {}
        targets = [m for m in data.get("moments", []) + data.get("all", []) if m["id"] == moment_id]
        if not targets:
            raise KeyError(moment_id)
        m0 = targets[0]
        changes: dict = {}
        if patch.get("status") is not None:
            if patch["status"] not in STATUSES:
                raise ReviewError("status precisa ser pending, approved ou discarded")
            changes["status"] = patch["status"]
        if patch.get("start") is not None or patch.get("end") is not None:
            start = float(patch.get("start", m0["start"]) if patch.get("start") is not None else m0["start"])
            end = float(patch.get("end", m0["end"]) if patch.get("end") is not None else m0["end"])
            _validate_cut(project, start, end)
            changes.update(start=round(start, 3), end=round(end, 3),
                           text=" ".join(w["w"] for w in _words(project, start, end)))
            if m0.get("caption_words") and patch.get("caption_text") is None:
                old_text = " ".join(w["w"] for w in m0["caption_words"])
                changes["caption_words"] = realign(_words(project, start, end), old_text)
        for key in ("captions", "hook_on"):
            if patch.get(key) is not None:
                changes[key] = bool(patch[key])
        if patch.get("caption_pos") is not None:
            pos = float(patch["caption_pos"])
            if not 0.0 <= pos <= 1.0:
                raise ReviewError("posição da legenda precisa estar entre 0 e 1")
            changes["caption_pos"] = round(pos, 4)
        reset_caption = bool(patch.get("reset_caption"))
        if patch.get("caption_text") is not None:
            if not patch["caption_text"].strip():
                reset_caption = True  # caixa vazia = volta ao texto original (nunca uma legenda vazia)
            else:
                start, end = changes.get("start", m0["start"]), changes.get("end", m0["end"])
                changes["caption_words"] = realign(_words(project, start, end), patch["caption_text"])
        if patch.get("meta") is not None:
            posts = patch["meta"].get("posts") if isinstance(patch["meta"], dict) else None
            if not isinstance(patch["meta"], dict) or (posts is not None and not isinstance(posts, dict)):
                raise ReviewError("textos em formato inválido")
            preset = load_preset(settings.render.presets_dir, project.data.get("streamer"))
            changes["meta"] = _normalize_meta(patch["meta"], preset.credit_handle if preset else None,
                                              m0.get("meta"))
        for m in targets:
            m.update(changes)
            if reset_caption:
                m.pop("caption_words", None)
            if patch.get("reset_caption_pos"):
                m.pop("caption_pos", None)
        write_json_atomic(project.path(MOMENTS), data)
        return targets[0]
