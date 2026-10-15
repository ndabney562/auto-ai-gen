from __future__ import annotations

import time
from collections.abc import Callable

from orchestrator.scheduler import choose_ready_task, dispatch_task
from orchestrator.store import JsonTaskStore
from providers.health import ProviderHealth

ProviderSource = Callable[[], list[ProviderHealth]]


def run_scheduler_loop(
    store: JsonTaskStore,
    provider_source: ProviderSource,
    *,
    poll_seconds: float = 5.0,
    max_ticks: int | None = None,
) -> None:
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        tasks = store.list_tasks()
        task = choose_ready_task(tasks)
        if task is not None:
            updated = dispatch_task(task, provider_source())
            store.save(updated["id"], updated)
        ticks += 1
        if max_ticks is None or ticks < max_ticks:
            time.sleep(poll_seconds)
