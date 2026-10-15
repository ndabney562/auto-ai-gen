from __future__ import annotations

import json
import os

from openai import OpenAI

from providers.base import WorkerResult


class OpenAIWorker:
    name = "openai"

    def __init__(self, *, model: str | None = None, client: OpenAI | None = None) -> None:
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-6-luna")
        self.client = client or OpenAI()

    def execute(self, handoff: dict[str, object]) -> WorkerResult:
        response = self.client.responses.create(
            model=self.model,
            input=(
                "Continue this autonomous engineering task from the durable handoff below. "
                "Do not redo completed work. Return concise execution output and next steps.\n\n"
                + json.dumps(handoff, sort_keys=True)
            ),
        )
        return WorkerResult(output=response.output_text)
