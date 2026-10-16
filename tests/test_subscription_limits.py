import json
from datetime import UTC, datetime

import pytest

from providers.cli_provider import _failure
from providers.errors import ProviderFailure
from providers.subscription_limits import reset_delay

NOW = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)


@pytest.mark.parametrize('detail,stamp', [
    ('weekly limit · resets 5am (America/New_York)', '2026-10-17T09:00:30+00:00'),
    ('weekly limit · resets Fri at 5am (America/New_York)', '2026-10-23T09:00:30+00:00'),
    ('weekly limit · resets October 23 at 5am (America/New_York)', '2026-10-23T09:00:30+00:00'),
    ('weekly limit · resets Oct 23, 2026 at 5:30pm (America/New_York)',
     '2026-10-23T21:30:30+00:00'),
    ('quota exhausted resets 2026-10-17T09:00:00Z', '2026-10-17T09:00:30+00:00'),
    (json.dumps({'reset_at': '2026-10-17T09:00:00-04:00'}), '2026-10-17T13:00:30+00:00'),
    (json.dumps({'error': {'resetsAt': 1792227600}}), '2026-10-17T09:00:30+00:00'),
    (json.dumps({'error': {'resets_at': 1792227600000}}), '2026-10-17T09:00:30+00:00'),
    (json.dumps({'error': {'retry_after_seconds': 900}}), '2026-10-16T14:15:30+00:00'),
    (json.dumps({'result': "You've hit your weekly limit · resets 5am (America/New_York)"}),
     '2026-10-17T09:00:30+00:00'),
])
def test_reset_formats(detail, stamp):
    expected = (datetime.fromisoformat(stamp) - NOW).total_seconds()
    assert reset_delay(detail, now=NOW) == expected


@pytest.mark.parametrize('detail', [
    'weekly limit', 'resets 5am', 'resets 13am (America/New_York)',
    'resets 5:99am (America/New_York)', 'resets 5am (Unknown/Zone)',
    'resets February 31 at 5am (America/New_York)',
    'resets 2027-10-17T09:00:00Z', 'resets 2020-10-17T09:00:00Z',
    '{"reset_at":"2026-10-17T09:00:00"}', '{"reset_at":true}',
    '{"reset_at":NaN}', '{"retry_after_seconds":Infinity}',
    '{"retry_after_seconds":-1}', '{"reset_at":9999999999999999999999}',
])
def test_invalid_and_ambiguous_hints_use_fallback(detail):
    assert reset_delay(detail, now=NOW) is None


def test_dst_and_year_boundary():
    # Later fold is chosen for the ambiguous fall-back 1:30am.
    assert reset_delay('resets 1:30am (America/New_York)',
                       now=datetime(2026, 11, 1, 5, 45, tzinfo=UTC)) == 2730
    # Nonexistent spring-forward wall times cannot establish a deadline.
    assert reset_delay('resets 2:30am (America/New_York)',
                       now=datetime(2026, 3, 8, 5, tzinfo=UTC)) is None
    assert reset_delay('resets Jan 1 at 5am (America/New_York)',
                       now=datetime(2026, 12, 31, 12, tzinfo=UTC)) == 79230


def test_limit_classification_and_privacy():
    error = _failure("You've hit your weekly limit; secret-token-do-not-store")
    assert error.classified.kind == ProviderFailure.QUOTA_EXHAUSTED
    assert error.classified.retry_after_seconds == 3600
    assert 'secret-token' not in str(error)
    assert error.classified.detail is None
    assert _failure('rate_limit').classified.retry_after_seconds == 300
    assert _failure('authentication required; quota').classified.retryable is False


def test_cli_metadata_does_not_create_a_false_permanent_quota_wait():
    payload = {'subtype': 'error', 'session_id': 'session-429-123',
               'usage': {'rate_limit': 'allowed'}, 'result': 'service unavailable'}
    assert _failure(json.dumps(payload)).classified.kind == ProviderFailure.OUTAGE
    payload['result'] = "You've hit your weekly limit"
    payload['reset_at'] = '2026-10-17T09:00:00Z'
    assert reset_delay(json.dumps(payload) + '\nCLI warning', now=NOW) == 68430
