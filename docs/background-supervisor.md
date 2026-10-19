# Local background supervisor

The supervisor drives explicitly assigned tasks through implementation,
isolated validation, independent review, PR publication, CI polling, and bounded
repair. It supports a single task or a [durable dependency-aware queue](durable-queue.md).
It does not invent new objectives or start unenrolled saved tasks. Merging is
off by default. `--merge-verified` enables only the existing exact-commit,
protected-main merge gate. The coordinator enforces independent model review;
GitHub enforces up-to-date CI for pull requests, including administrator merges.
An externally merged exact-candidate PR is reconciled to DONE even when automatic
merging is disabled.

Use a trusted checkout with the locked validation dependencies, existing CLI
subscription logins, GitHub login, and Docker running. Keep state outside source
folders. Build the trusted validator as described in
[reproducible validation](reproducible-validation.md), then use its immutable image
ID. Create a task with `orchestrator.subscription_runner create` first.

```sh
.venv/bin/python -m orchestrator.supervisor --state /path/to/state configure TASK_ID \
  --repository ndabney562/auto-ai-gen --image sha256:YOUR_LOCAL_IMAGE_ID
.venv/bin/python -m orchestrator.supervisor --state /path/to/state start
.venv/bin/python -m orchestrator.supervisor --state /path/to/state status
.venv/bin/python -m orchestrator.supervisor --state /path/to/state stop
.venv/bin/python -m orchestrator.supervisor --state /path/to/state cancel
```

`start` detaches from the terminal. A lifetime file lock prevents duplicate services.
`stop` interrupts the active phase and preserves checkpoints; use `start` to resume.
`cancel` writes a pending marker and attempts the same nonblocking lifetime lock.
If the service is stopped, cancellation records CANCELLED immediately while holding
that lock, without starting a service or worker. This also applies to READY tasks
and tasks waiting for CI. If a service holds the lock, cancellation remains pending;
the service interrupts and reaps its active phase before recording CANCELLED.
Successful cancellation removes the pending marker and retains all completed-work
evidence and checkpoints. DONE, CANCELLED, and FAILED_PERMANENT task records are
left unchanged. Only a busy service lock is treated as deferred cancellation;
unexpected lock, storage, or concurrent-update errors are reported. A failed
cancellation leaves the pending marker available for a later attempt. Cancellation
does not clear a stop marker. Cancellation cannot undo an already published PR or
completed merge. Use this service as the sole driver of its dedicated state directory.

Each phase runs in a guarded subprocess, with a 20-minute wall-clock bound.
Terminating the service also stops its phase; nested provider/container guards
clean up local workers. This cannot guarantee cancellation of server-side model
requests. After a hard interruption, execution resumes using the runner's durable
checkpoints and lease-expiry rules. Wake from sleep rechecks absolute retry times.

Provider waits and CI checks poll every 30 seconds by default (configurable from
5 to 3600 seconds). Terminal, blocked, and WAIT_HUMAN tasks are left alone. Failed
phase commands back off and pause after three consecutive failures, persisted
across service restarts. Status exposes their exit code without copying prompts,
credentials, or raw child output. Diagnose the phase with the foreground runner.
After fixing the cause, stop and explicitly reconfigure to reset this service
failure budget. Existing task-level retry/review limits still apply.

For macOS login/reboot recovery, generate and review a LaunchAgent file:

```sh
.venv/bin/python -m orchestrator.supervisor --state /path/to/state launch-agent \
  --output /tmp/local.auto-ai-gen.plist
```

It records absolute interpreter/source/state paths and the current PATH, with no
credential environment variables. Move it to `~/Library/LaunchAgents/` and load it
with `launchctl bootstrap gui/$(id -u) PATH_TO_PLIST`. Unload with
`launchctl bootout gui/$(id -u) PATH_TO_PLIST` before changing configuration. It
starts at user login and restarts abnormal exits with throttling. A deliberate
`stop` persists across login/reboot until `start` clears it. Keep the trusted
checkout and interpreter at those paths. This is a user login service, not a
pre-login system daemon; Docker and account login stores must be available.

Do not run a detached `start` service and a LaunchAgent simultaneously. A second
service is refused by the lifetime lock. The generated artifact is not installed
automatically. Tests exercise process death, immediate stopped-service cancellation,
deferred cancellation after phase cleanup, terminal-record preservation, unexpected
errors, and checkpoint preservation; a full physical Mac reboot and unattended
subscription task remain deployment acceptance checks.

## Subscription limits and automatic resumption

A Codex or Claude rate/subscription limit now enters WAIT_PROVIDER instead of
exhausting implementation, review, or supervisor failure budgets. The durable
record retains the exact candidate, validation, review phase, and UTC `retry_at`.
`status` shows that deadline and the waiting reviewer. Restarting the service does
not clear the cooldown. At the deadline the supervisor resumes the saved phase;
it never uses the implementation provider to approve its own candidate.

The CLI adapter recognizes explicit reset timestamps (offset ISO, Unix seconds or
milliseconds), retry-after seconds, and timezone-qualified notices such as
`resets Fri at 5am (America/New_York)`. A 30-second buffer avoids racing the reset;
probes are at least 60 seconds apart. Time-only notices mean the next occurrence
in the named timezone, not a guaranteed weekly reset date. If still limited, the
next response schedules another wait. DST and timezone offsets are handled
without relying on the Mac's timezone.

When no trustworthy reset hint is available, quota waits probe hourly and rate
limits every five minutes. Raw CLI diagnostics, tokens, and prompt text are not
saved with cooldowns. Authentication/configuration errors still stop for repair;
outages, invalid reviews, and interrupted requests retain their bounded budgets.
No API-key fallback, purchased credits, or review bypass is used.

The opt-in `tests.acceptance.local_limit_recovery` trial uses local CLI fixtures,
real subprocesses, real offline Docker validation, and a real cooldown. It kills
and restarts a waiting service, verifies no early requests, and observes automatic
review resumption for the same candidate. Publication is explicitly disabled in
that fixture; its approval is not a real model review or merge credential.

## Interruption diagnostics

`status` includes `last_phase` and, after a failure, `last_phase_failure`. These
fixed-size records live in `STATE/supervisor/last-phase.json` and
`last-phase-failure.json`; a successful retry does not erase the previous failure.
Each phase saves its start before launch. If the process dies before recording an
exit, the next phase retains that start as `interrupted_before_recording_exit`.
A `running` record alone is not proof that its process is still alive.

Records include the generated task ID, exit code, outcome (success, nonzero exit,
timeout, stopped, launch error or coordinator error), start/end timestamps, signed wall
and monotonic durations, stderr byte count, and whether stderr collection finished.
Only recognized exception types and up to eight trusted module file/line locations
are retained. Raw stderr, exception messages, commands, prompts, credentials and
candidate source are not saved. Long stderr lines are discarded and stderr is
drained without reader threads, with bounded memory and bounded work per poll.
The read pipe always closes, including when a detached descendant retains its
write end; incomplete collection is reported. Malformed diagnostic records and
diagnostic write failures do not block task processing. Missing metadata does not prove that no
error occurred. A clock gap is evidence to investigate, not proof of Mac sleep.

Recovery tests suspend a real worker process past its renewable lease and kill a
separate worker process. Tests resume the stale worker after a replacement owner
has completed its proposal, and advance wall time with monotonic time frozen. Stale work is rejected, checkpoints and attempt counts
survive, and resumed work must still enter validation rather than DONE. These
process tests do not certify physical host sleep, lid-close behavior or a Mac
reboot. An automatic physical sleep/wake test needs permission to schedule wake
with macOS power management; do not sleep a remote execution host without a
verified automatic wake plan. No retry budgets or independent-review gates are
relaxed by diagnostics.
