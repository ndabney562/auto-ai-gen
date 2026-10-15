from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from orchestrator.executor import execute_claimed_task
from orchestrator.recovery import recover_after_restart
from orchestrator.runtime import WorkerFactory
from orchestrator.scheduler import choose_ready_task, dispatch_task
from orchestrator.store import JsonTaskStore
from providers.health import ProviderHealth

ProviderSource = Callable[[], list[ProviderHealth]]
logger = logging.getLogger(__name__)


def run_scheduler_loop(
    store: JsonTaskStore,
    provider_source: ProviderSource,
    *,
    factories: dict[str, WorkerFactory] | None = None,
    audit_path: str | Path = "audit/events.jsonl",
    poll_seconds: float = 5.0,
    max_ticks: int | None = None,
) -> None:
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        for existing in store.list_tasks():
            recovered = recover_after_restart(existing)
            if recovered != existing:
                store.save(recovered["id"], recovered)

        tasks = store.list_tasks()
        task = choose_ready_task(tasks)
        if task is not None:
            updated = dispatch_task(task, provider_source())
            store.save(updated["id"], updated)
            if updated["status"] == "CLAIMED" and factories is not None:
                try:
                    execute_claimed_task(
                        store,
                        updated,
                        provider_source,
                        factories,
                        audit_path=audit_path,
                    )
                except Exception:
                    # Executor persists the error and requeues the task for recovery.
                    logger.exception("Task %s execution interrupted", updated["id"])
        ticks += 1
        if max_ticks is None or ticks < max_ticks:
            time.sleep(poll_seconds)
