from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.executor import execute_claimed_task
from orchestrator.provider_state import load_provider_health
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
    provider_health_path: str | Path | None = None,
    poll_seconds: float = 5.0,
    max_ticks: int | None = None,
) -> None:
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        live_providers = provider_source()
        saved = (
            {health.name: health for health in load_provider_health(provider_health_path)}
            if provider_health_path is not None
            else {}
        )
        providers = [saved.get(health.name, health) for health in live_providers]
        can_dispatch = any(health.available() for health in providers)

        for existing in store.list_tasks():
            recovered = recover_after_restart(existing)
            if recovered.get("status") == "WAIT_PROVIDER" and can_dispatch:
                recovered["status"] = "READY"
                recovered["next_action"] = {"type": "dispatch"}
            if (recovered.get("status") == "WAIT_RETRY" and recovered.get("retry_at")
                    and datetime.fromisoformat(recovered["retry_at"]) <= datetime.now(UTC)):
                recovered["status"] = "READY"
            if recovered != existing:
                store.compare_and_save(existing, recovered)

        tasks = store.list_tasks()
        task = choose_ready_task(tasks)
        if task is not None:
            updated = dispatch_task(task, providers)
            if not store.compare_and_save(task, updated):
                continue
            if updated["status"] == "CLAIMED" and factories is not None:
                try:
                    execute_claimed_task(
                        store,
                        updated,
                        lambda snapshot=providers: snapshot,
                        factories,
                        audit_path=audit_path,
                        provider_health_path=provider_health_path,
                    )
                except Exception:
                    # Executor persists the error and requeues the task for recovery.
                    logger.exception("Task %s execution interrupted", updated["id"])
        ticks += 1
        if max_ticks is None or ticks < max_ticks:
            time.sleep(poll_seconds)
