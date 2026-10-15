from orchestrator.runtime import execute_with_failover
from providers.base import WorkerResult
from providers.health import ProviderHealth


class FailingWorker:
    name = "openai"

    def execute(self, handoff: dict[str, object]) -> WorkerResult:
        raise RuntimeError("quota exhausted")


class SuccessfulWorker:
    name = "anthropic"

    def execute(self, handoff: dict[str, object]) -> WorkerResult:
        return WorkerResult(output=f"continued {handoff['task_id']}")


def test_runtime_fails_over_when_preferred_worker_raises() -> None:
    provider, result = execute_with_failover(
        {"task_id": "TP-10"},
        [ProviderHealth("openai"), ProviderHealth("anthropic")],
        {"openai": FailingWorker, "anthropic": SuccessfulWorker},
        preferred="openai",
    )

    assert provider == "anthropic"
    assert result.output == "continued TP-10"
