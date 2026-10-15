from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.audit import AuditLog
from orchestrator.runtime import WorkerFactory, execute_with_failover
from orchestrator.store import JsonTaskStore
from providers.health import ProviderHealth

ProviderSource = Callable[[], list[ProviderHealth]]


def execute_claimed_task(
    store: JsonTaskStore,
    task: dict[str, Any],
    provider_source: ProviderSource,
    factories: dict[str, WorkerFactory],
    *,
    audit_path: str | Path,
) -> dict[str, Any]:
    """Execute one claimed task and persist its result without losing durable state."""
    task_id = str(task["id"])
    audit = AuditLog(audit_path)
    running = dict(task)
    running["status"] = "EXECUTING"
    store.save(task_id, running)
    audit.append("execution_started", task_id=task_id, owner=running.get("owner"))

    handoff = dict(running.get("checkpoint") or {})
    handoff.setdefault("task_id", task_id)
    handoff.setdefault("task", running)

    try:
        provider, result = execute_with_failover(
            handoff,
            provider_source(),
            factories,
            preferred=(running.get("owner") or {}).get("provider"),
        )
    except Exception as exc:
        failed = dict(running)
        failed["status"] = "READY"
        failed["last_error"] = str(exc)
        failed["next_action"] = {"type": "retry"}
        store.save(task_id, failed)
        audit.append("execution_interrupted", task_id=task_id, error=str(exc))
        raise

    completed = dict(running)
    completed["status"] = "DONE"
    completed["result"] = result.output
    completed["owner"] = {"provider": provider}
    completed["checkpoint"] = {
        "task_id": task_id,
        "phase": "DONE",
        "completed_work": ["provider execution completed"],
        "remaining_work": [],
        "known_failures": [],
        "next_action": "none",
    }
    completed["next_action"] = {"type": "none"}
    store.save(task_id, completed)
    audit.append("execution_completed", task_id=task_id, provider=provider)
    return completed
