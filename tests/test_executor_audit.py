import json

import pytest

from orchestrator.executor import execute_claimed_task
from orchestrator.store import JsonTaskStore
from providers.base import WorkerResult
from providers.health import ProviderHealth


class SuccessfulWorker:
    name = "anthropic"

    def execute(self, handoff: dict[str, object]) -> WorkerResult:
        return WorkerResult(output=f"finished {handoff['task_id']}")


class FailingWorker:
    name = "openai"

    def execute(self, handoff: dict[str, object]) -> WorkerResult:
        raise RuntimeError("connection unavailable")


def test_executor_persists_completion_and_audit(tmp_path) -> None:
    store = JsonTaskStore(tmp_path / "tasks")
    task = {"id": "TP-30", "status": "CLAIMED", "owner": {"provider": "anthropic"}}
    store.save("TP-30", task)

    completed = execute_claimed_task(
        store,
        task,
        lambda: [ProviderHealth("anthropic")],
        {"anthropic": SuccessfulWorker},
        audit_path=tmp_path / "audit.jsonl",
    )

    assert completed["status"] == "VALIDATING"
    assert completed["result"] == "finished TP-30"
    assert store.load("TP-30")["checkpoint"]["phase"] == "VALIDATING"
    events = [json.loads(line)["event"] for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert events == ["execution_started", "execution_awaiting_validation"]


def test_executor_requeues_interrupted_task(tmp_path) -> None:
    store = JsonTaskStore(tmp_path / "tasks")
    task = {"id": "TP-31", "status": "CLAIMED", "owner": {"provider": "openai"}}
    store.save("TP-31", task)

    with pytest.raises(RuntimeError, match="All configured providers failed"):
        execute_claimed_task(
            store,
            task,
            lambda: [ProviderHealth("openai")],
            {"openai": FailingWorker},
            audit_path=tmp_path / "audit.jsonl",
        )

    saved = store.load("TP-31")
    assert saved["status"] == "WAIT_RETRY"
    assert saved["next_action"] == {"type": "retry"}
