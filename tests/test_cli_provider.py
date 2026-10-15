from __future__ import annotations

from subprocess import CompletedProcess

import pytest

from providers.cli_provider import (
    CliWorker,
    claude_subscription_worker,
    codex_subscription_worker,
)


def test_cli_worker_serializes_handoff_and_returns_output(monkeypatch):
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return CompletedProcess(command, 0, stdout="done\n", stderr="")

    monkeypatch.setattr("providers.cli_provider.subprocess.run", fake_run)
    worker = CliWorker(name="test", command=("agent", "run"), timeout_seconds=9)

    result = worker.execute({"task_id": "t-1", "goal": "fix lint"})

    assert result.output == "done"
    assert observed["command"][:2] == ["agent", "run"]
    assert '"task_id": "t-1"' in observed["command"][-1]
    assert observed["kwargs"]["timeout"] == 9


def test_cli_worker_fails_closed_on_nonzero_exit(monkeypatch):
    def fake_run(command, **kwargs):
        return CompletedProcess(command, 2, stdout="", stderr="usage exhausted")

    monkeypatch.setattr("providers.cli_provider.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="usage exhausted"):
        CliWorker(name="test", command=("agent",)).execute({"task_id": "t-1"})


def test_subscription_worker_commands_do_not_embed_api_keys():
    codex = codex_subscription_worker()
    claude = claude_subscription_worker()

    assert codex.command[:2] == ("codex", "exec")
    assert claude.command[:2] == ("claude", "-p")
    assert all("API_KEY" not in part for part in (*codex.command, *claude.command))
