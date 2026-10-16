# Local background supervisor

The supervisor drives one explicitly selected task through implementation,
isolated validation, independent review, PR publication, CI polling, and bounded
repair. It does not discover new objectives or start every saved task. Merging is
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
