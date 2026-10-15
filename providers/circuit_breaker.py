from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(slots=True)
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_seconds: float = 60.0
    failures: int = 0
    open_until: datetime | None = None

    def allows_request(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if self.open_until is None:
            return True
        if now >= self.open_until:
            self.failures = 0
            self.open_until = None
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.open_until = None

    def record_failure(self, *, retry_after_seconds: float | None = None) -> None:
        self.failures += 1
        if retry_after_seconds is not None or self.failures >= self.failure_threshold:
            delay = retry_after_seconds if retry_after_seconds is not None else self.cooldown_seconds
            self.open_until = datetime.now(UTC) + timedelta(seconds=delay)
