from datetime import UTC, datetime, timedelta

from orchestrator.loop import run_scheduler_loop
from orchestrator.provider_state import health_after_error
from orchestrator.store import JsonTaskStore
from providers.circuit_breaker import CircuitBreaker
from providers.errors import ClassifiedProviderError, ProviderFailure
from providers.health import ProviderHealth, ProviderState


def test_retry_after_becomes_durable_provider_cooldown() -> None:
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    error = ClassifiedProviderError(
        ProviderFailure.RATE_LIMITED,
        retryable=True,
        retry_after_seconds=120,
        detail="slow down",
    )

    health = health_after_error("openai", error, now=now)

    assert health.state == ProviderState.RATE_LIMITED
    assert health.retry_at == now + timedelta(seconds=120)


def test_circuit_breaker_opens_after_repeated_failures() -> None:
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)

    breaker.record_failure()
    assert breaker.allows_request()
    breaker.record_failure()

    assert not breaker.allows_request()


def test_continuous_loop_persists_dispatched_task(tmp_path) -> None:
    store = JsonTaskStore(tmp_path)
    store.save(
        "TP-20",
        {"id": "TP-20", "status": "READY", "dependencies": [], "priority": 1},
    )

    run_scheduler_loop(
        store,
        lambda: [ProviderHealth("anthropic")],
        poll_seconds=0,
        max_ticks=1,
    )

    task = store.load("TP-20")
    assert task["status"] == "CLAIMED"
    assert task["owner"]["provider"] == "anthropic"
