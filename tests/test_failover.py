from datetime import UTC, datetime, timedelta

import pytest

from orchestrator.checkpoint import Checkpoint
from orchestrator.dispatcher import NoProviderAvailable, select_provider
from orchestrator.leases import Lease
from providers.health import ProviderHealth, ProviderState


def test_dispatcher_fails_over_from_unavailable_preferred_provider() -> None:
    now = datetime.now(UTC)
    openai = ProviderHealth("openai", ProviderState.RATE_LIMITED, retry_at=now + timedelta(minutes=5))
    anthropic = ProviderHealth("anthropic")

    selected = select_provider([openai, anthropic], preferred="openai")

    assert selected.name == "anthropic"


def test_dispatcher_raises_when_all_providers_unavailable() -> None:
    openai = ProviderHealth("openai", ProviderState.AUTH_FAILURE)
    anthropic = ProviderHealth("anthropic", ProviderState.AUTH_FAILURE)

    with pytest.raises(NoProviderAvailable):
        select_provider([openai, anthropic])


def test_expired_lease_can_be_detected() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    lease = Lease.acquire("TP-007", "worker-a", "openai", ttl_seconds=60, now=start)

    assert not lease.expired(start + timedelta(seconds=59))
    assert lease.expired(start + timedelta(seconds=60))


def test_checkpoint_produces_provider_independent_handoff() -> None:
    checkpoint = Checkpoint(
        task_id="TP-007",
        phase="implementation",
        commit="abc123",
        completed_steps=["streaming interface"],
        remaining_steps=["eviction tests"],
        next_action="continue implementation",
    )

    manifest = checkpoint.handoff_manifest()

    assert manifest["commit"] == "abc123"
    assert manifest["completed_work"] == ["streaming interface"]
    assert "provider" not in manifest
