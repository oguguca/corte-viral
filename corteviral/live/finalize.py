"""Fim da live: fica salvo só o que virou clipe (trechos em live/src, com folga para ajustar).

A gravação inteira (partes) e o que sobrou das janelas de 5 min são apagados — o usuário quer só os melhores
momentos, não a live inteira no disco.
"""
from __future__ import annotations

from pathlib import Path

from corteviral.live.state import LiveState
from corteviral.project import Project


def finalize(project: Project, state: LiveState) -> None:
    for part in state.parts:
        Path(part.file).unlink(missing_ok=True)
    for win_dir in (project.dir / "live").glob("win-*"):
        (win_dir / "video.mp4").unlink(missing_ok=True)
        (win_dir / "audio.wav").unlink(missing_ok=True)
    state.active, state.ended, state.finalized = False, True, True
