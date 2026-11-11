"""Utilitários genéricos."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path


def slugify(text: str, max_len: int = 40) -> str:
    """ASCII minúsculo com '-' — seguro como nome de pasta no Windows."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    slug = slug[:max_len].rstrip("-")
    return slug or "video"


def params_hash(params: dict) -> str:
    """Hash estável de um dicionário de parâmetros (independe da ordem das chaves)."""
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def write_json_atomic(path: Path, data) -> None:
    """Escreve JSON em UTF-8 via arquivo temporário + os.replace (não corrompe se cair no meio)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
