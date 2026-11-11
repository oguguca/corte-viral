"""Modelos de dados persistidos em JSON."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from corteviral.utils import write_json_atomic


class Word(BaseModel):
    w: str
    start: float
    end: float
    p: float


class Segment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    language: str
    duration: float
    words: list[Word]
    segments: list[Segment]
    model: str = ""   # modelo realmente usado (pode ser o de fallback)
    device: str = ""  # cuda | cpu (realmente usado)

    def save(self, path: Path) -> None:
        write_json_atomic(Path(path), self.model_dump())

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
