from __future__ import annotations

import json
import sys
from dataclasses import replace

import pytest

from providers.cli_provider import (
    CliError,
    CliWorker,
    _claude_result,
    _codex_result,
    claude_subscription_worker,
    codex_subscription_worker,
    subscription_environment,
)
from providers.errors import ProviderFailure


def test_cli_worker_uses_stdin_and_scrubs_credentials(tmp_path, monkeypatch):
    script = tmp_path / "worker.py"
    script.write_text('''import os,sys,json
prompt=sys.stdin.read()
assert 'OPENAI_API_KEY' not in os.environ
assert 'GH_TOKEN' not in os.environ
print(json.dumps({'prompt':prompt,'cwd':os.getcwd()}))
''')
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-forward")
    monkeypatch.setenv("GH_TOKEN", "do-not-forward")
    worker = CliWorker("test", (sys.executable, str(script)), 9)
    result = json.loads(worker.execute({"prompt": "bounded task"}).output)
    assert result["prompt"] == "bounded task"
    assert "agent-cli-" in result["cwd"]


def test_subscription_environment_excludes_cloud_and_api_overrides(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "AWS_SECRET_ACCESS_KEY",
                 "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_AUTH_TOKEN", "GIT_CONFIG_COUNT"):
        monkeypatch.setenv(name, "secret")
    assert not set(subscription_environment()) & {
        "ANTHROPIC_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "AWS_SECRET_ACCESS_KEY",
        "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_AUTH_TOKEN", "GIT_CONFIG_COUNT"}


def test_codex_jsonl_requires_terminal_success_and_extracts_session():
    events = [
        {"type": "thread.started", "thread_id": "session-1"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"files":[]}'}},
        {"type": "turn.completed"},
    ]
    result = _codex_result("\n".join(map(json.dumps, events)))
    assert result.session_id == "session-1"
    assert result.output == '{"files":[]}'
    with pytest.raises(CliError):
        _codex_result(json.dumps(events[1]))


def test_claude_error_payload_is_not_success_even_on_zero_exit():
    with pytest.raises(CliError) as raised:
        _claude_result('{"subtype":"error","is_error":true,"result":"usage exhausted"}')
    assert raised.value.classified.kind == ProviderFailure.QUOTA_EXHAUSTED
    result = _claude_result('{"subtype":"success","result":"ok","session_id":"s"}')
    assert result.session_id == "s"


def test_cli_timeout_kills_process_and_classifies_retryable_outage():
    worker = CliWorker("test", (sys.executable, "-c", "import time;time.sleep(60)"), .05)
    with pytest.raises(CliError) as raised:
        worker.execute({"prompt": "test"})
    assert raised.value.classified.retryable


def test_login_check_rejects_api_billing_before_model_call(monkeypatch):
    calls = []

    def fake_run(self, command, prompt, cwd):
        calls.append(command)
        return 0, "Logged in using an API key", ""

    monkeypatch.setattr(CliWorker, "_run", fake_run)
    with pytest.raises(CliError):
        codex_subscription_worker().execute({"prompt": "test"})
    assert len(calls) == 1


def test_claude_login_requires_pro_or_max_subscription(monkeypatch):
    calls = []

    def fake_run(self, command, prompt, cwd):
        calls.append(command)
        return 0, json.dumps({"loggedIn": True, "authMethod": "api_key"}), ""

    monkeypatch.setattr(CliWorker, "_run", fake_run)
    with pytest.raises(CliError):
        claude_subscription_worker().execute({"prompt": "test"})
    assert len(calls) == 1


def test_subscription_commands_disable_tools_and_configuration():
    codex, claude = codex_subscription_worker(), claude_subscription_worker()
    assert codex.command[:2] == ("codex", "exec")
    assert "--full-auto" not in codex.command
    assert "--ignore-user-config" in codex.command
    assert "read-only" in codex.command
    assert claude.command[:2] == ("claude", "-p")
    assert "--safe-mode" in claude.command
    assert "--bare" not in claude.command
    assert claude.command[claude.command.index("--tools") + 1] == ""


def test_actual_cli_process_protocol_without_paid_calls(tmp_path):
    script = tmp_path / "codex"
    script.write_text('''#!/usr/bin/env python3
import sys,json
if sys.argv[1:]==['login','status']:
    print('Logged in using ChatGPT')
else:
    assert sys.stdin.read()=='implement'
    for event in [{'type':'thread.started','thread_id':'fixture'},
                  {'type':'item.completed','item':{'type':'agent_message','text':'proposal'}},
                  {'type':'turn.completed'}]:
        print(json.dumps(event))
''')
    script.chmod(0o755)
    worker = codex_subscription_worker()
    worker = replace(worker, command=(str(script), *worker.command[1:]))
    assert worker.execute({"prompt": "implement"}).session_id == "fixture"


def test_subscription_environment_keeps_user_for_macos_claude_keychain(monkeypatch):
    monkeypatch.setenv("USER", "pilot-user")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "never-forward")
    environment = subscription_environment()
    assert environment["USER"] == "pilot-user"
    assert "ANTHROPIC_API_KEY" not in environment
