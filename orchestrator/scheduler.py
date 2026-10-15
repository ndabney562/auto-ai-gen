from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from orchestrator.dispatcher import NoProviderAvailable, select_provider
from orchestrator.state_machine import TaskState
from providers.health import ProviderHealth


def dependencies_satisfied(task: dict[str, Any], all_tasks: list[dict[str, Any]]) -> bool:
    completed = {item["id"] for item in all_tasks if item.get("status") == TaskState.DONE.value}
    return set(task.get("dependencies", [])) <= completed


def choose_ready_task(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        task
        for task in tasks
        if task.get("status") == TaskState.READY.value and dependencies_satisfied(task, tasks)
        and (not task.get("retry_at") or datetime.fromisoformat(task["retry_at"]) <= datetime.now(UTC))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda task: (task.get("priority", 0), task["id"]))


def dispatch_task(
    task: dict[str, Any],
    providers: list[ProviderHealth],
    *,
    preferred: str | None = None,
) -> dict[str, Any]:
    dispatched = dict(task)
    try:
        provider = select_provider(providers, preferred=preferred)
    except NoProviderAvailable:
        dispatched["status"] = TaskState.WAIT_PROVIDER.value
        dispatched["next_action"] = {"type": "retry_when_provider_available"}
        return dispatched

    dispatched["status"] = TaskState.CLAIMED.value
    dispatched["owner"] = {"provider": provider.name}
    dispatched["lease"] = {
        "token": uuid4().hex,
        "expires_at": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
    }
    dispatched["next_action"] = {"type": "execute"}
    return dispatched
