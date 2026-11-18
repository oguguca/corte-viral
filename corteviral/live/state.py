"""Estado de uma live acompanhada ao vivo (live.json): partes gravadas e janelas processadas.

Linha do tempo da live = segundos desde o início da primeira parte gravada. Uma queda de rede abre uma parte
nova com `offset` pelo relógio de parede, então o buraco entre partes fica registrado.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from corteviral.utils import write_json_atomic

MIN_TAIL = 20.0  # s: sobra mínima de uma parte já encerrada que ainda vale uma janela


class Part(BaseModel):
    file: str
    offset: float               # início na linha do tempo da live
    duration: float = 0.0       # quanto já foi gravado

    @property
    def end(self) -> float:
        return self.offset + self.duration


class Window(BaseModel):
    n: int
    part: int
    start: float                # linha do tempo da live
    end: float
    state: Literal["pending", "pending_ai", "done", "failed"] = "pending"
    clips: list[str] = Field(default_factory=list)
    tries: int = 0


class LiveState(BaseModel):
    url: str
    active: bool = True
    opts: dict = Field(default_factory=dict)  # min_seconds, max_seconds, profile do Novo vídeo
    parts: list[Part] = Field(default_factory=list)
    windows: list[Window] = Field(default_factory=list)
    started_at: float = 0.0     # relógio de parede do início da primeira parte
    ended: bool = False

    @classmethod
    def load(cls, path: Path) -> "LiveState | None":
        try:
            return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def save(self, path: Path) -> None:
        write_json_atomic(Path(path), self.model_dump())

    def recorded_until(self) -> float:
        return self.parts[-1].end if self.parts else 0.0

    def next_window(self, window: int, overlap: int) -> Window | None:
        """Próxima janela a processar, ou None se ainda não há conteúdo novo suficiente.

        Nunca atravessa duas partes. Dentro da mesma parte, começa `overlap` s antes do fim da anterior
        (momento na divisa não se perde). Parte já encerrada fecha com o que sobrou (se ≥ 20 s).
        """
        if not self.parts:
            return None
        n = len(self.windows)
        if self.windows:
            last = self.windows[-1]
            p, cursor = last.part, last.end
            start = max(cursor - overlap, self.parts[p].offset)
        else:
            p = 0
            cursor = start = self.parts[0].offset
        while True:
            part = self.parts[p]
            finished = p < len(self.parts) - 1
            avail = part.end - cursor
            if avail >= window:
                return Window(n=n, part=p, start=round(start, 3), end=round(cursor + window, 3))
            if finished and avail >= MIN_TAIL:
                return Window(n=n, part=p, start=round(start, 3), end=round(part.end, 3))
            if not finished:
                return None
            p += 1
            cursor = start = self.parts[p].offset
