"""Fim da live: as partes viram um video.mp4 só, na mesma linha do tempo (buracos de queda = preto + silêncio).

Uma parte só (sem queda) → só reempacota (rápido mesmo em lives longas). Com buracos → recodifica uma vez.
"""
from __future__ import annotations

from pathlib import Path

from corteviral import ffmpeg
from corteviral.live.state import LiveState
from corteviral.metadata import MOMENTS_LOCK
from corteviral.pipeline.analyze import MOMENTS, load_moments
from corteviral.pipeline.ingest import AUDIO_RATE
from corteviral.project import Project
from corteviral.utils import write_json_atomic
from corteviral.vision.frames import video_size

MIN_GAP = 0.5  # s


def _durations(state: LiveState) -> list[float]:
    out = []
    for part in state.parts:
        try:
            out.append(ffmpeg.probe_duration(Path(part.file)))
        except Exception:  # noqa: BLE001
            out.append(part.duration)
    return out


def _join(state: LiveState, dest: Path) -> None:
    parts = [p for p in state.parts if Path(p.file).exists()]
    durs = _durations(state)
    if len(parts) == 1 and parts[0].offset < MIN_GAP:
        ffmpeg.run(["-y", "-i", parts[0].file, "-c", "copy", "-bsf:a", "aac_adtstoasc",
                    "-movflags", "+faststart", str(dest)])
        return
    w, h = video_size(Path(parts[0].file))
    inputs, chains, labels, cursor = [], [], [], 0.0
    for i, (part, dur) in enumerate(zip(state.parts, durs)):
        if not Path(part.file).exists():
            continue
        gap = part.offset - cursor
        if gap > MIN_GAP:  # queda de rede: preto + silêncio do tamanho do buraco
            k = inputs.count("-i")
            inputs += ["-f", "lavfi", "-t", f"{gap:.3f}", "-i", f"color=c=black:size={w}x{h}:rate=30",
                       "-f", "lavfi", "-t", f"{gap:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            chains.append(f"[{k}:v]setsar=1,format=yuv420p[gv{i}];[{k + 1}:a]anull[ga{i}]")
            labels.append(f"[gv{i}][ga{i}]")
        idx = inputs.count("-i")
        inputs += ["-i", part.file]
        chains.append(f"[{idx}:v]scale={w}:{h},fps=30,setsar=1,format=yuv420p[pv{i}];"
                      f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo[pa{i}]")
        labels.append(f"[pv{i}][pa{i}]")
        cursor = part.offset + dur
    graph = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=1:a=1[v][a]"
    ffmpeg.run(["-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(dest)])


def finalize(project: Project, state: LiveState) -> Path:
    dest = project.path("video.mp4")
    _join(state, dest)
    ffmpeg.run(["-y", "-i", str(dest), "-vn", "-ac", "1", "-ar", str(AUDIO_RATE), "-c:a", "pcm_s16le",
                str(project.path("audio.wav"))])
    project.data.update(video_path=str(dest), duration=round(ffmpeg.probe_duration(dest), 3))
    project.save()
    with MOMENTS_LOCK:  # os clipes passam a ler o vídeo completo (mesma linha do tempo)
        data = load_moments(project)
        if data:
            for m in data.get("moments", []) + data.get("all", []):
                m.pop("source", None)
            write_json_atomic(project.path(MOMENTS), data)
    state.active, state.ended = False, True
    return dest
