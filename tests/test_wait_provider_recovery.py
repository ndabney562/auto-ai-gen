from datetime import UTC, datetime, timedelta

from orchestrator.loop import run_scheduler_loop
from orchestrator.provider_state import load_provider_health, save_provider_health
from orchestrator.store import JsonTaskStore
from providers.health import ProviderHealth, ProviderState


def test_wait_provider_task_resumes_when_provider_returns(tmp_path) -> None:
    store = JsonTaskStore(tmp_path / "tasks")
    store.save("TP-50", {"id": "TP-50", "status": "WAIT_PROVIDER", "dependencies": []})

    run_scheduler_loop(store, list, poll_seconds=0, max_ticks=1)
    assert store.load("TP-50")["status"] == "WAIT_PROVIDER"

    run_scheduler_loop(
        store,
        lambda: [ProviderHealth("anthropic")],
        poll_seconds=0,
        max_ticks=1,
    )
    assert store.load("TP-50")["status"] == "CLAIMED"


def test_persisted_cooldown_blocks_dispatch_until_retry_time(tmp_path) -> None:
    store = JsonTaskStore(tmp_path / "tasks")
    store.save("TP-51", {"id": "TP-51", "status": "READY", "dependencies": []})
    health_path = tmp_path / "provider-health.json"
    future = datetime.now(UTC) + timedelta(minutes=30)
    save_provider_health(
        health_path,
        [ProviderHealth("openai", state=ProviderState.RATE_LIMITED, retry_at=future)],
    )

    restored = load_provider_health(health_path)
    assert restored[0].retry_at == future
    assert restored[0].state == ProviderState.RATE_LIMITED

    run_scheduler_loop(
        store,
        lambda: [ProviderHealth("openai")],
        provider_health_path=health_path,
        poll_seconds=0,
        max_ticks=1,
    )
    assert store.load("TP-51")["status"] == "WAIT_PROVIDER"

    past = datetime.now(UTC) - timedelta(seconds=1)
    save_provider_health(
        health_path,
        [ProviderHealth("openai", state=ProviderState.RATE_LIMITED, retry_at=past)],
    )

    run_scheduler_loop(
        store,
        lambda: [ProviderHealth("openai")],
        provider_health_path=health_path,
        poll_seconds=0,
        max_ticks=1,
    )
    assert store.load("TP-51")["status"] == "CLAIMED"
