"""Etapa 2 — transcrição com faster-whisper (timestamps por palavra).

Robustez para VODs longos:
- checkpoint em transcript.partial.json a cada CHECKPOINT_EVERY segmentos; uma nova execução
  (após Ctrl+C, queda ou falha de GPU) retoma do último ponto, cortando o áudio com ffmpeg;
- teste prévio da GPU em processo separado (DLL CUDA ausente derruba o processo nativamente,
  sem exceção Python);
- falha de GPU → continua na CPU com o modelo de fallback, a partir do checkpoint;
- resultado de fallback não é cacheado como se fosse da GPU: a próxima execução tenta a GPU de novo.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from corteviral import ffmpeg
from corteviral.config import Settings
from corteviral.models import Segment, Transcript, Word
from corteviral.project import Project
from corteviral.utils import write_json_atomic

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]
_GPU_ERROR_MARKERS = ("cuda", "cudnn", "cublas", "out of memory")
CHECKPOINT_EVERY = 25  # segmentos
PARTIAL = "transcript.partial.json"
RESUME_WAV = "audio.resume.wav"
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


_PREFLIGHT_SCRIPT = r"""
import sys, numpy as np
from corteviral.pipeline.transcribe import _register_cuda_dlls
_register_cuda_dlls()
from faster_whisper import WhisperModel
m = WhisperModel(sys.argv[1], device="cuda", compute_type=sys.argv[2], download_root=sys.argv[3])
segs, _ = m.transcribe(np.zeros(16000, dtype=np.float32), language="pt", vad_filter=False)
list(segs)
"""
_preflight_cache: dict[tuple, bool] = {}


def _gpu_preflight(settings: Settings) -> bool:
    """Carrega o modelo na GPU num processo filho; False se ele morrer ou falhar."""
    ws = settings.whisper
    key = (ws.model, ws.compute_type)
    if key not in _preflight_cache:
        try:
            proc = subprocess.run(
                [sys.executable, "-c", _PREFLIGHT_SCRIPT, ws.model, ws.compute_type,
                 str(settings.paths.models_dir)],
                capture_output=True, text=True, timeout=1800,
            )
            ok = proc.returncode == 0
            if not ok:
                log.warning("Teste da GPU falhou (código %s): %s", proc.returncode,
                            (proc.stderr or "").strip()[-300:])
        except subprocess.TimeoutExpired:
            ok = False
        _preflight_cache[key] = ok
    return _preflight_cache[key]


def _is_gpu_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _GPU_ERROR_MARKERS)


class _Checkpoint:
    """Acumula segmentos/palavras e grava parcial periodicamente."""

    def __init__(self, path: Path, params: dict):
        self.path = path
        self.params = params
        self.words: list[Word] = []
        self.segments: list[Segment] = []
        self.language: str | None = None
        self.resume_at = 0.0
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("audio_params") == params:
                self.words = [Word(**w) for w in data["words"]]
                self.segments = [Segment(**s) for s in data["segments"]]
                self.language = data.get("language")
                self.resume_at = data.get("resume_at", 0.0)

    def add(self, seg_start: float, seg_end: float, text: str, words: list[Word]) -> None:
        self.segments.append(Segment(start=seg_start, end=seg_end, text=text))
        self.words.extend(words)
        self.resume_at = seg_end

    def save(self) -> None:
        write_json_atomic(self.path, {
            "audio_params": self.params, "language": self.language, "resume_at": self.resume_at,
            "words": [w.model_dump() for w in self.words],
            "segments": [s.model_dump() for s in self.segments],
        })


def _run(model, project: Project, settings: Settings, ckpt: _Checkpoint, duration: float,
         on_progress: ProgressFn | None) -> str:
    """Transcreve a partir de ckpt.resume_at. Devolve o idioma detectado."""
    ws = settings.whisper
    offset = ckpt.resume_at
    audio = project.path("audio.wav")
    if offset > 0:
        audio = project.path(RESUME_WAV)
        ffmpeg.run(["-y", "-ss", f"{offset:.3f}", "-i", str(project.path("audio.wav")),
                    "-c", "copy", str(audio)])
    language = ckpt.language or (None if ws.language == "auto" else ws.language)
    segments_iter, info = model.transcribe(
        str(audio), language=language, beam_size=ws.beam_size,
        vad_filter=ws.vad_filter, word_timestamps=True,
    )
    ckpt.language = ckpt.language or info.language
    total = duration or getattr(info, "duration", 0) or 0
    pending = 0
    for seg in segments_iter:  # a inferência acontece aqui (gerador preguiçoso)
        words = [
            Word(w=w.word.strip(), start=round(w.start + offset, 3), end=round(w.end + offset, 3),
                 p=round(w.probability, 3))
            for w in (seg.words or []) if w.word.strip()
        ]
        ckpt.add(round(seg.start + offset, 3), round(seg.end + offset, 3), seg.text.strip(), words)
        pending += 1
        if pending >= CHECKPOINT_EVERY:
            ckpt.save()
            pending = 0
        if on_progress and total:
            on_progress(min((seg.end + offset) / total, 1.0), "transcrevendo")
    return ckpt.language


def transcribe(project: Project, settings: Settings, on_progress: ProgressFn | None = None,
               model_factory=None) -> Transcript:
    """Transcreve audio.wav do projeto e grava transcript.json (com cache por parâmetros)."""
    ws = settings.whisper
    params = ws.model_dump()
    out = project.path("transcript.json")
    if project.is_stage_done("transcribe", params) and out.exists():
        return Transcript.load(out)

    audio = project.path("audio.wav")
    if not audio.exists():
        raise FileNotFoundError(f"audio.wav não encontrado em {project.dir}; rode o ingest antes")

    real_factory = model_factory is None
    factory = WhisperModel if real_factory else model_factory
    if real_factory:
        _register_cuda_dlls()
    duration = project.data.get("duration", 0)
    download_root = str(settings.paths.models_dir)
    audio_params = project.data.get("ingest_params", {})
    ckpt = _Checkpoint(project.path(PARTIAL), audio_params)
    if ckpt.resume_at:
        log.warning("Retomando transcrição a partir de %.0fs.", ckpt.resume_at)

    used = (ws.model, ws.device)
    gpu_ok = ws.device == "cpu" or not real_factory or _gpu_preflight(settings)
    try:
        if not gpu_ok:
            raise RuntimeError("CUDA indisponível (teste da GPU falhou)")
        model = factory(ws.model, device=ws.device, compute_type=ws.compute_type, download_root=download_root)
        language = _run(model, project, settings, ckpt, duration, on_progress)
    except Exception as e:  # noqa: BLE001 — só tratamos falhas de GPU; o resto sobe
        if ws.device == "cpu" or not _is_gpu_error(e):
            ckpt.save()
            raise
        ckpt.save()
        if out.exists() and not ckpt.resume_at:
            previous = Transcript.load(out)
            if previous.model == ws.cpu_fallback_model and previous.device == "cpu":
                log.warning("GPU ainda indisponível (%s); reaproveitando a transcrição feita na CPU.", e)
                project.path(PARTIAL).unlink(missing_ok=True)
                return previous
        log.warning("GPU falhou (%s). Continuando na CPU com o modelo '%s' a partir de %.0fs — vai demorar mais.",
                    e, ws.cpu_fallback_model, ckpt.resume_at)
        if on_progress:
            on_progress(ckpt.resume_at / duration if duration else 0.0, "GPU indisponível, usando CPU")
        used = (ws.cpu_fallback_model, "cpu")
        model = factory(ws.cpu_fallback_model, device="cpu", compute_type="int8", download_root=download_root)
        try:
            language = _run(model, project, settings, ckpt, duration, on_progress)
        except BaseException:
            ckpt.save()
            raise
    except BaseException:  # Ctrl+C etc.: guarda o progresso para retomar
        ckpt.save()
        raise

    transcript = Transcript(
        language=language or "", duration=round(duration or ckpt.resume_at, 3),
        words=sorted(ckpt.words, key=lambda w: w.start), segments=ckpt.segments,
        model=used[0], device=used[1] if used[1] != "auto" else "cuda",
    )
    transcript.save(out)
    project.invalidate_from("chat")  # sinais e momentos antigos não valem para a nova transcrição
    for leftover in (PARTIAL, RESUME_WAV):
        project.path(leftover).unlink(missing_ok=True)
    if used[0] == ws.model:
        project.mark_stage("transcribe", params)
    else:
        log.warning("Transcrição feita com o modelo de fallback; a próxima execução tentará a GPU de novo.")
    return transcript
