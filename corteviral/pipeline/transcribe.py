"""Etapa 2 — transcrição com faster-whisper (timestamps por palavra), com fallback para CPU."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable

from corteviral.config import Settings
from corteviral.models import Segment, Transcript, Word
from corteviral.project import Project

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]
_GPU_ERROR_MARKERS = ("cuda", "cudnn", "cublas", "out of memory")
# Windows sem modo desenvolvedor não cria symlinks; o cache funciona igual, só avisa.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

try:  # importado no nível do módulo para poder ser substituído em testes
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover
    WhisperModel = None


def _register_cuda_dlls() -> None:
    """No Windows, expõe as DLLs de cuBLAS/cuDNN instaladas via pip (pacotes nvidia-*)."""
    if os.name != "nt":
        return
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for bin_dir in base.glob("*/bin"):
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def _is_gpu_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _GPU_ERROR_MARKERS)


def _run(model, audio: Path, settings: Settings, duration: float, on_progress: ProgressFn | None):
    ws = settings.whisper
    segments_iter, info = model.transcribe(
        str(audio),
        language=None if ws.language == "auto" else ws.language,
        beam_size=ws.beam_size,
        vad_filter=ws.vad_filter,
        word_timestamps=True,
    )
    total = getattr(info, "duration", None) or duration or 0
    words: list[Word] = []
    segments: list[Segment] = []
    for seg in segments_iter:  # a inferência acontece aqui (gerador preguiçoso)
        segments.append(Segment(start=round(seg.start, 3), end=round(seg.end, 3), text=seg.text.strip()))
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(w=text, start=round(w.start, 3), end=round(w.end, 3),
                                  p=round(w.probability, 3)))
        if on_progress and total:
            on_progress(min(seg.end / total, 1.0), "transcrevendo")
    words.sort(key=lambda w: w.start)
    return Transcript(language=info.language, duration=round(total, 3), words=words, segments=segments)


def transcribe(project: Project, settings: Settings, on_progress: ProgressFn | None = None,
               model_factory=None) -> Transcript:
    """Transcreve audio.wav do projeto e grava transcript.json (com cache por parâmetros)."""
    params = settings.whisper.model_dump()
    out = project.path("transcript.json")
    if project.is_stage_done("transcribe", params) and out.exists():
        return Transcript.load(out)

    audio = project.path("audio.wav")
    if not audio.exists():
        raise FileNotFoundError(f"audio.wav não encontrado em {project.dir}; rode o ingest antes")

    factory = model_factory or WhisperModel
    if model_factory is None:
        _register_cuda_dlls()
    ws = settings.whisper
    duration = project.data.get("duration", 0)
    download_root = str(settings.paths.models_dir)

    device = "auto" if ws.device == "auto" else ws.device
    try:
        model = factory(ws.model, device=device, compute_type=ws.compute_type, download_root=download_root)
        transcript = _run(model, audio, settings, duration, on_progress)
    except Exception as e:  # noqa: BLE001 — só tratamos falhas de GPU; o resto sobe
        if ws.device == "cpu" or not _is_gpu_error(e):
            raise
        log.warning("GPU falhou (%s). Continuando na CPU com o modelo '%s' — vai demorar mais.",
                    e, ws.cpu_fallback_model)
        if on_progress:
            on_progress(0.0, "GPU indisponível, usando CPU")
        model = factory(ws.cpu_fallback_model, device="cpu", compute_type="int8", download_root=download_root)
        transcript = _run(model, audio, settings, duration, on_progress)

    transcript.save(out)
    project.mark_stage("transcribe", params)
    return transcript
