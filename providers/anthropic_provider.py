from __future__ import annotations

import json
import os

from anthropic import Anthropic

from providers.base import WorkerResult


class AnthropicWorker:
    name = "anthropic"

    def __init__(self, *, model: str | None = None, client: Anthropic | None = None) -> None:
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        self.client = client or Anthropic()

    def execute(self, handoff: dict[str, object]) -> WorkerResult:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Continue this autonomous engineering task from the durable handoff below. "
                        "Do not redo completed work. Return concise execution output and next steps.\n\n"
                        + json.dumps(handoff, sort_keys=True)
                    ),
                }
            ],
        )
        text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")
        return WorkerResult(output=text)
