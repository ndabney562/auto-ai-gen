from __future__ import annotations

import fcntl
import json
import os
import tempfile
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
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".health-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_provider_health(path: str | Path) -> list[ProviderHealth]:
    """Restore provider cooldowns rather than forgetting them on process restart."""
    target = Path(path)
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    providers = []
    for name, item in sorted(payload.items()):
        retry_at = item.get("retry_at")
        providers.append(
            ProviderHealth(
                name=name,
                state=ProviderState(item["state"]),
                retry_at=datetime.fromisoformat(retry_at) if retry_at else None,
                last_error=item.get("last_error"),
            )
        )
    return providers


def record_provider_error(path: str | Path, name: str, error: ClassifiedProviderError) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        providers = {p.name: p for p in load_provider_health(target)}
        sanitized = ClassifiedProviderError(error.kind, error.retryable, error.retry_after_seconds)
        providers[name] = health_after_error(name, sanitized)
        save_provider_health(target, list(providers.values()))
