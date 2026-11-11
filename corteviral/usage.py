"""Contador de uso da IA (requisições e tokens por dia e por minuto), persistido em data/usage.json."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from corteviral.utils import write_json_atomic

log = logging.getLogger(__name__)
KEEP_DAYS = 30
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(Path(path).resolve()).lower()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class UsageTracker:
    def __init__(self, path: Path, now: Callable[[], datetime] = datetime.now):
        self.path = Path(path)
        self.now = now
        self._lock = _lock_for(self.path)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"days": {}}

    def record(self, provider: str, model: str, input_tokens: int, output_tokens: int,
               ok: bool, rate_limited: bool = False) -> None:
        now = self.now()
        day_key, minute_key = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        try:
            self._record(now, day_key, minute_key, provider, model, input_tokens, output_tokens, ok, rate_limited)
        except OSError as e:  # contabilidade nunca pode derrubar a análise
            log.warning("Não consegui salvar o uso da IA: %s", e)

    def _record(self, now, day_key, minute_key, provider, model, input_tokens, output_tokens, ok, rate_limited):
        with self._lock:
            data = self._load()
            day = data["days"].setdefault(day_key, {
                "requests": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0,
                "rate_limited": False, "minutes": {}, "models": {}})
            day["requests"] += 1
            day["errors"] += 0 if ok else 1
            day["input_tokens"] += int(input_tokens or 0)
            day["output_tokens"] += int(output_tokens or 0)
            day["rate_limited"] = day["rate_limited"] or rate_limited
            tokens = int(input_tokens or 0) + int(output_tokens or 0)
            day["minutes"][minute_key] = day["minutes"].get(minute_key, 0) + tokens
            mk = f"{provider}/{model}"
            day["models"][mk] = day["models"].get(mk, 0) + tokens
            cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
            data["days"] = {k: v for k, v in data["days"].items() if k >= cutoff}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.path, data)

    def today(self) -> dict:
        now = self.now()
        with self._lock:
            day = self._load()["days"].get(now.strftime("%Y-%m-%d"), {})
        minutes = day.get("minutes", {})
        per_minute = []
        for back in range(59, -1, -1):
            k = (now - timedelta(minutes=back)).strftime("%H:%M")
            per_minute.append({"t": k, "tokens": minutes.get(k, 0)})
        inp, out = day.get("input_tokens", 0), day.get("output_tokens", 0)
        return {
            "requests": day.get("requests", 0), "errors": day.get("errors", 0),
            "input_tokens": inp, "output_tokens": out, "total_tokens": inp + out,
            "rate_limited": day.get("rate_limited", False), "models": day.get("models", {}),
            "per_minute": per_minute,
        }
