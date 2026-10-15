from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, slots=True)
class Lease:
    task_id: str
    worker_id: str
    provider: str
    acquired_at: datetime
    expires_at: datetime

    @classmethod
    def acquire(
        cls,
        task_id: str,
        worker_id: str,
        provider: str,
        *,
        ttl_seconds: int = 900,
        now: datetime | None = None,
    ) -> Lease:
        now = now or datetime.now(UTC)
        return cls(task_id, worker_id, provider, now, now + timedelta(seconds=ttl_seconds))

    def expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at
