# Subscription-limit recovery acceptance — October 16, 2026

A real Claude Pro response during the installed-service trial reported:
`You've hit your weekly limit · resets 5am (America/New_York)`.
The previous adapter treated that as a generic rate limit and retried after
60 seconds, eventually exhausting independent-review attempts.

The new adapter classifies weekly/daily/session limits as quota waits and parses
reset hints without saving the raw diagnostic. Provider cooldowns and task retry
deadlines persist across service restarts. A completed limit response refunds that
request's implementation/review attempt; outages and interrupted requests retain
their bounded budgets. Implementation may fail over to another available provider;
review still waits for the other provider. Nothing approves or merges while the
independent review is missing.

Explicit reset dates/timestamps are preferred. A time-only hint lacks a weekly
reset date, so it schedules the next local occurrence as a probe. A still-limited
response extends the wait again. Unknown quota resets probe hourly; unknown rate
limits probe every five minutes. No API fallback or extra-credit purchase occurs.

Verification:

- The pinned offline ARM64 validator passed 164 tests and lint on the initial
  feature commit, with no skips.
- Parser tests cover explicit offsets/epochs, timezone-qualified dates/times,
  ambiguous fall-back DST, nonexistent spring-forward times, invalid hints and
  diagnostic privacy.
- Durable phase tests exceed the normal retry budget with repeated limit
  responses, verify no early calls, then resume the same candidate/reviewer.
- Bounded non-limit errors and cancellation remain independently tested.
- `tests.acceptance.local_limit_recovery` uses the production background service,
  runner and CLI subprocess protocol with an explicitly local Claude fixture.
  One fixture response establishes a real 60-second cooldown. The service is
  killed and restarted during the wait; no early request occurs. At expiry a
  second fixture response resumes review of the same validated commit. Only the
  successful review consumes a review attempt.

The trial explicitly disables GitHub publication and makes no real model calls;
its fixture verdict is not a real model approval. Evidence is written in its
acceptance-only state directory. The real Claude account's weekly reset has not
been observed yet; local fixtures establish scheduling/restart behavior without
waiting for it or consuming subscription allowance.

Reproduce from a trusted checkout with the pinned image already built:

```sh
PATH=/path/to/docker/bin:/usr/bin:/bin .venv/bin/python -m tests.acceptance.local_limit_recovery \
  --state /path/to/fresh-acceptance-state --repo . --image sha256:LOCAL_IMAGE_ID
```
