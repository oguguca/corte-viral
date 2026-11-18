"""Monitor da live: fora da fila de tarefas, grava e processa uma janela por ciclo até a live acabar."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from corteviral.config import Settings
from corteviral.live.recorder import Recorder
from corteviral.live.state import LiveState, Part
from corteviral.project import Project

log = logging.getLogger(__name__)
LIVE_FILE = "live.json"
POLL = 15.0         # s entre ciclos
RETRY_AI = 120.0    # s até tentar de novo uma janela que a IA não avaliou
ACTIVE: dict[str, "LiveMonitor"] = {}   # projeto → monitor (uma live por vez)
_LOCK = threading.Lock()


class LiveError(Exception):
    """Falha mostrada ao usuário como está."""


def _default_process(*a, **kw):
    from corteviral.live.window import process_window
    return process_window(*a, **kw)


def _default_finalize(project, state):
    from corteviral.live.finalize import finalize
    return finalize(project, state)


def _default_is_live(url: str, settings: Settings) -> bool:
    from corteviral.pipeline.ingest import probe_is_live
    return probe_is_live(url, settings)


class LiveMonitor:
    def __init__(self, project: Project, settings: Settings, recorder, process=None, is_live=None,
                 clock=time.time, sleep=time.sleep, finalize_fn=None):
        self.project, self.settings, self.recorder = project, settings, recorder
        self.process = process or _default_process
        self.is_live = is_live or (lambda url: _default_is_live(url, settings))
        self.clock, self.sleep = clock, sleep
        self.finalize_fn = finalize_fn or _default_finalize
        self.path = project.dir / LIVE_FILE
        self.state = LiveState.load(self.path)
        self.failures = 0                        # quedas seguidas sem gravar nada
        self.wait = settings.live.retry_wait
        self.stopping = False
        self.finished = False

    # --- ciclo -----------------------------------------------------------
    def _new_part(self) -> None:
        n = len(self.state.parts)
        out = self.recorder.start(n)
        offset = 0.0 if n == 0 else round(self.clock() - self.state.started_at, 3)
        self.state.parts.append(Part(file=str(out), offset=offset))

    def step(self) -> None:
        st, lv = self.state, self.settings.live
        if st.parts:
            part = st.parts[-1]
            real = Recorder.resolve(Path(part.file))
            if real.exists():
                part.file = str(real)  # nome real (o yt-dlp pode acrescentar a extensão)
            part.duration = max(part.duration, self.recorder.recorded(Path(part.file)))
        if not self.recorder.running():
            gained = st.parts and st.parts[-1].duration > 1.0
            self.failures = 0 if gained else self.failures + 1
            if self.stopping or self.failures > lv.max_retries or not self.is_live(st.url):
                self._finish()
                return
            self.sleep(self.wait)
            self.wait = min(60, self.wait * 2)
            self._new_part()
        elif st.parts and st.parts[-1].duration > 1.0:
            self.wait = lv.retry_wait
        self._process_one()
        st.save(self.path)

    def _process_one(self, final: bool = False) -> bool:
        st, lv = self.state, self.settings.live
        now = self.clock()
        win = next((w for w in st.windows if w.state == "pending_ai" and (final or w.next_try <= now)), None)
        if win is None:
            win = st.next_window(lv.window_seconds, lv.overlap_seconds, final=final)
            if win is None:
                return False
            st.windows.append(win)
        try:
            self.process(self.project, st, win, self.settings)
        except Exception as e:  # noqa: BLE001 — uma janela ruim não derruba a live
            log.warning("Janela %s da live falhou: %s", win.n, e)
            win.state, win.tries = "failed", win.tries + 1
        if win.state == "pending_ai":
            win.next_try = now + RETRY_AI
        st.save(self.path)
        return True

    def _finish(self) -> None:
        st = self.state
        self.recorder.stop()
        for _ in range(500):  # o que sobrou da gravação antes de fechar
            if not self._process_one(final=True):
                break
            if not any(w.state == "pending_ai" for w in st.windows) and st.next_window(
                    self.settings.live.window_seconds, self.settings.live.overlap_seconds, final=True) is None:
                break
        st.active, st.ended = False, True
        st.save(self.path)
        try:
            self.finalize_fn(self.project, st)
        except Exception as e:  # noqa: BLE001
            log.warning("Não consegui juntar as partes da live: %s", e)
        st.save(self.path)
        self.finished = True
        with _LOCK:
            ACTIVE.pop(self.project.dir.name, None)

    # --- laço --------------------------------------------------------------
    def run(self) -> None:
        while not self.finished:
            try:
                self.step()
            except Exception as e:  # noqa: BLE001
                log.warning("Ciclo da live falhou: %s", e)
            if not self.finished:
                self.sleep(POLL)

    def stop(self) -> None:
        self.stopping = True
        self.recorder.stop()

    def status(self) -> dict:
        st = self.state
        clips = sum(len(w.clips) for w in st.windows)
        return {"active": not self.finished, "project": self.project.dir.name, "recorded": st.recorded_until(),
                "clips": clips, "windows": len(st.windows), "stopping": self.stopping,
                "pending_ai": sum(w.state == "pending_ai" for w in st.windows)}


def start_monitor(project: Project, settings: Settings, url: str, opts: dict, recorder=None, process=None,
                  is_live=None, clock=time.time, sleep=time.sleep, finalize_fn=None,
                  thread: bool = True) -> LiveMonitor:
    """Começa (ou retoma, se já há live.json) o acompanhamento. Uma live por vez."""
    with _LOCK:
        if ACTIVE:
            raise LiveError("já existe uma live sendo acompanhada — pare ela antes de começar outra")
        path = project.dir / LIVE_FILE
        state = LiveState.load(path)
        if state is None:
            state = LiveState(url=url, opts=opts, started_at=clock())
        state.active, state.ended = True, False
        state.save(path)
        rec = recorder or Recorder(url, project.dir / "live", settings)
        m = LiveMonitor(project, settings, rec, process=process, is_live=is_live, clock=clock, sleep=sleep,
                        finalize_fn=finalize_fn)
        m._new_part()
        m.state.save(path)
        ACTIVE[project.dir.name] = m
    if thread:
        threading.Thread(target=m.run, daemon=True, name=f"live-{project.dir.name}").start()
    return m
