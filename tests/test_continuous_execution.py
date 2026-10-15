from datetime import UTC, datetime, timedelta

from orchestrator.loop import run_scheduler_loop
from orchestrator.store import JsonTaskStore
from providers.base import WorkerResult
from providers.health import ProviderHealth


class SuccessfulWorker:
    name = "anthropic"

    def execute(self, handoff: dict[str, object]) -> WorkerResult:
        return WorkerResult(output=f"completed {handoff['task_id']}")


def test_loop_dispatches_and_executes_ready_task(tmp_path) -> None:
    store = JsonTaskStore(tmp_path / "tasks")
    store.save("TP-40", {"id": "TP-40", "status": "READY", "dependencies": []})

    run_scheduler_loop(
        store,
        lambda: [ProviderHealth("anthropic")],
        factories={"anthropic": SuccessfulWorker},
        audit_path=tmp_path / "audit.jsonl",
        poll_seconds=0,
        max_ticks=1,
    )

    task = store.load("TP-40")
    assert task["status"] == "VALIDATING"
    assert task["result"] == "completed TP-40"


def test_loop_recovers_expired_execution_then_completes(tmp_path) -> None:
    store = JsonTaskStore(tmp_path / "tasks")
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store.save(
        "TP-41",
        {
            "id": "TP-41",
            "status": "EXECUTING",
            "dependencies": [],
            "lease": {"expires_at": expired},
            "checkpoint": {"completed_work": ["step one"]},
        },
    )

    run_scheduler_loop(
        store,
        lambda: [ProviderHealth("anthropic")],
        factories={"anthropic": SuccessfulWorker},
        audit_path=tmp_path / "audit.jsonl",
        poll_seconds=0,
        max_ticks=1,
    )

    task = store.load("TP-41")
    assert task["status"] == "VALIDATING"
    assert task["result"] == "completed TP-41"
