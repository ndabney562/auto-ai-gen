from providers.errors import ProviderFailure, normalize_sdk_error


class Response:
    def __init__(self, retry_after: str | None = None) -> None:
        self.headers = {} if retry_after is None else {"retry-after": retry_after}


class SDKError(RuntimeError):
    def __init__(self, status_code: int, detail: str, retry_after: str | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.response = Response(retry_after)


def test_rate_limit_preserves_retry_after() -> None:
    error = normalize_sdk_error(SDKError(429, "rate limited", "12.5"))

    assert error.kind is ProviderFailure.RATE_LIMITED
    assert error.retryable is True
    assert error.retry_after_seconds == 12.5


def test_quota_exhaustion_is_distinct_from_rate_limit() -> None:
    error = normalize_sdk_error(SDKError(429, "insufficient_quota: credit balance exhausted"))

    assert error.kind is ProviderFailure.QUOTA_EXHAUSTED
    assert error.retryable is True


def test_auth_failure_is_not_retryable() -> None:
    error = normalize_sdk_error(SDKError(401, "invalid API key"))

    assert error.kind is ProviderFailure.AUTH_FAILURE
    assert error.retryable is False


def test_connection_error_is_retryable_outage() -> None:
    class APIConnectionError(RuntimeError):
        pass

    error = normalize_sdk_error(APIConnectionError("network unavailable"))

    assert error.kind is ProviderFailure.OUTAGE
    assert error.retryable is True
