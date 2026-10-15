from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WorkerResult:
    output: str
    checkpoint_required: bool = True


class ProviderWorker(Protocol):
    name: str

    def execute(self, handoff: dict[str, object]) -> WorkerResult:
        """Execute one resumable unit of work from a provider-independent handoff."""
        ...
