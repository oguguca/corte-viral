"""Textos de postagem por plataforma (gancho, título, descrição, hashtags) — com IA ou sem IA."""
from __future__ import annotations

import logging
import re
import threading
import unicodedata

from corteviral.config import Settings
from corteviral.llm import LLMError, make_client
from corteviral.presets import load_preset
from corteviral.project import Project
from corteviral.utils import write_json_atomic

log = logging.getLogger(__name__)
PLATFORMS = ("tiktok", "shorts", "reels", "kwai")
TITLE_MAX = {"tiktok": 80, "shorts": 100, "reels": 80, "kwai": 80}
DESC_MAX = 300
MAX_TAGS = 10
BASE_TAGS = ["#gta6", "#gta", "#cortes", "#gaming", "#viral"]
BATCH = 5
_AUTO = object()
MOMENTS_LOCK = threading.Lock()  # escritas em moments.json (textos, revisão, legenda)


def fit(text: str, limit: int) -> str:
    """Corta no limite sem quebrar palavra, com reticências."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return (cut or text[:limit - 1]) + "…"


def _tag(raw: str) -> str:
    body = re.sub(r"[^\w]", "", str(raw).replace("#", ""), flags=re.UNICODE)
    return f"#{body}" if body else ""


def normalize_hashtags(tags) -> list[str]:
    out, seen = [], set()
    for t in tags or []:
        tag = _tag(t)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:MAX_TAGS]


def _credit_line(credit: str | None) -> str:
    if not credit:
        return ""
    handle = credit.strip()
    return f"Créditos: {handle if handle.startswith('@') else '@' + handle}"


def normalize_post(post: dict, platform: str, credit: str | None) -> dict:
    title = str(post.get("title") or "")
    if platform == "shorts":
        title = fit(title.replace("#shorts", "").strip(), TITLE_MAX["shorts"] - len(" #shorts")) + " #shorts"
    else:
        title = fit(title, TITLE_MAX[platform])
    credit_line = _credit_line(credit)
    desc = str(post.get("description") or "")
    if credit_line:
        desc = desc.replace(credit_line, "").strip()
        desc = (fit(desc, DESC_MAX - len(credit_line) - 2) + "\n\n" + credit_line).strip()
    else:
        desc = fit(desc, DESC_MAX)
    return {"title": title, "description": desc, "hashtags": normalize_hashtags(post.get("hashtags"))}


def _streamer_tag(streamer: str | None) -> str:
    if not streamer:
        return ""
    ascii_ = unicodedata.normalize("NFKD", streamer).encode("ascii", "ignore").decode("ascii")
    return _tag(ascii_.lower())


def fallback_posts(moment: dict, streamer: str | None, credit: str | None) -> dict:
    words = (moment.get("text") or "").split()
    hook = (moment.get("hook") or " ".join(words[:6]) or "Olha isso").strip()
    desc = moment.get("description") or moment.get("reason") or hook
    tags = BASE_TAGS + ([_streamer_tag(streamer)] if streamer else [])
    raw = {"title": f"{hook} 😱", "description": desc, "hashtags": tags}
    return {"hook": fit(hook, 60), "ai": False,
            "posts": {p: normalize_post(raw, p, credit) for p in PLATFORMS}}


def _from_ai(item: dict, credit: str | None, fallback: dict) -> dict:
    posts = {}
    for p in PLATFORMS:
        post = item.get(p)
        posts[p] = normalize_post(post, p, credit) if isinstance(post, dict) and post.get("title") else fallback["posts"][p]
    hook = fit(str(item.get("hook") or fallback["hook"]), 60)
    return {"hook": hook, "ai": True, "posts": posts}


def generate_posts(project: Project, ids: list[str], settings: Settings, client=_AUTO) -> dict:
    """Gera (ou refaz) os textos dos momentos `ids` e salva em moments.json."""
    from corteviral.pipeline.analyze import MOMENTS, load_moments

    data = load_moments(project) or {}
    moments = [m for m in data.get("moments", []) if m["id"] in set(ids)]
    streamer = project.data.get("streamer")
    preset = load_preset(settings.render.presets_dir, streamer)
    credit = preset.credit_handle if preset else None
    llm = make_client(settings) if client is _AUTO else client
    can_write = llm is not None and hasattr(llm, "write_posts")
    results: dict[str, dict] = {}
    stats = {"ai": 0, "fallback": 0}
    language = project.data.get("language") or "pt"
    for i in range(0, len(moments), BATCH):
        batch = moments[i:i + BATCH]
        got: dict[str, dict] = {}
        if can_write:
            items = [{"id": m["id"], "description": m.get("description") or m.get("reason", ""),
                      "hook_hint": m.get("hook", ""), "transcript": (m.get("text") or "")[:1200],
                      "streamer": streamer or ""} for m in batch]
            try:
                got = {it["id"]: it for it in llm.write_posts(items, language)
                       if isinstance(it, dict) and isinstance(it.get("id"), str)}
            except LLMError as e:
                log.warning("IA não gerou textos (%s); usando textos simples.", e)
        for m in batch:
            fb = fallback_posts(m, streamer, credit)
            if m["id"] in got:
                results[m["id"]] = _from_ai(got[m["id"]], credit, fb)
                stats["ai"] += 1
            else:
                results[m["id"]] = fb
                stats["fallback"] += 1
    with MOMENTS_LOCK:
        data = load_moments(project) or {}
        for m in data.get("moments", []) + data.get("all", []):
            if m["id"] in results:
                m["meta"] = results[m["id"]]
        write_json_atomic(project.path(MOMENTS), data)
    return stats
