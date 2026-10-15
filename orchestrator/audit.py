from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLog:
    """Append-only JSONL audit trail for durable agent actions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, *, task_id: str, **details: Any) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "task_id": task_id,
            **details,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
