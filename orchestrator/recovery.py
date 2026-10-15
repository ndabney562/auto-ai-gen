from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from orchestrator.state_machine import TaskState


RECOVERABLE_ACTIVE_STATES = {TaskState.CLAIMED, TaskState.EXECUTING, TaskState.REPAIR}


def recover_after_restart(task: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return a recovered task record without discarding completed checkpoint work."""
    now = now or datetime.now(UTC)
    recovered = dict(task)
    state = TaskState(recovered["status"])

    lease = recovered.get("lease")
    lease_expired = True
    if lease and lease.get("expires_at"):
        expires_at = datetime.fromisoformat(lease["expires_at"])
        lease_expired = now >= expires_at

    if state in RECOVERABLE_ACTIVE_STATES and lease_expired:
        recovered["status"] = TaskState.READY.value
        recovered["lease"] = None
        recovered["recovery_reason"] = "expired_or_missing_lease"

    return recovered
