from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class Checkpoint(BaseModel):
    task_id: str
    phase: str
    commit: str | None = None
    completed_steps: list[str] = Field(default_factory=list)
    remaining_steps: list[str] = Field(default_factory=list)
    test_results: dict[str, Any] = Field(default_factory=dict)
    known_failures: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    next_action: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def handoff_manifest(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "phase": self.phase,
            "commit": self.commit,
            "completed_work": self.completed_steps,
            "remaining_work": self.remaining_steps,
            "test_results": self.test_results,
            "known_failures": self.known_failures,
            "decisions": self.decisions,
            "next_action": self.next_action,
        }
