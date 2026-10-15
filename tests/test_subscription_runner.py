import json
from pathlib import Path

import pytest

from orchestrator.store import JsonTaskStore
from orchestrator.subscription_runner import advance_task, candidate_commit, create_task, git
from providers.base import WorkerResult
from providers.errors import ClassifiedProviderError, ProviderExecutionError, ProviderFailure
from providers.health import ProviderHealth


@pytest.fixture
def setup_task(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init")
    (repo / "docs").mkdir()
    (repo / "docs/a.md").write_text("before")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    state = tmp_path / "state"
    task_id = create_task(state, repo, "Update A", ["docs/a.md"], "HEAD")
    return repo, state, task_id


class Implementer:
    def execute(self, handoff):
        return WorkerResult(json.dumps({"files": [{"path": "docs/a.md", "content": "after"}],
                                        "summary": "Updated A"}), session_id="implementation")


class Reviewer:
    def execute(self, handoff):
        payload = json.loads(handoff["prompt"].split("\n", 1)[1])
        return WorkerResult(json.dumps({"approved": True, "candidate_sha": payload["candidate_sha"],
                                       "reason": "Meets objective"}), session_id="review")


def validated(repo, sha, image):
    assert git(repo, "show", f"{sha}:docs/a.md") == "after"
    return {"passed": True, "candidate_sha": sha, "exit_code": 0, "log": "fixture validation"}


def test_durable_pipeline_binds_independent_review_to_exact_commit(setup_task):
    source, state, task_id = setup_task
    kwargs = {"factories": {"openai": Implementer, "anthropic": Reviewer},
                  "health": [ProviderHealth("openai"), ProviderHealth("anthropic")], "validator": validated}
    assert advance_task(state, task_id, **kwargs)["status"] == "VALIDATING"
    assert advance_task(state, task_id, **kwargs)["status"] == "REVIEW"
    task = advance_task(state, task_id, **kwargs)
    assert task["status"] == "WAIT_CI"
    checkpoint = task["checkpoint"]
    assert checkpoint["review"]["provider"] != checkpoint["implementation_provider"]
    assert checkpoint["review"]["candidate_sha"] == task["validation"]["candidate_sha"]
    assert git(Path(checkpoint["workspace"]), "rev-parse", "agent-candidate") == checkpoint["candidate_sha"]
    assert (source / "docs/a.md").read_text() == "before"
    assert (Path(checkpoint["workspace"]) / "docs/a.md").read_text() == "before"
    # Restart after review does not call either model or revalidate.
    assert advance_task(state, task_id, **kwargs) == task


def test_failover_never_allows_implementer_to_review_itself(setup_task):
    _, state, task_id = setup_task

    class QuotaFailure:
        def execute(self, handoff):
            raise ProviderExecutionError("quota", ClassifiedProviderError(
                ProviderFailure.QUOTA_EXHAUSTED, True))

    both = [ProviderHealth("openai"), ProviderHealth("anthropic")]
    factories = {"openai": QuotaFailure, "anthropic": Implementer}
    advance_task(state, task_id, factories=factories, health=both)
    advance_task(state, task_id, factories=factories, health=both, validator=validated)
    task = advance_task(state, task_id, factories=factories,
                        health=[ProviderHealth("anthropic")])
    assert task["status"] == "WAIT_PROVIDER"
    assert task["checkpoint"]["resume_phase"] == "REVIEW"
    # Recovery restores REVIEW; it cannot dispatch the available implementer.
    task = advance_task(state, task_id, factories=factories,
                        health=[ProviderHealth("anthropic")])
    assert task["status"] == "REVIEW"


def test_unavailable_container_requires_human_setup_without_done(setup_task):
    _, state, task_id = setup_task
    kwargs = {"factories": {"openai": Implementer}, "health": [ProviderHealth("openai")]}
    advance_task(state, task_id, **kwargs)

    def unavailable(*args):
        raise FileNotFoundError("docker")

    task = advance_task(state, task_id, validator=unavailable, **kwargs)
    assert task["status"] == "WAIT_HUMAN"
    assert task["checkpoint"]["candidate_sha"]
    assert task["next_action"]["type"] == "configure_isolated_validator"


def test_invalid_python_cannot_create_candidate(setup_task):
    _, state, task_id = setup_task
    task = JsonTaskStore(state / "tasks").load(task_id)
    repo = Path(task["checkpoint"]["workspace"])
    base = task["checkpoint"]["base_sha"]
    with pytest.raises(SyntaxError):
        candidate_commit(repo, base, ["docs/a.py"], json.dumps({
            "files": [{"path": "docs/a.py", "content": "def broken("}]}))
    assert git(repo, "rev-parse", "HEAD") == base


def test_rejected_review_triggers_bounded_repair(setup_task):
    _, state, task_id = setup_task

    class Reject:
        def execute(self, handoff):
            return WorkerResult('{"approved":false,"reason":"incomplete"}')

    kwargs = {"factories": {"openai": Implementer, "anthropic": Reject},
                  "health": [ProviderHealth("openai"), ProviderHealth("anthropic")], "validator": validated}
    for _ in range(3):
        assert advance_task(state, task_id, **kwargs)["status"] == "VALIDATING"
        assert advance_task(state, task_id, **kwargs)["status"] == "REVIEW"
        assert advance_task(state, task_id, **kwargs)["status"] == "REPAIR"
        assert advance_task(state, task_id, **kwargs)["status"] == "READY"
    task = advance_task(state, task_id, **kwargs)
    assert task["status"] == "FAILED_PERMANENT"


def test_publishing_requires_exact_review_and_merge_confirmation(setup_task):
    from orchestrator.subscription_runner import publish_task

    _, state, task_id = setup_task
    kwargs = {"factories": {"openai": Implementer, "anthropic": Reviewer},
              "health": [ProviderHealth("openai"), ProviderHealth("anthropic")],
              "validator": validated}
    for _ in range(3):
        task = advance_task(state, task_id, **kwargs)

    class Publisher:
        def publish(self, workspace, task_id, sha):
            assert sha == task["checkpoint"]["candidate_sha"]
            return {"number": 1, "url": "https://github.com/example/repo/pull/1"}

        def ci_failure(self, sha):
            return None

        def verified_merge(self, number, sha, base_sha):
            return False

    publisher = Publisher()
    assert publish_task(state, task_id, "example/repo", merge=True,
                        publisher=publisher)["status"] == "WAIT_CI"
    publisher.verified_merge = lambda *args: True
    assert publish_task(state, task_id, "example/repo", merge=True,
                        publisher=publisher)["status"] == "DONE"


def test_publisher_rejects_self_review_record(setup_task):
    from orchestrator.subscription_runner import publish_task

    _, state, task_id = setup_task
    store = JsonTaskStore(state / "tasks")
    task = store.load(task_id)
    task.update(status="WAIT_CI", validation={"passed": True, "candidate_sha": "a"})
    task["checkpoint"].update(candidate_sha="a", implementation_provider="openai",
                              review={"candidate_sha": "a", "provider": "openai"})
    store.save(task_id, task)
    with pytest.raises(ValueError, match="independent review"):
        publish_task(state, task_id, "example/repo")


def test_isolated_validator_mounts_no_credentials_and_disables_network(tmp_path, monkeypatch):
    import io
    import subprocess
    import tarfile

    from orchestrator.subscription_runner import docker_validate

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        item = tarfile.TarInfo("safe.txt")
        item.size = 4
        archive.addfile(item, io.BytesIO(b"safe"))
    observed = []

    def run(command, **kwargs):
        if len(command) > 1 and command[1].endswith("_cli_guard.py"):
            command = command[4:]
        observed.append(command)
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0, stdout=buffer.getvalue())
        return subprocess.CompletedProcess(command, 0, stdout="passed", stderr="")

    monkeypatch.setattr("orchestrator.subscription_runner.subprocess.run", run)
    result = docker_validate(tmp_path, "a" * 40, "trusted-image")
    command = next(c for c in observed if c[:2] == ["docker", "run"])
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--pull=never" in command
    assert "timeout" in command
    assert command.count("--mount") == 1
    assert "readonly" in command[command.index("--mount") + 1]
    assert not any(".codex" in arg or "docker.sock" in arg for arg in command)
    assert observed[-1][:3] == ["docker", "rm", "-f"]
    assert result["passed"]


def test_repair_clears_previous_reviewer_resume_phase(setup_task):
    from orchestrator.subscription_runner import _repair

    _, state, task_id = setup_task
    store = JsonTaskStore(state / "tasks")
    task = store.load(task_id)
    task["status"] = "REVIEW"
    task["checkpoint"].update(candidate_sha="old", resume_phase="REVIEW", review_attempt=2,
                              review={"approved": True, "candidate_sha": "old"})
    store.save(task_id, task)
    repaired = _repair(store, task, dict(task["checkpoint"]), "Needs another implementation")
    assert repaired["status"] == "REPAIR"
    for key in ("candidate_sha", "resume_phase", "review_attempt", "review"):
        assert key not in repaired["checkpoint"]
