import json

import pytest

from orchestrator.github_publish import GitHubPublisher


def publisher(monkeypatch, *, ci="success", main="base", head="candidate", step="success"):
    worker = GitHubPublisher("example/repo")
    calls = []

    def gh(*args):
        calls.append(args)
        path = args[1] if args[1] != "--method" else args[3]
        if path.endswith("/protection"):
            return json.dumps({"required_status_checks": {"strict": True, "contexts": ["verify"]},
                               "enforce_admins": {"enabled": True}})
        if path.endswith("/merge"):
            return '{"merged":true}'
        if "/pulls/" in path:
            return json.dumps({"head": {"sha": head, "repo": {"full_name": "example/repo"}},
                               "base": {"ref": "main"}, "draft": False, "state": "open",
                               "mergeable": True})
        if "git/ref" in path:
            return json.dumps({"object": {"sha": main}})
        if "/jobs?" in path:
            return json.dumps({"jobs": [{"name": "verify", "conclusion": "success", "steps": [
                {"name": "Lint", "conclusion": "success"}, {"name": "Test", "conclusion": step}]}]})
        return json.dumps({"workflow_runs": [{"name": "CI", "head_sha": "candidate",
            "event": "pull_request", "path": ".github/workflows/ci.yml", "run_number": 1,
            "id": 1, "status": "completed", "conclusion": ci}]})

    monkeypatch.setattr(worker, "_gh", gh)
    return worker, calls


def test_merge_is_bound_to_candidate_and_green_steps(monkeypatch):
    worker, calls = publisher(monkeypatch)
    assert worker.verified_merge(1, "candidate", "base")
    assert "sha=candidate" in calls[-1]


@pytest.mark.parametrize("kwargs", [{"ci": "failure"}, {"step": "failure"}])
def test_failed_checks_never_merge(monkeypatch, kwargs):
    worker, calls = publisher(monkeypatch, **kwargs)
    assert not worker.verified_merge(1, "candidate", "base")
    assert not any("PUT" in call for call in calls)


@pytest.mark.parametrize("kwargs", [{"head": "moved"}, {"main": "advanced"}])
def test_changed_head_or_main_requires_revalidation(monkeypatch, kwargs):
    worker, calls = publisher(monkeypatch, **kwargs)
    with pytest.raises(ValueError):
        worker.verified_merge(1, "candidate", "base")
    assert not any("PUT" in call for call in calls)


def test_auto_merge_requires_server_enforced_strict_checks(monkeypatch):
    worker, calls = publisher(monkeypatch)
    previous = worker._gh

    def gh(*args):
        if args[-1].endswith("/protection"):
            return '{}'
        return previous(*args)

    monkeypatch.setattr(worker, "_gh", gh)
    with pytest.raises(ValueError, match="strict verify"):
        worker.verified_merge(1, "candidate", "base")
    assert not any("PUT" in call for call in calls)
