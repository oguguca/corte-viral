"""Renderização dos clipes verticais 9:16 (corte + layout + codificação NVENC/x264), com cache por clipe."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable

from corteviral import ffmpeg
from corteviral.caption_style import load_style
from corteviral.config import Settings
from corteviral.models import Transcript
from corteviral.pipeline.captions import build_ass, caption_events, clip_words
from corteviral.ffmpeg import FFmpegError
from corteviral.pipeline.layout import build_filter
from corteviral.presets import LayoutPreset, resolve_layout
from corteviral.project import Project
from corteviral.utils import params_hash, write_json_atomic
from corteviral.vision.frames import grab, video_size

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]
CLIPS = "clips"
_BROKEN_ENCODERS: set[str] = set()  # codificadores que já falharam nesta execução (ex.: NVENC x driver)


def _encoder_args(encoder: str, cq: int, preview: bool) -> list[str]:
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(28 if preview else 21)]
    return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr", "-cq", str(30 if preview else cq), "-b:v", "0"]


def _face_track(video: Path, start: float, end: float, models_dir: Path) -> list[tuple[float, float]]:
    """Centro horizontal do maior rosto a cada 1 s do trecho (tempo relativo ao início do clipe)."""
    from corteviral.vision.detect import _detector

    times = [start + i for i in range(int(end - start) + 1)]
    frames = grab(video, times, width=640)
    if not frames:
        return []
    H, W = frames[0].shape[:2]
    det = _detector(models_dir, (W, H))
    track = []
    for t, f in zip(times, frames):
        faces = det(f)
        if faces:
            x, _, w, _, _ = max(faces, key=lambda b: b[2] * b[3])
            track.append((round(t - start, 2), (x + w / 2) / W))
    return track


def ass_filter_path(path: Path) -> str:
    """Caminho do .ass para o filtro do FFmpeg no Windows: barras '/', ':' escapado, entre aspas."""
    p = str(Path(path).resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    return f"'{p}'"


def _caption_words(project: Project, moment: dict, start: float, end: float) -> list[dict]:
    edited = moment.get("caption_words")
    if edited is not None:
        return clip_words(edited, start, end)
    tpath = project.path("transcript.json")
    if not tpath.exists():
        return []
    return clip_words(Transcript.load(tpath).words, start, end)


def hook_top_for(layout: str, has_cam: bool, rs) -> int:
    """Topo do gancho (em 1920): logo abaixo da câmera no split; topo da faixa 16:9 no fundo desfocado."""
    if layout == "split" and has_cam:
        cam_h = max(2, int(round(1920 * rs.facecam_ratio / 2)) * 2)
        return cam_h + (rs.divider_px or 0) + 40
    if layout == "face-follow":
        return 300
    band = 1080 * 9 / 16
    return int((1920 - band) / 2) + 30


def caption_style_for(moment: dict, style):
    """Estilo da legenda para um clipe: a posição escolhida no clipe vale mais que a de Ajustes."""
    if moment.get("caption_pos") is None:
        return style
    return style.model_copy(update={"position": float(moment["caption_pos"])})


def captions_wanted(moment: dict, captions: bool | None, style) -> bool:
    """Por clipe > pedido de geração > estilo (config/interface)."""
    if moment.get("captions") is not None:
        return bool(moment["captions"])
    if captions is not None:
        return bool(captions)
    return bool(style.enabled)


def _overlay_parts(project: Project, moment: dict, settings: Settings, preset: LayoutPreset,
                   captions: bool | None = None):
    """Estilo, palavras, gancho e crédito que vão em cima do vídeo (mesma regra no MP4 e na prévia)."""
    style = caption_style_for(moment, load_style(settings))
    start, end = float(moment["start"]), float(moment["end"])
    words = _caption_words(project, moment, start, end) if captions_wanted(moment, captions, style) else []
    hook_on = moment["hook_on"] if moment.get("hook_on") is not None else style.hook
    hook = ((moment.get("meta") or {}).get("hook") or moment.get("hook") or "") if hook_on else ""
    credit = preset.credit_handle if (style.credit and preset.credit_handle) else ""
    return style, words, hook, credit


def preview_spec(project: Project, moment: dict, settings: Settings) -> dict:
    """Tudo o que a prévia ao vivo da interface precisa para desenhar o clipe como ele vai sair."""
    rs = settings.render
    preset = resolve_layout(project, settings)
    style, words, hook, credit = _overlay_parts(project, moment, settings, preset)
    layout = "fallback" if preset.layout == "split" and not preset.facecam else preset.layout
    return {"layout": layout, "facecam": preset.facecam, "gameplay": preset.gameplay,
            "start": float(moment["start"]), "end": float(moment["end"]),
            "duration": round(float(moment["end"]) - float(moment["start"]), 3),
            "render": {"facecam_ratio": rs.facecam_ratio, "divider_px": rs.divider_px,
                       "divider_color": rs.divider_color},
            "style": style.model_dump(), "events": caption_events(words, style), "hook": hook,
            "hook_top": hook_top_for(preset.layout, bool(preset.facecam), rs), "credit": credit}


def render_clip(project: Project, moment: dict, settings: Settings, preview: bool = False,
                preset: LayoutPreset | None = None, on_progress: ProgressFn | None = None,
                captions: bool | None = None) -> Path:
    rs = settings.render
    video = Path(project.data["video_path"])
    preset = preset or resolve_layout(project, settings)
    det = project.data.get("layout_detect") or {}
    src = tuple(det.get("source") or video_size(video))
    start, end = float(moment["start"]), float(moment["end"])

    out_dir = project.path(CLIPS)
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{moment['id']}{'.preview' if preview else ''}.mp4"
    meta_file = out.with_suffix(".json")

    style, words, hook, credit = _overlay_parts(project, moment, settings, preset, captions)
    ass_text = None
    if words or hook or credit:
        ass_text = build_ass(words, style, hook=hook or None, credit=credit or None,
                             duration=end - start, layout=preset.layout,
                             hook_top=hook_top_for(preset.layout, bool(preset.facecam), rs))
    key = params_hash({"v": 2, "start": start, "end": end, "preset": preset.model_dump(),
                       "render": rs.model_dump(mode="json"), "preview": preview,
                       "captions": params_hash({"ass": ass_text}) if ass_text else None})
    if out.exists() and meta_file.exists():
        if json.loads(meta_file.read_text(encoding="utf-8")).get("hash") == key:
            return out

    track = _face_track(video, start, end, Path(settings.paths.models_dir)) if preset.layout == "face-follow" else None
    graph = build_filter(preset, src, rs, track=track, preview=preview)
    ass_file = out.with_suffix(".ass")
    if ass_text:
        ass_file.write_text(ass_text, encoding="utf-8")
        head, _, _ = graph.rpartition("[v]")
        graph = f"{head},ass=filename={ass_filter_path(ass_file)}[v]"
    else:
        ass_file.unlink(missing_ok=True)
    tmp = out.with_name(out.stem + ".tmp.mp4")
    base = ["-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(video),
            "-filter_complex", graph, "-map", "[v]", "-map", "0:a?"]
    tail = ["-c:a", "aac", "-b:a", rs.audio_bitrate, "-ac", "2", "-movflags", "+faststart", str(tmp)]

    if on_progress:
        on_progress(0.0, f"renderizando {moment['id']}")
    used = "libx264" if rs.encoder in _BROKEN_ENCODERS else rs.encoder
    try:
        ffmpeg.run(base + _encoder_args(used, rs.cq, preview) + tail)
    except FFmpegError as e:
        if used == "libx264":
            raise
        log.warning("Codificador %s falhou (%s); tentando libx264 (CPU).", used, str(e)[-200:])
        try:
            ffmpeg.run(base + _encoder_args("libx264", rs.cq, preview) + tail)
        except FFmpegError:
            tmp.unlink(missing_ok=True)
            raise e from None  # o problema não era o codificador
        _BROKEN_ENCODERS.add(used)  # x264 funcionou: o codificador da GPU é que está indisponível
        used = "libx264"

    if tmp.exists():
        os.replace(tmp, out)
    write_json_atomic(meta_file, {"hash": key, "layout": preset.layout, "encoder": used, "start": start,
                                  "end": end, "preview": preview, "preset": preset.model_dump(),
                                  "captions": bool(ass_text)})
    if on_progress:
        on_progress(1.0, f"{moment['id']} pronto")
    return out


def render_all(project: Project, settings: Settings, moments: list[dict], preview: bool = False,
               on_progress: ProgressFn | None = None, captions: bool | None = None) -> list[Path]:
    preset = resolve_layout(project, settings)
    outs = []
    for i, m in enumerate(moments):
        if on_progress:
            on_progress(i / max(1, len(moments)), f"renderizando clipe {i + 1} de {len(moments)}")
        outs.append(render_clip(project, m, settings, preview=preview, preset=preset, captions=captions))
    if on_progress:
        on_progress(1.0, f"{len(outs)} clipes prontos")
    return outs
