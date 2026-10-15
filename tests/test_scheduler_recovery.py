from datetime import UTC, datetime, timedelta

from orchestrator.recovery import recover_after_restart
from orchestrator.scheduler import choose_ready_task, dispatch_task
from orchestrator.state_machine import TaskState
from providers.health import ProviderHealth, ProviderState


def test_scheduler_respects_dependencies_and_priority() -> None:
    tasks = [
        {"id": "TP-1", "status": "DONE", "dependencies": []},
        {"id": "TP-2", "status": "READY", "dependencies": ["TP-1"], "priority": 10},
        {"id": "TP-3", "status": "READY", "dependencies": ["TP-9"], "priority": 100},
    ]

    assert choose_ready_task(tasks)["id"] == "TP-2"


def test_scheduler_waits_when_every_provider_is_unavailable() -> None:
    task = {"id": "TP-2", "status": "READY", "dependencies": [], "next_action": {}}
    providers = [
        ProviderHealth("openai", ProviderState.AUTH_FAILURE),
        ProviderHealth("anthropic", ProviderState.AUTH_FAILURE),
    ]

    result = dispatch_task(task, providers)

    assert result["status"] == TaskState.WAIT_PROVIDER.value


def test_scheduler_fails_over_to_anthropic() -> None:
    now = datetime.now(UTC)
    task = {"id": "TP-2", "status": "READY", "dependencies": [], "next_action": {}}
    providers = [
        ProviderHealth("openai", ProviderState.RATE_LIMITED, retry_at=now + timedelta(hours=1)),
        ProviderHealth("anthropic"),
    ]

    result = dispatch_task(task, providers, preferred="openai")

    assert result["status"] == TaskState.CLAIMED.value
    assert result["owner"]["provider"] == "anthropic"


def test_restart_requeues_expired_execution_without_losing_checkpoint() -> None:
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    task = {
        "id": "TP-7",
        "status": "EXECUTING",
        "checkpoint": {"commit": "abc123", "completed_steps": ["interface"]},
        "lease": {"expires_at": (now - timedelta(seconds=1)).isoformat()},
    }

    recovered = recover_after_restart(task, now=now)

    assert recovered["status"] == TaskState.READY.value
    assert recovered["lease"] is None
    assert recovered["checkpoint"] == task["checkpoint"]
