from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProviderFailure(StrEnum):
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    AUTH_FAILURE = "AUTH_FAILURE"
    OUTAGE = "OUTAGE"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ClassifiedProviderError:
    kind: ProviderFailure
    retryable: bool
    retry_after_seconds: float | None = None
    detail: str | None = None


def classify_http_error(
    status_code: int,
    *,
    retry_after_seconds: float | None = None,
    quota_exhausted: bool = False,
    detail: str | None = None,
) -> ClassifiedProviderError:
    if status_code == 429:
        kind = ProviderFailure.QUOTA_EXHAUSTED if quota_exhausted else ProviderFailure.RATE_LIMITED
        return ClassifiedProviderError(
            kind=kind,
            retryable=True,
            retry_after_seconds=retry_after_seconds,
            detail=detail,
        )
    if status_code in {401, 403}:
        return ClassifiedProviderError(ProviderFailure.AUTH_FAILURE, False, detail=detail)
    if status_code in {408, 409, 425} or status_code >= 500:
        return ClassifiedProviderError(
            ProviderFailure.OUTAGE,
            True,
            retry_after_seconds=retry_after_seconds,
            detail=detail,
        )
    if 400 <= status_code < 500:
        return ClassifiedProviderError(ProviderFailure.INVALID_REQUEST, False, detail=detail)
    return ClassifiedProviderError(ProviderFailure.UNKNOWN, True, detail=detail)
