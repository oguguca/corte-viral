"""Uma janela da live vira um mini-projeto e passa pelo pipeline de sempre (transcrever → analisar);
os momentos bons voltam para o projeto principal com tempos da linha do tempo da live e já viram clipe."""
from __future__ import annotations

import logging
from pathlib import Path

from corteviral import ffmpeg
from corteviral.config import Settings
from corteviral.live.state import LiveState, Window
from corteviral.metadata import MOMENTS_LOCK
from corteviral.models import Transcript
from corteviral.pipeline.analyze import MOMENTS, analyze, load_moments
from corteviral.pipeline.boundaries import _overlap
from corteviral.pipeline.ingest import AUDIO_RATE
from corteviral.pipeline.render import render_clip
from corteviral.pipeline.transcribe import transcribe
from corteviral.project import Project
from corteviral.utils import write_json_atomic

log = logging.getLogger(__name__)
MAX_OVERLAP = 0.3


def cut_window(part_path: Path, part_offset: float, win: Window, dest: Path) -> Path:
    """Recorte preciso (re-encode rápido) da janela: video.mp4 + audio.wav do mesmo jeito que o ingest."""
    dest.mkdir(parents=True, exist_ok=True)
    video, tmp = dest / "video.mp4", dest / "video.tmp.mp4"
    ss, dur = win.start - part_offset, win.end - win.start
    try:  # -ss antes do -i: não decodifica a live desde o começo (re-encode mantém o corte exato)
        ffmpeg.run(["-y", "-ss", f"{ss:.3f}", "-i", str(part_path), "-t", f"{dur:.3f}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(tmp)])
    except Exception:
        tmp.unlink(missing_ok=True)  # nunca deixa um vídeo pela metade para ser reaproveitado
        raise
    tmp.replace(video)
    ffmpeg.run(["-y", "-i", str(video), "-vn", "-ac", "1", "-ar", str(AUDIO_RATE), "-c:a", "pcm_s16le",
                str(dest / "audio.wav")])
    return video


def window_project(main: Project, win: Window, video: Path) -> Project:
    d = main.dir / "live" / f"win-{win.n:03d}"
    data = {"source": f"{main.data.get('source')}#janela={win.n}", "title": main.data.get("title"),
            "stages": {}, "video_path": str(video), "duration": round(win.end - win.start, 3),
            "ingest_params": {"source": f"live-win-{win.n}", "ar": AUDIO_RATE}}
    for k in ("streamer", "layout_detect", "language"):
        if main.data.get(k) is not None:
            data[k] = main.data[k]
    wp = Project(d, data)
    wp.save()
    return wp


def _append_transcript(main: Project, t: Transcript, win: Window) -> None:
    path = main.path("transcript.json")
    cur = Transcript.load(path) if path.exists() else Transcript(language=t.language, duration=0.0, words=[],
                                                                 segments=[], model=t.model, device=t.device)
    cutoff = max((w.end for w in cur.words), default=float("-inf"))  # sobreposição: não repete palavra
    for w in t.words:
        if w.start + win.start >= cutoff:
            cur.words.append(w.model_copy(update={"start": round(w.start + win.start, 3),
                                                  "end": round(w.end + win.start, 3)}))
    seg_cut = max((s.end for s in cur.segments), default=float("-inf"))
    for s in t.segments:
        if s.start + win.start >= seg_cut:
            cur.segments.append(s.model_copy(update={"start": round(s.start + win.start, 3),
                                                     "end": round(s.end + win.start, 3)}))
    cur.duration = max(cur.duration, win.end)
    cur.language = cur.language or t.language
    cur.save(path)


def _window_settings(settings: Settings, state: LiveState) -> Settings:
    s = settings.model_copy(deep=True)
    s.analysis.n_clips = None
    s.analysis.max_auto_clips = s.live.max_clips_per_window
    s.deep.top_k = s.live.review_per_window
    lo, hi = state.opts.get("min_seconds"), state.opts.get("max_seconds")
    if lo is not None and hi is not None:
        s.analysis.min_seconds, s.analysis.max_seconds = float(lo), float(hi)
    return s


def process_window(main: Project, state: LiveState, win: Window, settings: Settings, *,
                   transcribe_fn=transcribe, analyze_fn=analyze, render_fn=render_clip) -> list[dict]:
    """Processa uma janela; devolve os momentos novos (já com clipe gerado). Atualiza `win`."""
    main = Project.load(main.dir)  # mudanças feitas pela tela durante a live (preset, layout) valem já
    wdir = main.dir / "live" / f"win-{win.n:03d}"
    video = wdir / "video.mp4"
    if not video.exists():
        part = state.parts[win.part]
        cut_window(Path(part.file), part.offset, win, wdir)
    wp = Project.load(wdir) if (wdir / "project.json").exists() else window_project(main, win, video)
    if not main.data.get("video_path"):  # provisório até o fim da live: detecção de layout e prévia
        main.data["video_path"] = str(video)
    main.data["duration"] = round(max(float(main.data.get("duration") or 0), state.recorded_until(), win.end), 3)
    main.save()

    tpath = wp.path("transcript.json")
    t = Transcript.load(tpath) if tpath.exists() else transcribe_fn(wp, settings)
    _append_transcript(main, t, win)  # idempotente: tentar a janela de novo não repete palavras
    s = _window_settings(settings, state)
    analyze_fn(wp, s, n=None, profile=state.opts.get("profile"))
    saved = load_moments(wp) or {}
    if not saved.get("complete"):  # IA não avaliou: nada de clipe só por sinais; tenta no próximo ciclo
        win.state, win.tries = "pending_ai", win.tries + 1
        return []

    with MOMENTS_LOCK:
        data = load_moments(main) or {"moments": [], "all": []}
        data.setdefault("all", [])
        existing = data["moments"]
        new = []
        for m in saved.get("moments", []):
            m = {**m, "id": f"L{win.n:03d}-{m['id']}", "start": round(m["start"] + win.start, 3),
                 "end": round(m["end"] + win.start, 3), "source": {"video": str(video), "offset": win.start, "end": win.end}}
            if any(_overlap(m, o) > MAX_OVERLAP for o in existing + new):
                continue
            new.append(m)
        data["moments"] = existing + new
        data["all"] = data["all"] + [dict(m) for m in new]
        data.setdefault("range", [s.analysis.min_seconds, s.analysis.max_seconds])
        write_json_atomic(main.path(MOMENTS), data)
    for m in new:
        try:
            render_fn(main, m, settings)
        except Exception as e:  # noqa: BLE001 — um clipe que falha não derruba a live
            log.warning("Clipe %s da live não foi gerado: %s", m["id"], e)
    win.state, win.clips = "done", [m["id"] for m in new]
    return new
