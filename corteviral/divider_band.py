"""Faixa da divisa: uma imagem (PNG com fundo transparente) por cima da linha entre a câmera e o jogo.

A imagem pode ser do tamanho do vídeo (1080×1920) com a faixa no meio: guardamos só a parte visível
(recorte pelo canal alfa). Fica em data/overlays/ (fora do git) com o ajuste em data/divider_band.json.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from corteviral import ffmpeg
from corteviral.utils import write_json_atomic

FILE_NAME = "faixa-divisa.png"


class BandError(Exception):
    """Falha mostrada ao usuário como está."""


class DividerBand(BaseModel):
    enabled: bool = False
    w: int = 0                   # tamanho da parte visível da imagem (px, no vídeo 1080 de largura)
    h: int = 0
    scale: float = Field(default=1.0, ge=0.3, le=2.0)
    offset: int = Field(default=0, ge=-700, le=700)   # px (em 1920) acima (−) ou abaixo (+) do meio da divisa
    version: int = 0             # muda quando a imagem é trocada (cache do navegador e dos clipes)


class BandStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.cfg = self.dir / "divider_band.json"
        self.image = self.dir / "overlays" / FILE_NAME

    def load(self) -> DividerBand:
        try:
            return DividerBand.model_validate(json.loads(self.cfg.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValidationError):
            return DividerBand()

    def active(self) -> DividerBand | None:
        """A faixa, se estiver ligada e com imagem; senão None."""
        b = self.load()
        return b if b.enabled and b.w and b.h and self.image.exists() else None

    def save(self, **patch) -> DividerBand:
        b = DividerBand.model_validate({**self.load().model_dump(), **patch})
        if b.enabled and not self.image.exists():
            raise BandError("escolha a imagem da faixa antes de ligar")
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.cfg, b.model_dump())
        return b

    def set_image(self, src: Path) -> DividerBand:
        """Copia o PNG recortado na parte visível e liga a faixa."""
        src = Path(src)
        if not src.is_file() or src.suffix.lower() not in (".png", ".webp"):
            raise BandError("use uma imagem PNG (com fundo transparente)")
        w, h, x, y = visible_box(src)
        self.image.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.image.with_name("tmp-" + FILE_NAME)
        try:
            ffmpeg.run(["-y", "-i", str(src), "-vf", f"crop={w}:{h}:{x}:{y}", "-frames:v", "1", str(tmp)])
            shutil.move(str(tmp), str(self.image))
        finally:
            tmp.unlink(missing_ok=True)
        b = self.load()
        return self.save(enabled=True, w=w, h=h, version=b.version + 1)


def visible_box(png: Path) -> tuple[int, int, int, int]:
    """(largura, altura, x, y) da parte não transparente da imagem."""
    cmd = [ffmpeg.binary("ffmpeg"), "-hide_banner", "-i", str(png), "-vf",
           "alphaextract,cropdetect=limit=0.02:round=2:reset=0:skip=0", "-frames:v", "1", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        if "alphaextract" in proc.stderr or "alpha" in proc.stderr.lower():
            raise BandError("a imagem não tem fundo transparente: exporte o PNG com transparência")
        raise BandError("não consegui ler a imagem: " + proc.stderr.strip()[-200:])
    found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", proc.stderr)
    if not found:
        raise BandError("a imagem está toda transparente")
    w, h, x, y = map(int, found[-1])
    if w < 20 or h < 10:
        raise BandError("a parte visível da imagem é pequena demais")
    return w, h, x, y


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def band_box(band: DividerBand, rs, k: float = 1.0) -> tuple[int, int, int, int]:
    """(x, y, w, h) da faixa no vídeo: centrada na divisa câmera/jogo, com o ajuste de altura e tamanho."""
    W, H = _even(rs.width * k), _even(rs.height * k)
    cam_h = _even(H * rs.facecam_ratio)
    div = _even(rs.divider_px * k) if rs.divider_px else 0
    w = _even(band.w * band.scale * (W / 1080))
    h = _even(band.h * band.scale * (W / 1080))
    y = int(round(cam_h + div / 2 - h / 2 + band.offset * (H / 1920)))
    return (W - w) // 2, y, w, h


def band_bottom(band: DividerBand, rs) -> int:
    """Pé da faixa no vídeo final (em 1920): o gancho fica logo abaixo dela."""
    _, y, _, h = band_box(band, rs)
    return y + h
