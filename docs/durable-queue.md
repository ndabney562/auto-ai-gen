# Durable assigned-task queue

Queue mode executes explicitly enrolled tasks one bounded phase at a time. It
persists membership, dependency edges, a round-robin cursor and the active task
before external work starts. Task checkpoints and per-task phase-error budgets
survive restart. A quota wait, failed task, or unresolved dependency does not stop
independent work. Only a confirmed DONE dependency releases its dependents.

```sh
python -m orchestrator.supervisor --state /path/to/state configure-queue \
  --repository owner/repo --image sha256:IMMUTABLE_IMAGE_ID --merge-verified
# Create bounded tasks with subscription_runner create, then enroll their IDs.
python -m orchestrator.supervisor --state /path/to/state enqueue FIRST_TASK
python -m orchestrator.supervisor --state /path/to/state enqueue SECOND_TASK --after FIRST_TASK
python -m orchestrator.supervisor --state /path/to/state start
python -m orchestrator.supervisor --state /path/to/state status
python -m orchestrator.supervisor --state /path/to/state cancel --task SECOND_TASK
```

Enrollment is idempotent for identical dependency lists, and is available while
the service runs. Dependencies must already be enrolled, preventing missing edges
and cycles. Unenrolled tasks are never discovered or executed automatically.
Queue capacity is 1,000 retained records, including completed history. Repeated
phase failures pause that task after three failures; unrelated tasks continue.
Non-retryable authentication errors and other task-level safety stops remain.

Cancellation names an enrolled task. Without an ID it resolves the currently
active task; an idle queue requires an explicit ID. A running phase is reaped
before its task is cancelled. Cancelling a waiting task does not cancel unrelated
active work. Stop affects the service and preserves every queued checkpoint.

The singleton service lock prevents two services from driving the same state.
Use this supervisor alone for its dedicated state directory. Queue mode is
backward compatible with existing single-task configurations; changing mode or
repository/image/merge policy requires stopping/unloading the service first.
This is an execution queue, not an autonomous goal planner.
