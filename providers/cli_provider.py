from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Sequence

from providers.base import WorkerResult


@dataclass(frozen=True, slots=True)
class CliWorker:
    """Provider worker backed by an authenticated local coding CLI.

    This deliberately passes no API key. Authentication and subscription usage
    remain the responsibility of the installed Codex/Claude CLI.
    """

    name: str
    command: Sequence[str]
    timeout_seconds: int = 1800

    def execute(self, handoff: dict[str, object]) -> WorkerResult:
        prompt = (
            "Execute this provider-independent engineering handoff. Preserve "
            "checkpoint semantics and return a concise result.\n\n"
            + json.dumps(handoff, sort_keys=True)
        )
        completed = subprocess.run(
            [*self.command, prompt],
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                f"{self.name} CLI failed with exit {completed.returncode}: {detail}"
            )
        return WorkerResult(output=completed.stdout.strip())


def codex_subscription_worker() -> CliWorker:
    """Use the locally authenticated Codex CLI (ChatGPT subscription)."""
    return CliWorker(
        name="openai-codex-cli",
        command=("codex", "exec", "--full-auto"),
    )


def claude_subscription_worker() -> CliWorker:
    """Use the locally authenticated Claude Code CLI (Claude subscription)."""
    return CliWorker(
        name="anthropic-claude-cli",
        command=("claude", "-p", "--permission-mode", "acceptEdits"),
    )
