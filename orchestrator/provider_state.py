from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from providers.errors import ClassifiedProviderError, ProviderFailure
from providers.health import ProviderHealth, ProviderState

_FAILURE_TO_STATE = {
    ProviderFailure.RATE_LIMITED: ProviderState.RATE_LIMITED,
    ProviderFailure.QUOTA_EXHAUSTED: ProviderState.QUOTA_EXHAUSTED,
    ProviderFailure.AUTH_FAILURE: ProviderState.AUTH_FAILURE,
    ProviderFailure.OUTAGE: ProviderState.OUTAGE,
}


def health_after_error(
    name: str,
    error: ClassifiedProviderError,
    *,
    now: datetime | None = None,
    default_retry_seconds: float = 60.0,
) -> ProviderHealth:
    now = now or datetime.now(UTC)
    state = _FAILURE_TO_STATE.get(error.kind, ProviderState.OUTAGE)
    retry_at = None
    if error.retryable:
        delay = error.retry_after_seconds or default_retry_seconds
        retry_at = now + timedelta(seconds=delay)
    return ProviderHealth(name=name, state=state, retry_at=retry_at, last_error=error.detail)


def save_provider_health(path: str | Path, providers: list[ProviderHealth]) -> None:
    payload = {
        provider.name: {
            "state": provider.state.value,
            "retry_at": provider.retry_at.isoformat() if provider.retry_at else None,
            "last_error": provider.last_error,
        }
        for provider in providers
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)
