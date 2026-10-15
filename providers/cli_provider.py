from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass

from providers.base import WorkerResult
from providers.errors import ClassifiedProviderError, ProviderExecutionError, ProviderFailure


class CliError(ProviderExecutionError):
    pass


def _failure(detail: str) -> CliError:
    text = detail.lower()
    if any(word in text for word in ("not logged in", "authentication", "unauthorized", "login")):
        kind, retry = ProviderFailure.AUTH_FAILURE, False
    elif any(word in text for word in ("usage limit", "quota", "usage exhausted")):
        kind, retry = ProviderFailure.QUOTA_EXHAUSTED, True
    elif any(word in text for word in ("rate limit", "rate_limit", "429")):
        kind, retry = ProviderFailure.RATE_LIMITED, True
    elif any(word in text for word in ("unknown option", "unexpected argument", "invalid request")):
        kind, retry = ProviderFailure.INVALID_REQUEST, False
    else:
        kind, retry = ProviderFailure.OUTAGE, True
    # Raw CLI diagnostics may contain credentials or prompt text. Never persist them.
    return CliError(f"Coding CLI failed: {kind.value}", ClassifiedProviderError(kind, retry))


def subscription_environment() -> dict[str, str]:
    """Keep account-login locations, exclude API/cloud credentials and paid overrides."""
    allowed = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "USER"}
    return {key: value for key, value in os.environ.items() if key in allowed}


@dataclass(frozen=True, slots=True)
class CliWorker:
    """Subscription-authenticated, tool-disabled proposal/review worker.

    The coordinator supplies source context and applies reviewed patches. These
    credential-bearing processes never run model-generated shell or Python.
    """

    name: str
    command: tuple[str, ...]
    timeout_seconds: int = 1800

    def _run(self, command: list[str], prompt: str, cwd: str) -> tuple[int, str, str]:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd, env=subscription_environment(), start_new_session=True,
        )
        try:
            out, err = process.communicate(prompt, timeout=self.timeout_seconds)
        except BaseException:
            # Kill descendants too: a timed-out CLI must not keep working after failover.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise
        return process.returncode, out, err

    def _check_login(self, cwd: str) -> None:
        executable = self.command[0]
        if self.name == "openai-codex-cli":
            code, out, err = self._run([executable, "login", "status"], "", cwd)
            valid = code == 0 and "chatgpt" in (out + err).lower()
        elif self.name == "anthropic-claude-cli":
            code, out, err = self._run([executable, "auth", "status"], "", cwd)
            try:
                status = json.loads(out)
                valid = (code == 0 and status.get("loggedIn") is True
                         and status.get("authMethod") == "claude.ai"
                         and status.get("subscriptionType") in {"pro", "max"})
            except (ValueError, AttributeError):
                valid = False
        else:
            return
        if not valid:
            raise _failure("subscription login authentication required")

    def execute(self, handoff: dict[str, object]) -> WorkerResult:
        prompt = handoff.get("prompt")
        if not isinstance(prompt, str):
            prompt = "Return a proposal for this engineering handoff:\n" + json.dumps(handoff)
        try:
            with tempfile.TemporaryDirectory(prefix="agent-cli-") as cwd:
                self._check_login(cwd)
                code, out, err = self._run(list(self.command), prompt, cwd)
        except (FileNotFoundError, PermissionError) as exc:
            raise _failure("login executable missing") from exc
        except subprocess.TimeoutExpired as exc:
            raise _failure("connection timeout") from exc
        if code != 0:
            raise _failure(err or out)
        if self.name == "openai-codex-cli":
            return _codex_result(out)
        if self.name == "anthropic-claude-cli":
            return _claude_result(out)
        return WorkerResult(output=out.strip())


def _codex_result(output: str) -> WorkerResult:
    session = None
    final = None
    completed = False
    for line in output.splitlines():
        event = json.loads(line)
        kind = event.get("type")
        if kind in {"error", "turn.failed"}:
            raise _failure(json.dumps(event))
        if kind == "thread.started":
            session = event.get("thread_id")
        if kind == "item.completed" and event.get("item", {}).get("type") == "agent_message":
            final = event["item"].get("text")
        if kind == "turn.completed":
            completed = True
    if not completed or not isinstance(final, str) or not final.strip():
        raise _failure("invalid request: incomplete Codex event stream")
    return WorkerResult(output=final, session_id=session)


def _claude_result(output: str) -> WorkerResult:
    payload = json.loads(output)
    if payload.get("is_error") or payload.get("subtype") != "success":
        raise _failure(json.dumps(payload))
    final = payload.get("result")
    if not isinstance(final, str) or not final.strip():
        raise _failure("invalid request: empty Claude result")
    return WorkerResult(output=final, session_id=payload.get("session_id"))


def codex_subscription_worker() -> CliWorker:
    flags = tuple(part for feature in ("shell_tool", "unified_exec", "hooks", "plugins", "apps",
                                      "browser_use", "computer_use", "multi_agent",
                                      "image_generation", "skill_search")
                  for part in ("--disable", feature))
    return CliWorker("openai-codex-cli", (
        "codex", "exec", "--json", "--sandbox", "read-only", "--ignore-user-config",
        "--ignore-rules", "--skip-git-repo-check", "-c", 'web_search="disabled"', *flags, "-"))


def claude_subscription_worker() -> CliWorker:
    return CliWorker("anthropic-claude-cli", (
        "claude", "-p", "--output-format", "json", "--safe-mode", "--restricted",
        "--tools", "", "--disallowedTools", "mcp__*", "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
        "--settings", '{"disableAllHooks":true,"forceLoginMethod":"claudeai"}'))
