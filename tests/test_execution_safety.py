from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from orchestrator.executor import LeaseLost, execute_claimed_task
from orchestrator.loop import run_scheduler_loop
from orchestrator.scheduler import dispatch_task
from orchestrator.store import JsonTaskStore
from providers.base import WorkerResult
from providers.health import ProviderHealth


def test_only_one_concurrent_scheduler_can_claim(tmp_path):
    store = JsonTaskStore(tmp_path)
    ready = {"id": "one", "status": "READY"}
    store.save("one", ready)

    def claim(_):
        other = JsonTaskStore(tmp_path)
        return other.compare_and_save(ready, dispatch_task(ready, [ProviderHealth("p")]))

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(claim, range(8))) == 1
    assert store.load("one")["lease"]["token"]


def test_ids_do_not_collide_or_escape_store(tmp_path):
    store = JsonTaskStore(tmp_path)
    for name in ["a/b", "a_b", "a%2Fb", "../../outside"]:
        store.save(name, {"id": name})
    assert len(store.list_tasks()) == 4
    assert store.load("a/b") == {"id": "a/b"}


@pytest.mark.parametrize("permanent,attempt", [(True, 0), (False, 2)])
def test_auth_failure_and_retry_exhaustion_stop(tmp_path, permanent, attempt):
    class Failure(RuntimeError):
        status_code = 401 if permanent else 503

    class Worker:
        def execute(self, handoff):
            raise Failure("do not log credentials")

    store = JsonTaskStore(tmp_path / "tasks")
    claimed = dispatch_task({"id": "t", "status": "READY", "attempt": attempt},
                            [ProviderHealth("p")])
    store.save("t", claimed)
    with pytest.raises(RuntimeError):
        execute_claimed_task(store, claimed, lambda: [ProviderHealth("p")],
                             {"p": Worker}, audit_path=tmp_path / "audit")
    saved = store.load("t")
    assert saved["status"] == "FAILED_PERMANENT"
    assert saved["attempt"] == attempt + 1
    assert saved["lease"] is None
    assert "credentials" not in str(saved)


def test_cancellation_fences_stale_worker_result(tmp_path):
    store = JsonTaskStore(tmp_path / "tasks")
    claimed = dispatch_task({"id": "t", "status": "READY"}, [ProviderHealth("p")])
    store.save("t", claimed)

    class Worker:
        def execute(self, handoff):
            current = store.load("t")
            cancelled = dict(current, status="CANCELLED")
            assert store.compare_and_save(current, cancelled)
            return WorkerResult("done")

    with pytest.raises(LeaseLost):
        execute_claimed_task(store, claimed, lambda: [ProviderHealth("p")],
                             {"p": Worker}, audit_path=tmp_path / "audit")
    assert store.load("t")["status"] == "CANCELLED"


def test_active_lease_is_renewed_and_checkpoint_preserved(tmp_path):
    store = JsonTaskStore(tmp_path / "tasks")
    claimed = dispatch_task({"id": "t", "status": "READY",
                             "checkpoint": {"completed_work": ["keep"]}}, [ProviderHealth("p")])
    store.save("t", claimed)

    class Worker:
        def execute(self, handoff):
            expiry = store.load("t")["lease"]["expires_at"]
            # Wait for a real renewal, with a bounded test timeout.
            for _ in range(100):
                if store.load("t")["lease"]["expires_at"] != expiry:
                    return WorkerResult("plan only")
                Event().wait(.01)
            pytest.fail("lease was not renewed")

    saved = execute_claimed_task(store, claimed, lambda: [ProviderHealth("p")],
                                 {"p": Worker}, audit_path=tmp_path / "audit",
                                 heartbeat_seconds=.01)
    assert saved["status"] == "VALIDATING"
    assert saved["checkpoint"]["completed_work"] == ["keep"]


def test_backoff_prevents_immediate_reexecution(tmp_path):
    store = JsonTaskStore(tmp_path)
    store.save("t", {"id": "t", "status": "WAIT_RETRY",
                     "retry_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()})
    run_scheduler_loop(store, lambda: [ProviderHealth("p")], poll_seconds=0, max_ticks=3)
    assert store.load("t")["status"] == "WAIT_RETRY"


def test_subscription_limits_preserve_attempts_and_wait_for_first_provider(tmp_path):
    from orchestrator.provider_state import load_provider_health
    from providers.cli_provider import _failure

    class Limit:
        def __init__(self, delay):
            self.delay = delay

        def execute(self, handoff):
            from providers.errors import (
                ClassifiedProviderError,
                ProviderExecutionError,
                ProviderFailure,
            )
            raise ProviderExecutionError('limit', ClassifiedProviderError(
                ProviderFailure.QUOTA_EXHAUSTED, True, self.delay))

    store = JsonTaskStore(tmp_path / 'tasks')
    health = tmp_path / 'provider-health.json'
    for _ in range(5):
        task = dispatch_task({'id': 't', 'status': 'READY', 'attempt': 2, 'max_attempts': 3,
                              'checkpoint': {'completed_work': ['keep']}},
                             [ProviderHealth('openai'), ProviderHealth('anthropic')])
        store.save('t', task)
        waiting = execute_claimed_task(store, task,
            lambda: [ProviderHealth('openai'), ProviderHealth('anthropic')],
            {'openai': lambda: Limit(7200), 'anthropic': lambda: Limit(21600)},
            audit_path=tmp_path / 'audit', provider_health_path=health)
        assert waiting['status'] == 'WAIT_PROVIDER'
        assert waiting['attempt'] == 2
        assert waiting['lease'] is None
        assert waiting['checkpoint']['completed_work'] == ['keep']
        assert waiting['checkpoint']['resume_phase'] == 'READY'
        assert datetime.fromisoformat(waiting['retry_at']) == min(
            p.retry_at for p in load_provider_health(health))
    assert _failure('weekly limit').classified.retry_after_seconds == 3600


def test_missing_cli_old_cooldown_cannot_trigger_early_quota_probes(tmp_path):
    from orchestrator.provider_state import save_provider_health
    from providers.cli_provider import _failure
    from providers.health import ProviderState

    health = tmp_path / 'provider-health.json'
    save_provider_health(health, [ProviderHealth('anthropic', ProviderState.RATE_LIMITED,
                                                datetime.now(UTC) - timedelta(hours=1))])

    class Limit:
        def execute(self, handoff):
            raise _failure('weekly limit')

    claimed = dispatch_task({'id': 't', 'status': 'READY'}, [ProviderHealth('openai')])
    store = JsonTaskStore(tmp_path / 'tasks')
    store.save('t', claimed)
    waiting = execute_claimed_task(store, claimed, lambda: [ProviderHealth('openai')],
        {'openai': Limit, 'anthropic': Limit}, audit_path=tmp_path / 'audit',
        provider_health_path=health)
    assert datetime.fromisoformat(waiting['retry_at']) > datetime.now(UTC) + timedelta(minutes=59)
