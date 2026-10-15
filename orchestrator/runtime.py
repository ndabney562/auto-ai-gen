from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path

from orchestrator.dispatcher import NoProviderAvailable, select_provider
from providers.anthropic_provider import AnthropicWorker
from providers.base import ProviderWorker, WorkerResult
from providers.errors import ProviderExecutionError, normalize_sdk_error
from providers.health import ProviderHealth
from providers.openai_provider import OpenAIWorker

WorkerFactory = Callable[[], ProviderWorker]


def available_health() -> list[ProviderHealth]:
    providers: list[ProviderHealth] = []
    if os.getenv("OPENAI_API_KEY"):
        providers.append(ProviderHealth("openai"))
    if os.getenv("ANTHROPIC_API_KEY"):
        providers.append(ProviderHealth("anthropic"))
    return providers


def execute_with_failover(
    handoff: dict[str, object],
    providers: list[ProviderHealth],
    factories: dict[str, WorkerFactory],
    *,
    preferred: str | None = None,
) -> tuple[str, WorkerResult]:
    remaining = list(providers)
    last_error: Exception | None = None

    while remaining:
        try:
            selected = select_provider(remaining, preferred=preferred)
        except NoProviderAvailable:
            break
        try:
            return selected.name, factories[selected.name]().execute(handoff)
        except Exception as exc:
            classified = normalize_sdk_error(exc)
            if not classified.retryable:
                raise
            last_error = exc
            remaining = [provider for provider in remaining if provider.name != selected.name]
            preferred = None

    if last_error is not None:
        raise ProviderExecutionError(
            "All configured providers failed", normalize_sdk_error(last_error)
        ) from last_error
    raise NoProviderAvailable("No provider credentials are configured")


def default_factories() -> dict[str, WorkerFactory]:
    return {"openai": OpenAIWorker, "anthropic": AnthropicWorker}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one resumable autonomous-agent handoff")
    parser.add_argument("handoff", type=Path, help="JSON handoff manifest")
    parser.add_argument("--preferred", choices=["openai", "anthropic"])
    args = parser.parse_args()

    handoff = json.loads(args.handoff.read_text(encoding="utf-8"))
    provider, result = execute_with_failover(
        handoff,
        available_health(),
        default_factories(),
        preferred=args.preferred,
    )
    print(json.dumps({"provider": provider, "output": result.output}, indent=2))


if __name__ == "__main__":
    main()
