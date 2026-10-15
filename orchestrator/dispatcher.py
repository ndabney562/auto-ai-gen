from __future__ import annotations

from collections.abc import Iterable

from providers.health import ProviderHealth


class NoProviderAvailable(RuntimeError):
    pass


def select_provider(
    providers: Iterable[ProviderHealth],
    *,
    preferred: str | None = None,
) -> ProviderHealth:
    candidates = list(providers)
    if preferred:
        candidates.sort(key=lambda provider: provider.name != preferred)

    for provider in candidates:
        if provider.available():
            return provider

    raise NoProviderAvailable("No inference provider is currently available")
