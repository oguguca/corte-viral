"""👍/👎 do usuário nos momentos: viram exemplos do gosto dele no prompt da IA."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from corteviral.utils import write_json_atomic

_LOCK = threading.Lock()


class FeedbackStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    def add(self, project_id: str, streamer: str | None, moment: dict, label: str | None) -> None:
        """Marca (ou desmarca, com label=None) um momento. Remarcar substitui."""
        with _LOCK:
            items = [e for e in self._load() if not (e["project"] == project_id and e["moment"] == moment["id"])]
            if label in ("good", "bad"):
                items.append({
                    "project": project_id, "moment": moment["id"], "streamer": streamer or "", "label": label,
                    "description": (moment.get("description") or "")[:300], "reason": (moment.get("reason") or "")[:300],
                    "text": (moment.get("text") or "")[:400], "criteria": moment.get("criteria") or {},
                    "score": moment.get("score"), "ts": time.time()})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.path, items)

    def label_for(self, project_id: str, moment_id: str) -> str | None:
        return next((e["label"] for e in self._load() if e["project"] == project_id and e["moment"] == moment_id), None)

    def labels(self, project_id: str) -> dict[str, str]:
        return {e["moment"]: e["label"] for e in self._load() if e["project"] == project_id}

    def examples(self, streamer: str | None, k: int = 6) -> list[dict]:
        """Até k exemplos (metade bons, metade ruins); mesmo streamer primeiro, mais recentes primeiro."""
        items = sorted(self._load(), key=lambda e: (e["streamer"] != (streamer or ""), -e["ts"]))
        half = max(1, k // 2)
        good = [e for e in items if e["label"] == "good"][:half]
        bad = [e for e in items if e["label"] == "bad"][:half]
        return good + bad
