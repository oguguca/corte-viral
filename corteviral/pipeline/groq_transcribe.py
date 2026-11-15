"""Transcrição pela API grátis do Groq (Whisper na nuvem), para máquinas sem GPU.

O áudio vai em pedaços comprimidos (o plano grátis aceita até 25 MB por arquivo); cada resposta traz
timestamps por palavra, que recebem o deslocamento do pedaço.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from corteviral import ffmpeg
from corteviral.models import Segment, Word

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MAX_SHORT_WAIT = 60.0   # s: espera no 429 só se o Groq pedir até isso
MAX_RETRIES = 2
_LANGS = {"portuguese": "pt", "english": "en", "spanish": "es"}

HttpFn = Callable[[Path, str, str, "str | None"], dict]


class GroqError(Exception):
    """Falha ao transcrever pelo Groq (rede, servidor, resposta)."""


class GroqQuotaExceeded(GroqError):
    """Cota grátis do Groq esgotada (HTTP 429 com espera longa)."""


class GroqAuthError(GroqError):
    """Chave do Groq inválida ou sem permissão."""


def chunk_plan(start: float, duration: float, chunk: int) -> list[tuple[float, float]]:
    """(início, duração) dos pedaços que cobrem [start, duration); sobra < 0,5 s é ignorada."""
    out = []
    pos = start
    while duration - pos >= 0.5:
        length = min(chunk, duration - pos)
        out.append((pos, length))
        pos += length
    return out


def encode_chunk(audio: Path, offset: float, length: float, dest: Path) -> None:
    """Recorta e comprime um pedaço do áudio (mono 16 kHz, Opus 32 kbps)."""
    ffmpeg.run(["-y", "-ss", f"{offset:.3f}", "-t", f"{length:.3f}", "-i", str(audio),
                "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k", str(dest)])


def _multipart(fields: list[tuple[str, str]], file_path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
                  "Content-Type: application/octet-stream\r\n\r\n").encode())
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def post_chunk(path: Path, api_key: str, model: str, language: str | None, timeout: int = 180,
               sleep=time.sleep) -> dict:
    """Envia um pedaço e devolve a resposta verbose_json do Groq."""
    fields = [("model", model), ("response_format", "verbose_json"),
              ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment"),
              ("temperature", "0")]
    if language:
        fields.append(("language", language))
    body, ctype = _multipart(fields, Path(path))
    retries = 0
    while True:
        req = urllib.request.Request(URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": ctype, "User-Agent": "corteviral"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = float(e.headers.get("retry-after") or 0)
                except (TypeError, ValueError):
                    wait = 0.0
                if wait <= MAX_SHORT_WAIT and retries < MAX_RETRIES:
                    retries += 1
                    sleep(wait)
                    continue
                raise GroqQuotaExceeded("cota grátis do Groq esgotada (HTTP 429)") from e
            if e.code in (401, 403):
                raise GroqAuthError(f"chave do Groq recusada (HTTP {e.code})") from e
            raise GroqError(f"Groq HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise GroqError(f"sem conexão com o Groq: {e}") from e
        except json.JSONDecodeError as e:
            raise GroqError("resposta do Groq fora do formato esperado") from e


def _lang_code(name) -> str | None:
    if not name:
        return None
    name = str(name).strip().lower()
    if name in _LANGS:
        return _LANGS[name]
    return name if len(name) == 2 else None


def parse_response(data: dict, offset: float) -> tuple[list[Segment], list[Word], str | None]:
    """Segmentos e palavras com o deslocamento do pedaço somado; idioma em código de 2 letras."""
    segs = [Segment(start=round(s["start"] + offset, 3), end=round(s["end"] + offset, 3),
                    text=str(s.get("text", "")).strip())
            for s in data.get("segments") or []]
    words = [Word(w=str(w["word"]).strip(), start=round(w["start"] + offset, 3),
                  end=round(w["end"] + offset, 3), p=1.0)
             for w in data.get("words") or [] if str(w.get("word", "")).strip()]
    return segs, words, _lang_code(data.get("language"))
