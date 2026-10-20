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


def test_failover_never_allows_implementer_to_review_itself(setup_task, monkeypatch):
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
    # Stay paused until the durable cooldown expires, then restore REVIEW;
    # the available implementer cannot review itself.
    from datetime import datetime, timedelta

    import orchestrator.subscription_runner as runner
    from providers import health as provider_health

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(task["retry_at"]).astimezone(tz) + timedelta(seconds=1)

    monkeypatch.setattr(runner, "datetime", Clock)
    monkeypatch.setattr(provider_health, "datetime", Clock)
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


def test_repair_receives_failed_candidate_instead_of_original_source(setup_task):
    from orchestrator.subscription_runner import _repair

    _, state, task_id = setup_task
    kwargs = {"factories": {"openai": Implementer}, "health": [ProviderHealth("openai")],
              "validator": validated}
    advance_task(state, task_id, **kwargs)
    task = advance_task(state, task_id, **kwargs)
    store = JsonTaskStore(state / "tasks")
    repaired = _repair(store, task, dict(task["checkpoint"]), "CI fixture failure")
    assert repaired["validation"] is None
    assert repaired["checkpoint"]["repair_candidate_sha"] == task["checkpoint"]["candidate_sha"]
    advance_task(state, task_id, **kwargs)

    class RepairWorker:
        def execute(self, handoff):
            request = json.loads(handoff["prompt"].split("\n", 1)[1])
            assert request["sources"]["docs/a.md"] == "after"
            assert request["failures"] == ["CI fixture failure"]
            return Implementer().execute(handoff)

    result = advance_task(state, task_id, factories={"openai": RepairWorker},
                          health=[ProviderHealth("openai")])
    assert result["status"] == "VALIDATING"
    advance_task(state, task_id, validator=validated)

    class RepairReviewer:
        def execute(self, handoff):
            request = json.loads(handoff["prompt"].split("\n", 1)[1])
            assert request["previous_failures"] == ["CI fixture failure"]
            return Reviewer().execute(handoff)

    reviewed = advance_task(state, task_id, factories={"anthropic": RepairReviewer},
                            health=[ProviderHealth("anthropic")])
    assert reviewed["status"] == "WAIT_CI"


def test_review_attempt_is_durable_before_request_and_stops_at_budget(setup_task):
    _, state, task_id = setup_task
    kwargs = {"factories": {"openai": Implementer}, "health": [ProviderHealth("openai")],
              "validator": validated}
    advance_task(state, task_id, **kwargs)
    advance_task(state, task_id, **kwargs)
    store = JsonTaskStore(state / "tasks")

    class InterruptedReview:
        def execute(self, handoff):
            assert store.load(task_id)["checkpoint"]["review_attempt"] > 0
            raise KeyboardInterrupt

    for attempt in range(1, 4):
        with pytest.raises(KeyboardInterrupt):
            advance_task(state, task_id, factories={"anthropic": InterruptedReview},
                          health=[ProviderHealth("anthropic")])
        assert store.load(task_id)["status"] == "REVIEW"
        assert store.load(task_id)["checkpoint"]["review_attempt"] == attempt
    result = advance_task(state, task_id, factories={"anthropic": InterruptedReview},
                          health=[ProviderHealth("anthropic")])
    assert result["status"] == "WAIT_HUMAN"
    assert result["next_action"]["type"] == "review_attempts_exhausted"


def test_supervised_merge_is_reconciled_without_requesting_merge(setup_task):
    from orchestrator.subscription_runner import publish_task

    _, state, task_id = setup_task
    kwargs = {"factories": {"openai": Implementer, "anthropic": Reviewer},
              "health": [ProviderHealth("openai"), ProviderHealth("anthropic")],
              "validator": validated}
    for _ in range(3):
        advance_task(state, task_id, **kwargs)

    class Publisher:
        def publish(self, workspace, task_id, sha):
            return {"number": 1, "headRefOid": sha, "state": "MERGED"}

        def ci_failure(self, sha):
            pytest.fail("An already merged candidate cannot enter CI repair")

        def verified_merge(self, *_):
            pytest.fail("Must not request a merge in supervised mode")

    task = publish_task(state, task_id, "example/repo", publisher=Publisher())
    assert task["status"] == "DONE"
    assert task["checkpoint"]["merged_candidate_sha"] == task["checkpoint"]["candidate_sha"]


def test_review_limit_wait_survives_restart_and_resumes_exact_candidate(setup_task, monkeypatch):
    from datetime import UTC, datetime, timedelta

    import orchestrator.subscription_runner as runner
    from orchestrator import provider_state, supervisor
    from providers import health as provider_health
    from providers import subscription_limits
    from providers.cli_provider import _claude_result

    current = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz)

    for module in (runner, provider_state, provider_health, subscription_limits):
        monkeypatch.setattr(module, 'datetime', Clock)
    _, state, task_id = setup_task
    factories = {'openai': Implementer}
    both = [ProviderHealth('openai'), ProviderHealth('anthropic')]
    advance_task(state, task_id, factories=factories, health=both)
    original = advance_task(state, task_id, factories=factories, health=both, validator=validated)
    calls = []

    class LimitThenReview:
        def execute(self, handoff):
            calls.append(1)
            if len(calls) <= 5:
                _claude_result(json.dumps({'subtype': 'error', 'is_error': True,
                    'result': "You've hit your weekly limit; secret-diagnostic",
                    'reset_at': (current[0] + timedelta(hours=6)).isoformat()}))
            return Reviewer().execute(handoff)

    factories = {'anthropic': LimitThenReview}
    store = JsonTaskStore(state / 'tasks')
    for count in range(1, 6):
        waiting = advance_task(state, task_id, factories=factories, health=both)
        assert waiting['status'] == 'WAIT_PROVIDER'
        assert waiting['checkpoint']['review_attempt'] == 0
        assert waiting['checkpoint']['candidate_sha'] == original['checkpoint']['candidate_sha']
        assert waiting['validation'] == original['validation']
        assert waiting['checkpoint']['resume_phase'] == 'REVIEW'
        expiry = datetime.fromisoformat(waiting['retry_at'])
        assert expiry > current[0] + timedelta(hours=6)
        # A fresh store/driver represents process restart, with no hidden session.
        assert JsonTaskStore(state / 'tasks').load(task_id) == waiting
        current[0] = expiry - timedelta(seconds=1)
        assert advance_task(state, task_id, factories=factories, health=both) == waiting
        supervisor.configure(state, task_id, 'owner/repo', 'sha256:' + 'a' * 64)
        config = supervisor.records(state).load('config')
        supervisor.tick(state, config, lambda: False,
                        phase=lambda *_: pytest.fail('must not launch before reset'))
        assert len(calls) == count
        assert supervisor.status(state)['retry_at'] == waiting['retry_at']
        assert supervisor.status(state)['waiting_provider'] == 'anthropic'
        assert 'secret-diagnostic' not in (state / 'provider-health.json').read_text()
        assert 'secret-diagnostic' not in str(store.load(task_id))
        current[0] = expiry + timedelta(seconds=1)
        assert advance_task(state, task_id, factories=factories, health=both)['status'] == 'REVIEW'
    reviewed = advance_task(state, task_id, factories=factories, health=both)
    assert len(calls) == 6
    assert reviewed['status'] == 'WAIT_CI'
    assert reviewed['checkpoint']['review']['provider'] == 'anthropic'
    assert reviewed['checkpoint']['review']['candidate_sha'] == original['checkpoint']['candidate_sha']
    assert reviewed['checkpoint']['review_attempt'] == 1
    assert reviewed['attempt'] == original['attempt']


def test_nonlimit_review_errors_still_exhaust_budget(setup_task, monkeypatch):
    from datetime import UTC, datetime, timedelta

    import orchestrator.subscription_runner as runner
    from orchestrator import provider_state
    from providers import health as provider_health

    current = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz)

    for module in (runner, provider_state, provider_health):
        monkeypatch.setattr(module, 'datetime', Clock)
    _, state, task_id = setup_task
    kwargs = {'factories': {'openai': Implementer}, 'health': [ProviderHealth('openai')],
              'validator': validated}
    advance_task(state, task_id, **kwargs)
    advance_task(state, task_id, **kwargs)

    class Outage:
        def execute(self, handoff):
            raise ProviderExecutionError('outage', ClassifiedProviderError(ProviderFailure.OUTAGE, True))

    for count in range(1, 4):
        task = advance_task(state, task_id, factories={'anthropic': Outage},
                            health=[ProviderHealth('anthropic')])
        assert task['checkpoint']['review_attempt'] == count
        if count < 3:
            assert task['status'] == 'WAIT_RETRY'
            current[0] += timedelta(minutes=2)
            assert advance_task(state, task_id)['status'] == 'REVIEW'
    assert task['status'] == 'WAIT_HUMAN'
    assert task['next_action']['type'] == 'inspect_review_failure'


@pytest.mark.parametrize('wrapper', ['plain', 'json-fence', 'bare-fence', 'crlf-fence'])
def test_fenced_independent_review_keeps_exact_sha_gate(setup_task, wrapper):
    _, state, task_id = setup_task
    class FencedReviewer(Reviewer):
        def execute(self, handoff):
            result = super().execute(handoff)
            text = result.output
            if wrapper != 'plain':
                newline = '\r\n' if wrapper == 'crlf-fence' else '\n'
                label = '' if wrapper == 'bare-fence' else 'json'
                text = '```' + label + newline + text + newline + '```'
            return WorkerResult(text, session_id='independent-review')
    kwargs = {'factories': {'openai': Implementer, 'anthropic': FencedReviewer},
              'health': [ProviderHealth('openai'), ProviderHealth('anthropic')],
              'validator': validated}
    advance_task(state, task_id, **kwargs)
    advance_task(state, task_id, **kwargs)
    result = advance_task(state, task_id, **kwargs)
    assert result['status'] == 'WAIT_CI'
    assert result['checkpoint']['review']['provider'] == 'anthropic'
    assert result['checkpoint']['review']['candidate_sha'] == result['validation']['candidate_sha']
    assert not (state / 'provider-health.json').exists()


@pytest.mark.parametrize('text', [
    'prose\n```json\n{}\n```', '```json\n{}\n```\nprose',
    '```json\n{}\n```\n```json\n{}\n```', '```python\n{}\n```',
    '```json\n{}', '[]', 'null', '{broken', None])
def test_review_parser_does_not_extract_prose_or_nonobjects(text):
    from orchestrator.subscription_runner import InvalidReviewResponse, parse_review_response
    with pytest.raises(InvalidReviewResponse):
        parse_review_response(text)


def test_invalid_review_format_exhausts_without_fake_provider_outage(setup_task):
    _, state, task_id = setup_task
    store = JsonTaskStore(state / 'tasks')
    class BrokenReviewer:
        def execute(self, handoff):
            return WorkerResult('private-invalid-prose')
    kwargs = {'factories': {'openai': Implementer, 'anthropic': BrokenReviewer},
              'health': [ProviderHealth('openai'), ProviderHealth('anthropic')],
              'validator': validated}
    advance_task(state, task_id, **kwargs)
    advance_task(state, task_id, **kwargs)
    for count in range(1, 4):
        result = advance_task(state, task_id, **kwargs)
        assert result['checkpoint']['review_attempt'] == count
        assert result['checkpoint']['review_error'] == {'kind': 'invalid_review_response'}
        if count < 3:
            assert result['status'] == 'WAIT_RETRY'
            result.pop('retry_at')
            store.save(task_id, result)
            assert advance_task(state, task_id, **kwargs)['status'] == 'REVIEW'
    assert result['status'] == 'WAIT_HUMAN'
    assert result['next_action']['type'] == 'inspect_review_response'
    assert 'private-invalid-prose' not in json.dumps(result)
    assert 'private-invalid-prose' not in (state / 'tasks' / f'{task_id}.json').read_text()
    assert 'private-invalid-prose' not in (state / 'audit.jsonl').read_text()
    assert not (state / 'provider-health.json').exists()


@pytest.mark.parametrize('approved,sha', [(True, 'wrong-sha'), ('true', None), (False, None)])
def test_fenced_review_never_weakens_approval_or_sha(setup_task, approved, sha):
    _, state, task_id = setup_task
    class BadApproval(Reviewer):
        def execute(self, handoff):
            result = json.loads(super().execute(handoff).output)
            result.update(approved=approved, candidate_sha=sha or result['candidate_sha'])
            return WorkerResult('```json\n' + json.dumps(result) + '\n```')
    kwargs = {'factories': {'openai': Implementer, 'anthropic': BadApproval},
              'health': [ProviderHealth('openai'), ProviderHealth('anthropic')], 'validator': validated}
    advance_task(state, task_id, **kwargs)
    advance_task(state, task_id, **kwargs)
    assert advance_task(state, task_id, **kwargs)['status'] == 'REPAIR'


def test_fenced_review_preserves_unicode_separator_inside_json_string():
    from orchestrator.subscription_runner import parse_review_response
    payload = {'approved': True, 'candidate_sha': 'exact', 'reason': 'first\u2028second'}
    text = '```json\n' + json.dumps(payload, ensure_ascii=False) + '\n```'
    assert parse_review_response(text) == payload


def test_review_rejects_duplicate_approval_keys():
    from orchestrator.subscription_runner import InvalidReviewResponse, parse_review_response
    with pytest.raises(InvalidReviewResponse):
        parse_review_response('```json\n{"approved":false,"approved":true}\n```')


def test_review_deep_nesting_is_format_error():
    from orchestrator.subscription_runner import InvalidReviewResponse, parse_review_response
    with pytest.raises(InvalidReviewResponse):
        parse_review_response('[' * 100000)


def test_review_auth_failure_keeps_authentication_action(setup_task):
    _, state, task_id = setup_task
    class AuthError:
        def execute(self, handoff):
            raise ProviderExecutionError('private', ClassifiedProviderError(
                ProviderFailure.AUTH_FAILURE, False))
    kwargs = {'factories': {'openai': Implementer, 'anthropic': AuthError},
              'health': [ProviderHealth('openai'), ProviderHealth('anthropic')],
              'validator': validated}
    advance_task(state, task_id, **kwargs)
    advance_task(state, task_id, **kwargs)
    result = advance_task(state, task_id, **kwargs)
    assert result['status'] == 'WAIT_HUMAN'
    assert result['next_action']['type'] == 'repair_review_authentication'


def test_valid_fenced_review_after_format_error_clears_metadata(setup_task):
    _, state, task_id = setup_task
    class FirstInvalid(Reviewer):
        count = 0
        def execute(self, handoff):
            type(self).count += 1
            if self.count == 1:
                return WorkerResult('invalid-private-response')
            r = super().execute(handoff)
            return WorkerResult('```json\n' + r.output + '\n```')
    kwargs = {'factories': {'openai': Implementer, 'anthropic': FirstInvalid},
              'health': [ProviderHealth('openai'), ProviderHealth('anthropic')],
              'validator': validated}
    advance_task(state, task_id, **kwargs)
    advance_task(state, task_id, **kwargs)
    task = advance_task(state, task_id, **kwargs)
    assert task['status'] == 'WAIT_RETRY'
    task.pop('retry_at')
    JsonTaskStore(state / 'tasks').save(task_id, task)
    assert advance_task(state, task_id, **kwargs)['status'] == 'REVIEW'
    result = advance_task(state, task_id, **kwargs)
    assert result['status'] == 'WAIT_CI'
    assert result['checkpoint']['review_attempt'] == 2
    assert 'review_error' not in result['checkpoint']
    assert not (state / 'provider-health.json').exists()
    assert 'invalid-private-response' not in (state / 'tasks' / f'{task_id}.json').read_text()


@pytest.mark.parametrize('kind,retryable,status,action,attempts', [
    (ProviderFailure.AUTH_FAILURE, False, 'WAIT_HUMAN', 'repair_review_authentication', 2),
    (ProviderFailure.OUTAGE, True, 'WAIT_RETRY', 'retry_review', 2),
    (ProviderFailure.QUOTA_EXHAUSTED, True, 'WAIT_PROVIDER', 'wait_for_subscription_reset', 1),
])
def test_provider_error_after_invalid_review_clears_stale_metadata(
        setup_task, kind, retryable, status, action, attempts):
    from datetime import UTC, datetime, timedelta

    _, state, task_id = setup_task
    store = JsonTaskStore(state / 'tasks')
    class MixedReviewer:
        calls = 0
        def execute(self, handoff):
            type(self).calls += 1
            if self.calls == 1:
                return WorkerResult('private-invalid-review')
            raise ProviderExecutionError('private-provider-error', ClassifiedProviderError(
                kind, retryable, 180 if kind == ProviderFailure.QUOTA_EXHAUSTED else None,
                'private-provider-detail'))
    kwargs = {'factories': {'openai': Implementer, 'anthropic': MixedReviewer},
              'health': [ProviderHealth('openai'), ProviderHealth('anthropic')],
              'validator': validated}
    advance_task(state, task_id, **kwargs)
    advance_task(state, task_id, **kwargs)
    malformed = advance_task(state, task_id, **kwargs)
    assert malformed['status'] == 'WAIT_RETRY'
    assert malformed['checkpoint']['review_attempt'] == 1
    assert malformed['checkpoint']['review_error'] == {'kind': 'invalid_review_response'}
    assert not (state / 'provider-health.json').exists()
    malformed.pop('retry_at')
    store.save(task_id, malformed)
    assert advance_task(state, task_id, **kwargs)['status'] == 'REVIEW'
    before = datetime.now(UTC)
    result = advance_task(state, task_id, **kwargs)
    assert result['status'] == status
    assert result['next_action']['type'] == action
    assert result['checkpoint']['review_attempt'] == attempts
    assert result['attempt'] == malformed['attempt']
    assert 'review_error' not in result['checkpoint']
    assert result['checkpoint']['resume_phase'] == 'REVIEW'
    health = json.loads((state / 'provider-health.json').read_text())['anthropic']
    assert health['state'] == kind.value
    if kind == ProviderFailure.QUOTA_EXHAUSTED:
        assert datetime.fromisoformat(result['retry_at']) >= before + timedelta(seconds=180)
        assert result['retry_at'] == health['retry_at']
    for path in (state / 'tasks' / f'{task_id}.json', state / 'provider-health.json',
                 state / 'audit.jsonl'):
        text = path.read_text()
        assert 'private-invalid-review' not in text
        assert 'private-provider-error' not in text
        assert 'private-provider-detail' not in text
    assert MixedReviewer.calls == 2
