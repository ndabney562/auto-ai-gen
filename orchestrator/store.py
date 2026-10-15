from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonTaskStore:
    """Small durable store with atomic replacement for scheduler state."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        safe_id = task_id.replace("/", "_")
        return self.root / f"{safe_id}.json"

    def save(self, task_id: str, payload: dict[str, Any]) -> None:
        target = self._path(task_id)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(target)

    def load(self, task_id: str) -> dict[str, Any]:
        return json.loads(self._path(task_id).read_text(encoding="utf-8"))

    def list_tasks(self) -> list[dict[str, Any]]:
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(self.root.glob("*.json"))]
