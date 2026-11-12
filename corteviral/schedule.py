"""Fila de postagem programada: solta um clipe a cada N minutos (data/publish_queue.json).

Uma leva nova começa só depois da última que ainda está na fila (ou do último envio), mantendo o intervalo.
Com o app fechado nada sai; ao voltar, os atrasados saem um por vez, respeitando o intervalo.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from corteviral.utils import write_json_atomic

MIN_INTERVAL, MAX_INTERVAL = 1, 24 * 60  # minutos
_LOCK = threading.Lock()


class ScheduleStore:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "publish_queue.json"

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("items"), list):
                return d
        except (OSError, json.JSONDecodeError):
            pass
        return {"items": [], "last_sent_at": None}

    def _save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, d)

    def items(self, project: str | None = None) -> list[dict]:
        out = [i for i in self._load()["items"] if project is None or i["project"] == project]
        return sorted(out, key=lambda i: i["at"])

    def summary(self, now: float | None = None) -> dict:
        """Quando a fila termina e quando uma leva nova começaria."""
        now = time.time() if now is None else now
        d = self._load()
        waiting = [i for i in d["items"] if i["status"] == "waiting"]
        return {"waiting": len(waiting), "ends_at": max((i["at"] for i in waiting), default=None),
                "next_at": min((i["at"] for i in waiting), default=None), "last_sent_at": d.get("last_sent_at"),
                "paused": bool(d.get("paused"))}

    def default_interval(self) -> int:
        """Intervalo da fila atual (o do último programado); 15 min se nunca programou."""
        items = sorted(self._load()["items"], key=lambda i: i["at"])
        return int(items[-1].get("interval") or 15) if items else 15

    def pause(self) -> None:
        """Nada sai até continuar."""
        with _LOCK:
            d = self._load()
            d["paused"] = True
            self._save(d)

    def resume(self, now: float | None = None) -> None:
        """Continua de onde parou: os que esperam ganham horários novos a partir de agora (mesma ordem e intervalo)."""
        now = time.time() if now is None else now
        with _LOCK:
            d = self._load()
            d["paused"] = False
            waiting = sorted((i for i in d["items"] if i["status"] == "waiting"), key=lambda i: i["at"])
            t = now
            if d.get("last_sent_at") and waiting:
                t = max(now, d["last_sent_at"] + waiting[0]["interval"] * 60)
            for k, i in enumerate(waiting):
                if k:
                    t += i["interval"] * 60
                i["at"] = round(t, 1)
            self._save(d)

    def plan(self, project: str, clip_ids: list[str], interval_min: int, now: float | None = None) -> list[dict]:
        """Põe os clipes na fila, um a cada `interval_min`, começando depois da última leva."""
        if not MIN_INTERVAL <= interval_min <= MAX_INTERVAL:
            raise ValueError("intervalo inválido")
        now = time.time() if now is None else now
        gap = interval_min * 60
        with _LOCK:
            d = self._load()
            waiting = [i for i in d["items"] if i["status"] == "waiting"]
            queued = {(i["project"], i["clip_id"]) for i in waiting}
            start = now
            if waiting:  # reconhece o fim da última leva e continua dali
                start = max(start, max(i["at"] for i in waiting) + gap)
            elif d.get("last_sent_at"):  # acabou de sair um: espera o intervalo
                start = max(start, d["last_sent_at"] + gap)
            new = []
            for cid in clip_ids:
                if (project, cid) in queued:
                    continue
                new.append({"id": uuid.uuid4().hex[:12], "project": project, "clip_id": cid,
                            "at": round(start + len(new) * gap, 1), "interval": interval_min,
                            "status": "waiting", "error": None, "sent_at": None})
                queued.add((project, cid))
            d["items"] = [i for i in d["items"] if not (i["status"] != "waiting" and i["project"] == project
                                                        and i["clip_id"] in clip_ids)] + new
            self._save(d)
        return new

    def cancel(self, item_id: str | None = None, project: str | None = None) -> int:
        """Tira da fila um item ou todos os que esperam de um projeto (ou da fila inteira)."""
        with _LOCK:
            d = self._load()
            keep, n = [], 0
            for i in d["items"]:
                hit = i["status"] == "waiting" and (
                    i["id"] == item_id if item_id else (project is None or i["project"] == project))
                if hit:
                    n += 1
                else:
                    keep.append(i)
            d["items"] = keep
            self._save(d)
        return n

    def due(self, now: float | None = None) -> dict | None:
        """O próximo a sair agora (no máximo um por intervalo, mesmo se vários estiverem atrasados)."""
        now = time.time() if now is None else now
        d = self._load()
        if d.get("paused"):
            return None
        waiting = sorted((i for i in d["items"] if i["status"] == "waiting"), key=lambda i: i["at"])
        if not waiting or waiting[0]["at"] > now:
            return None
        first, last = waiting[0], d.get("last_sent_at")
        if last and now - last < first["interval"] * 60 - 5:
            return None
        return first

    def mark(self, item_id: str, status: str, error: str | None = None, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with _LOCK:
            d = self._load()
            for i in d["items"]:
                if i["id"] == item_id:
                    i.update(status=status, error=(error or None) and str(error)[:300], sent_at=now)
            d["last_sent_at"] = now
            # guarda só os 200 últimos já resolvidos
            done = sorted((i for i in d["items"] if i["status"] != "waiting"), key=lambda i: -(i["sent_at"] or 0))
            drop = {i["id"] for i in done[200:]}
            d["items"] = [i for i in d["items"] if i["id"] not in drop]
            self._save(d)


def run_due(store: ScheduleStore, send: Callable[[dict], str | None], now: float | None = None) -> dict | None:
    """Solta o próximo clipe vencido. `send` devolve None (ok) ou o texto do erro."""
    item = store.due(now)
    if item is None:
        return None
    try:
        err = send(item)
    except Exception as e:  # noqa: BLE001 — um envio com problema não para a fila
        err = str(e) or repr(e)
    store.mark(item["id"], "error" if err else "sent", err, now)
    return {**item, "status": "error" if err else "sent", "error": err}


class Scheduler(threading.Thread):
    """Olha a fila a cada `every` segundos enquanto o app está aberto."""

    def __init__(self, store: ScheduleStore, send: Callable[[dict], str | None], every: float = 20):
        super().__init__(daemon=True, name="fila-postagem")
        self.store, self.send, self.every = store, send, every
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(self.every):
            try:
                run_due(self.store, self.send)
            except Exception:  # noqa: BLE001 — a fila nunca derruba o app
                pass
