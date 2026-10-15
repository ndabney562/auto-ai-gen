from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class ProviderState(StrEnum):
    AVAILABLE = "AVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    AUTH_FAILURE = "AUTH_FAILURE"
    OUTAGE = "OUTAGE"


@dataclass(slots=True)
class ProviderHealth:
    name: str
    state: ProviderState = ProviderState.AVAILABLE
    retry_at: datetime | None = None
    last_error: str | None = None

    def available(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if self.state == ProviderState.AVAILABLE:
            return True
        if self.state in {ProviderState.RATE_LIMITED, ProviderState.QUOTA_EXHAUSTED, ProviderState.OUTAGE}:
            return self.retry_at is not None and now >= self.retry_at
        return False
