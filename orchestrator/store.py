from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from orchestrator.state_machine import TaskState, transition


class JsonTaskStore:
    """Fsynced task records with locked compare-and-swap for concurrent schedulers."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        if not task_id or len(task_id.encode()) > 150:
            raise ValueError("Task ID must contain 1 to 150 bytes")
        return self.root / f"{quote(task_id, safe='')}.json"

    @contextmanager
    def _locked(self):
        with (self.root / ".store.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _save(self, task_id: str, payload: dict[str, Any]) -> None:
        target = self._path(task_id)
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".task-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def save(self, task_id: str, payload: dict[str, Any]) -> None:
        with self._locked():
            self._save(task_id, payload)

    def compare_and_save(self, expected: dict[str, Any], updated: dict[str, Any]) -> bool:
        """Commit only if no claimant, heartbeat, cancellation, or edit intervened."""
        task_id = str(expected["id"])
        with self._locked():
            current = json.loads(self._path(task_id).read_text(encoding="utf-8"))
            if current != expected:
                return False
            if current.get("status") != updated.get("status"):
                transition(TaskState(current["status"]), TaskState(updated["status"]))
            self._save(task_id, updated)
            return True

    def load(self, task_id: str) -> dict[str, Any]:
        with self._locked():
            return json.loads(self._path(task_id).read_text(encoding="utf-8"))

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._locked():
            return [json.loads(path.read_text(encoding="utf-8"))
                    for path in sorted(self.root.glob("*.json"))]
