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
PLATFORM_MAX_TAGS = {"reels": 5}  # o Instagram aceita no máximo 5 hashtags por post
YT_TAGS_CHARS = 450               # o YouTube aceita 500 caracteres no campo Tags; folga para aspas/vírgulas
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


def normalize_hashtags(tags, limit: int = MAX_TAGS) -> list[str]:
    if isinstance(tags, str):
        tags = re.split(r"[\s,;]+", tags)
    out, seen = [], set()
    for t in tags or []:
        tag = _tag(t)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:limit]


def normalize_keywords(tags) -> list[str]:
    """Tags do YouTube (campo "Tags"): palavras-chave sem #, sem repetir, até YT_TAGS_CHARS no total."""
    if isinstance(tags, str):
        tags = tags.split(",")
    out, seen, total = [], set(), 0
    for t in tags or []:
        kw = " ".join(str(t).replace("#", "").replace(",", " ").split())[:60]
        if not kw or kw.lower() in seen:
            continue
        cost = len(kw) + (2 if out else 0)
        if total + cost > YT_TAGS_CHARS:
            continue
        seen.add(kw.lower())
        out.append(kw)
        total += cost
    return out


def _credit_line(credit: str | None) -> str:
    if not credit:
        return ""
    handle = credit.strip()
    return f"Créditos: {handle if handle.startswith('@') else '@' + handle}"


def normalize_post(post: dict, platform: str, credit: str | None) -> dict:
    title = str(post.get("title") or "")
    if platform == "shorts":
        base = re.sub(r"#shorts", "", title, flags=re.IGNORECASE).strip()
        title = (fit(base, TITLE_MAX["shorts"] - len(" #shorts")) + " #shorts") if base else ""
    else:
        title = fit(title, TITLE_MAX[platform])
    credit_line = _credit_line(credit)
    desc = str(post.get("description") or "")
    if credit_line:
        desc = desc.replace(credit_line, "").strip()
        desc = (fit(desc, DESC_MAX - len(credit_line) - 2) + "\n\n" + credit_line).strip()
    else:
        desc = fit(desc, DESC_MAX)
    hashtags = normalize_hashtags(post.get("hashtags"), PLATFORM_MAX_TAGS.get(platform, MAX_TAGS))
    out = {"title": title, "description": desc, "hashtags": hashtags}
    if platform == "shorts":  # o YouTube tem campo próprio de tags (palavras-chave para busca)
        out["tags"] = normalize_keywords(post.get("tags") or hashtags)
    return out


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
    keywords = ["gta 6", "gta vi", "gta 6 trailer", "gta 6 gameplay", "cortes de live", "gta"]
    if streamer:
        keywords.insert(2, streamer)
    raw = {"title": f"{hook} 😱", "description": desc, "hashtags": tags, "tags": keywords + tags}
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
