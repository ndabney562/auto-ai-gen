# Background supervisor acceptance — October 16, 2026

The merged-main baseline (`95126041ce068984cd1b61018a716dfe81a0502b`)
passed 94 tests and lint in the restricted ARM64 validator. GitHub main CI passed.
PR #21's pinned environment also passed all 94 tests and lint locally, and its
GitHub AMD64 CI passed.

The supervisor's final live fixture used source commit
`4ffee40c10241de032841d8a9eefb1b4cfb40d5b` and immutable local image
`sha256:a6c4472c905a556a376be193ae622a002f1b5d390fe291930d84067892824b18`.
Its candidate `979b385b19a5aeb016f9ac7a48ca636f42f06a7f` passed **117 tests
and lint**, inside the offline, non-root, read-only container.

The production background service, rather than a direct foreground runner:

- Started validation of that immutable candidate in a real Docker container.
- Accepted stop while validation was active; the service and container stopped.
- Restarted and validated the same candidate, then waited for an unavailable reviewer.
- Stopped/restarted again while preserving the candidate and validation checkpoint.
- Accepted cancellation, retained the checkpoint, and shut down cleanly.

This was an explicit validation fixture, not a model implementation/review trial.
No model calls, PRs, merges, or API credit purchases were involved in this fixture.
The earlier live subscription and CI-repair trials remain separately documented.

Reproduce using a fresh state directory outside source:

```sh
PATH=/path/to/docker/bin:/usr/bin:/bin .venv/bin/python -m tests.acceptance.live_supervisor \
  --state /path/to/fresh-supervisor-state --repo . --image sha256:LOCAL_IMAGE_ID
```

The Docker-only PATH deliberately excludes Codex and Claude, so the fixture must
wait at independent review. The script records evidence in the state directory
and stops the service in its cleanup path. One earlier run correctly failed local
lint due to missing explicit subprocess error handling in the acceptance script;
that script was fixed before the successful run recorded above.

Automated subprocess tests also cover SIGTERM/SIGKILL of an active phase, timeout
cleanup, duplicate-service exclusion, cancellation during phases, persisted retry
budgets, provider retry times, external merge reconciliation, and start/stop/restart.
Host tests pass with one Node-dependent skip; the container run has no skips.
A generated macOS LaunchAgent is an opt-in deployment artifact. Physical logout,
reboot, and subscription work under launchd still require deployment acceptance.
No persistent service has been installed by these changes.
