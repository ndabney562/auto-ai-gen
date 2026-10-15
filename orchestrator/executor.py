from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Any
from uuid import uuid4

from orchestrator.audit import AuditLog
from orchestrator.runtime import WorkerFactory, execute_with_failover
from orchestrator.store import JsonTaskStore
from providers.errors import normalize_sdk_error
from providers.health import ProviderHealth

logger = logging.getLogger(__name__)

ProviderSource = Callable[[], list[ProviderHealth]]


class LeaseLost(RuntimeError):
    pass


def _owned(task: dict[str, Any], token: str) -> bool:
    lease = task.get("lease") or {}
    return (task.get("status") == "EXECUTING" and lease.get("token") == token
            and datetime.fromisoformat(lease["expires_at"]) > datetime.now(UTC))


def execute_claimed_task(
    store: JsonTaskStore,
    task: dict[str, Any],
    provider_source: ProviderSource,
    factories: dict[str, WorkerFactory],
    *,
    audit_path: str | Path,
    heartbeat_seconds: float = 30,
) -> dict[str, Any]:
    """Execute under a renewable lease; only verified orchestration can finish work."""
    task_id = str(task["id"])
    audit = AuditLog(audit_path)
    if task.get("status") != "CLAIMED":
        raise ValueError("Execution requires a claimed task")
    lease = task.get("lease") or {}
    if lease and datetime.fromisoformat(lease["expires_at"]) <= datetime.now(UTC):
        raise LeaseLost("Claim lease expired")
    if int(task.get("attempt", 0)) >= int(task.get("max_attempts", 3)):
        exhausted = dict(task, status="FAILED_PERMANENT", lease=None,
                         next_action={"type": "human_intervention"})
        if not store.compare_and_save(task, exhausted):
            raise LeaseLost("Task changed before retry exhaustion")
        return exhausted
    token = lease.get("token") or uuid4().hex
    running = dict(task)
    running["status"] = "EXECUTING"
    running["attempt"] = int(task.get("attempt", 0)) + 1
    running["lease"] = {"token": token,
                        "expires_at": (datetime.now(UTC) + timedelta(seconds=120)).isoformat()}
    if not store.compare_and_save(task, running):
        raise LeaseLost("Task changed before execution")
    audit.append("execution_started", task_id=task_id, owner=running.get("owner"))
    stop = Event()
    lost = Event()

    def renew():
        while not stop.wait(heartbeat_seconds):
            try:
                current = store.load(task_id)
                if not _owned(current, token):
                    lost.set()
                    return
                updated = dict(current)
                updated["lease"] = {"token": token, "expires_at": (
                    datetime.now(UTC) + timedelta(seconds=120)).isoformat()}
                if not store.compare_and_save(current, updated):
                    lost.set()
                    return
            except Exception:
                logger.exception("Lease renewal failed for %s", task_id)
                lost.set()
                return

    heartbeat = Thread(target=renew, daemon=True)
    heartbeat.start()
    handoff = dict(running.get("checkpoint") or {})
    handoff.setdefault("task_id", task_id)
    handoff["task"] = running
    result = None
    provider = None
    error = None
    try:
        provider, result = execute_with_failover(
            handoff, provider_source(), factories,
            preferred=(running.get("owner") or {}).get("provider"),
        )
    except Exception as exc:  # noqa: BLE001 - persist failure without logging provider secrets
        logger.warning("Execution failed for %s: %s", task_id, type(exc).__name__)
        error = exc
    finally:
        stop.set()
        heartbeat.join()

    current = store.load(task_id)
    if lost.is_set() or not _owned(current, token):
        raise LeaseLost("Execution lost its lease; refusing stale result")
    updated = dict(current)
    updated["lease"] = None
    if error is not None:
        classified = normalize_sdk_error(error)
        retry = classified.retryable and running["attempt"] < int(task.get("max_attempts", 3))
        updated["status"] = "WAIT_RETRY" if retry else "FAILED_PERMANENT"
        updated["last_error"] = {"kind": classified.kind.value,
                                 "retryable": classified.retryable,
                                 "message": type(error).__name__}
        delay = max(classified.retry_after_seconds or 0, min(3600, 30 * 2 ** (
            min(running["attempt"], 8) - 1)))
        updated["retry_at"] = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        updated["next_action"] = {"type": "retry" if retry else "human_intervention"}
        event = "execution_interrupted"
    else:
        updated["status"] = "VALIDATING"
        updated["result"] = result.output
        updated["owner"] = {"provider": provider}
        checkpoint = dict(current.get("checkpoint") or {})
        checkpoint.update({"task_id": task_id, "phase": "VALIDATING",
                           "implementation_provider": provider,
                           "next_action": "validate_candidate"})
        updated["checkpoint"] = checkpoint
        updated["next_action"] = {"type": "validate_candidate"}
        event = "execution_awaiting_validation"
    if not store.compare_and_save(current, updated):
        raise LeaseLost("Task changed before result could be saved")
    audit.append(event, task_id=task_id, provider=provider)
    if error is not None:
        raise error
    return updated
