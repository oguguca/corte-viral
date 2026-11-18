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


def keyframe_before(part_path: Path, t: float) -> float | None:
    """Último quadro-chave do vídeo em até 10 s antes de `t` (tempo da parte), ou None."""
    import subprocess
    probe = subprocess.run([ffmpeg.binary("ffprobe"), "-v", "error", "-show_entries", "format=start_time",
                            "-of", "csv=p=0", str(part_path)], capture_output=True, text=True).stdout.strip()
    try:  # MPEG-TS não começa em 0: os tempos do ffprobe são absolutos, o -ss do ffmpeg é relativo ao início
        base = float(probe.split()[0])
    except (ValueError, IndexError):
        base = 0.0
    lo = max(0.0, t - 10.0) + base - 0.2  # folga: o 1º quadro-chave fica exatamente no início
    out = subprocess.run([ffmpeg.binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                          "-show_entries", "frame=pts_time", "-read_intervals", f"{lo:.3f}%{t + base + 0.05:.3f}",
                          "-of", "csv=p=0", str(part_path)], capture_output=True, text=True).stdout
    times = []
    for line in out.split():
        try:
            times.append(float(line.strip().strip(",")))
        except ValueError:
            continue
    times = [x - base for x in times if x - base <= t + 0.1]  # o vídeo pode começar uns ms depois do áudio
    return round(max(times), 3) if times else None


def _video_delay(path: Path) -> float:
    """Quanto o 1º quadro de vídeo começa depois do início do arquivo (o áudio costuma vir antes)."""
    import subprocess

    def start(args):
        out = subprocess.run([ffmpeg.binary("ffprobe"), "-v", "error", *args, "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True).stdout.split()
        try:
            return float(out[0].strip(","))
        except (ValueError, IndexError):
            return 0.0
    return max(0.0, start(["-select_streams", "v:0", "-show_entries", "stream=start_time"])
               - start(["-show_entries", "format=start_time"]))


def cut_window(part_path: Path, part_offset: float, win: Window, dest: Path) -> Path:
    """Recorte da janela sem recodificar (rápido no notebook): começa no quadro-chave antes do início pedido e
    `win.start` passa a ser esse ponto, então os tempos continuam exatos. Sem quadro-chave → recodifica."""
    dest.mkdir(parents=True, exist_ok=True)
    video, tmp = dest / "video.mp4", dest / "video.tmp.mp4"
    want = win.start - part_offset
    kf = keyframe_before(part_path, want)
    try:  # -ss antes do -i: não decodifica a live desde o começo
        if kf is not None:
            ffmpeg.run(["-y", "-ss", f"{kf + 0.01:.3f}", "-i", str(part_path), "-t", f"{win.end - part_offset - kf:.3f}",
                        "-c", "copy", "-avoid_negative_ts", "make_zero", "-bsf:a", "aac_adtstoasc",
                        "-movflags", "+faststart", str(tmp)])
            win.start = round(part_offset + kf - _video_delay(tmp), 3)  # tempo 0 do arquivo = este ponto da live
        else:
            ffmpeg.run(["-y", "-ss", f"{want:.3f}", "-i", str(part_path), "-t", f"{win.end - win.start:.3f}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-c:a", "aac", "-b:a", "160k",
                        "-movflags", "+faststart", str(tmp)])
    except Exception:
        tmp.unlink(missing_ok=True)  # nunca deixa um vídeo pela metade para ser reaproveitado
        raise
    tmp.replace(video)
    ffmpeg.run(["-y", "-i", str(video), "-vn", "-ac", "1", "-ar", str(AUDIO_RATE), "-c:a", "pcm_s16le",
                str(dest / "audio.wav")])
    return video


def save_segment(window_video: Path, win: Window, m: dict, margin: int, dest: Path) -> dict:
    """Guarda só o trecho do clipe (com folga para ajustar o corte), sem recodificar. Devolve o novo `source`."""
    lo, hi = max(win.start, m["start"] - margin), min(win.end, m["end"] + margin)
    rel = lo - win.start
    kf = keyframe_before(window_video, rel)
    kf = rel if kf is None else kf
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".tmp.mp4")
    try:
        ffmpeg.run(["-y", "-ss", f"{kf + 0.01:.3f}", "-i", str(window_video), "-t", f"{hi - win.start - kf:.3f}",
                    "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(tmp)])
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    offset = win.start + kf - _video_delay(tmp)  # tempo 0 do arquivo = este ponto da live
    tmp.replace(dest)
    return {"video": str(dest), "offset": round(offset, 3), "end": round(hi, 3)}


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
    vp = main.data.get("video_path")
    if not vp or not Path(vp).exists():  # provisório: detecção de layout e prévia precisam de um vídeo
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

    candidates = []
    for m in saved.get("moments", []):
        candidates.append({**m, "id": f"L{win.n:03d}-{m['id']}", "start": round(m["start"] + win.start, 3),
                           "end": round(m["end"] + win.start, 3)})
    with MOMENTS_LOCK:
        data = load_moments(main) or {"moments": [], "all": []}
        data.setdefault("all", [])
        existing = data["moments"]
        new = []
        for m in candidates:
            if any(_overlap(m, o) > MAX_OVERLAP for o in existing + new):
                continue
            new.append(m)
    # só os melhores momentos ficam no disco: o trecho de cada clipe (com folga), não a janela de 5 min
    keep_window = False
    for m in new:
        try:
            m["source"] = save_segment(video, win, m, settings.live.keep_margin,
                                       main.dir / "live" / "src" / f"{m['id']}.mp4")
        except Exception as e:  # noqa: BLE001 — sem o trecho, o clipe usa a janela (que então fica)
            log.warning("Não consegui guardar o trecho de %s: %s", m["id"], e)
            m["source"] = {"video": str(video), "offset": win.start, "end": win.end}
            keep_window = True
    with MOMENTS_LOCK:
        data = load_moments(main) or {"moments": [], "all": []}
        data.setdefault("all", [])
        have = {m["id"] for m in data["moments"]}  # relido: ajustes feitos na tela nesse meio-tempo ficam
        data["moments"] = data["moments"] + [m for m in new if m["id"] not in have]
        data["all"] = data["all"] + [dict(m) for m in new]
        data.setdefault("range", [s.analysis.min_seconds, s.analysis.max_seconds])
        write_json_atomic(main.path(MOMENTS), data)
    for m in new:
        try:
            render_fn(main, m, settings)
        except Exception as e:  # noqa: BLE001 — um clipe que falha não derruba a live
            log.warning("Clipe %s da live não foi gerado: %s", m["id"], e)
    win.state, win.clips = "done", [m["id"] for m in new]
    if not keep_window:
        _drop_window_media(main, wdir, video, new)
    return new


def _drop_window_media(main: Project, wdir: Path, video: Path, new: list[dict]) -> None:
    """Janela processada: o vídeo de 5 min e o áudio dela saem do disco (a transcrição fica)."""
    if Path(main.data.get("video_path") or "") == video:  # nada pode apontar para arquivo apagado
        seg = next((m["source"]["video"] for m in new if Path(m["source"]["video"]).exists()), None)
        fresh = Project.load(main.dir)
        fresh.data["video_path"] = seg or ""
        fresh.save()
    video.unlink(missing_ok=True)
    (wdir / "audio.wav").unlink(missing_ok=True)
