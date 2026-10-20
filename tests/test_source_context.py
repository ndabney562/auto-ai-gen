import json

import pytest

from orchestrator.engineering import MAX_SOURCE_BYTES
from orchestrator.store import JsonTaskStore
from orchestrator.subscription_runner import advance_task, create_task, git
from providers.base import WorkerResult
from providers.health import ProviderHealth


@pytest.mark.parametrize("content,blocked,repair", [
    ("a" * MAX_SOURCE_BYTES, False, False),
    ("a" * (MAX_SOURCE_BYTES + 1), True, False),
    ("é" * (MAX_SOURCE_BYTES // 2 + 1), True, False),
    ("a" * (MAX_SOURCE_BYTES + 1), False, True),
], ids=["exact-limit", "over-limit", "utf8-over-limit", "repair-context"])
def test_initial_source_boundary_is_durable_without_dispatch(tmp_path, content, blocked, repair):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    (repo / "docs").mkdir()
    (repo / "docs/a.md").write_text(content, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    state = tmp_path / "state"
    task_id = create_task(state, repo, "Update source", ["docs/a.md"], "HEAD")
    store = JsonTaskStore(state / "tasks")
    from orchestrator.task_queue import enqueue, health, select_task

    enqueue(state, task_id)
    if repair:
        task = store.load(task_id)
        task["checkpoint"]["repair_candidate_sha"] = git(repo, "rev-parse", "HEAD")
        store.save(task_id, task)
    before = store.load(task_id)
    calls = []

    class Worker:
        def execute(self, handoff):
            calls.append(handoff)
            return WorkerResult(json.dumps({"files": [{"path": "docs/a.md", "content": "pass\n"}],
                                            "summary": "Updated"}))

    kwargs = {"factories": {"openai": Worker}, "health": [ProviderHealth("openai")]}
    result = advance_task(state, task_id, **kwargs)
    assert result["status"] == ("BLOCKED" if blocked else "VALIDATING")
    assert len(calls) == (0 if blocked else 1)
    if blocked:
        assert result["checkpoint"] == before["checkpoint"]
        assert result["attempt"] == before["attempt"]
        assert result["next_action"] == {"type": "inspect_source_context",
                                         "reason": "source_size_limit", "target": "docs/a.md"}
        assert store.load(task_id) == result
        assert advance_task(state, task_id, **kwargs) == result
        assert not calls
        assert select_task(state) is None
        assert health(state, task_id)["failures"] == 0


def test_larger_blob_safety_failure_remains_an_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    (repo / "docs").mkdir()
    (repo / "docs/a.md").write_text("a" * 40001)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    state = tmp_path / "state"
    task_id = create_task(state, repo, "Update source", ["docs/a.md"], "HEAD")
    store = JsonTaskStore(state / "tasks")
    before = store.load(task_id)
    with pytest.raises(ValueError, match="Integration context exceeds"):
        advance_task(state, task_id, factories={"unused": lambda: pytest.fail("dispatch")},
                     health=[])
    assert store.load(task_id) == before
