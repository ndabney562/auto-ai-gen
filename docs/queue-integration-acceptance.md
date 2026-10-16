# Queue and integration acceptance — October 16, 2026

PR #25 adds durable queue membership, dependencies, round-robin phase selection,
per-task error budgets and targeted cancellation. Its exact head passed 178 tests
and lint in the pinned offline ARM64 validator and GitHub CI before merge.

The integration change fetches current main, saves a replayable INTEGRATING
intent, merges Git objects or requests bounded conflict repair, then discards
old validation/review. Replacement candidates require new tests, independent
review and exact-head CI; unchanged superseded PRs are closed after replacement
publication. Unit tests cover clean integration, conflicts, interrupted checkout,
merge-gate races, repair-source preservation, whitespace, target bounds and rewrites.

The opt-in `tests.acceptance.multi_task_queue` trial completed three tasks without
operator action after start. It used the production queue, runner, subprocess
protocol and real offline Docker validation, with local CLI fixtures and a local
Git publisher. It made **zero real model requests and zero GitHub requests**.
Fixture approvals/publications are confined to that acceptance-only state.

Observed sequence:

1. Two independent tasks implemented and validated while their dependent stayed idle.
2. A simulated subscription response established a real 60-second provider wait.
   The service was killed/restarted; both independent tasks resumed after cooldown.
3. The first task merged locally. The second task's reviewed candidate became
   stale, integrated cleanly, revalidated, received a new fixture review, and merged.
4. Only then did the dependent task start, using its dependencies' merged source.
5. A concurrent trusted edit to its target caused a real Git conflict. The runner
   sent conflict context for repair, preserved that upstream edit, validated and
   reviewed the replacement, retired the old local PR, and merged it.

All three tasks reached DONE through the normal merge-confirmation gate, with
three distinct merged candidates and two retired predecessors. Each final
candidate passed all 189 tests and lint in the pinned image. A further regression
case verifies preserving an already-failed patch when main refreshes before repair.
The trial fixture loader was corrected to work with the production scrubbed child
environment; it does not weaken credential/environment isolation.

Reproduce from a trusted checkout with Docker and the pinned image available:

```sh
PATH=/path/to/docker/bin:/usr/bin:/bin python -m tests.acceptance.multi_task_queue \
  run /path/to/fresh-state --repo . --image sha256:IMMUTABLE_IMAGE_ID
```

The successful development evidence is `/private/tmp/auto-ai-gen-unattended-trial-v2/evidence.json`.
This is a local unattended integration trial, not proof that Claude's real weekly
limit reset or that real remote CI/model review completed unattended. The installed
service's separate live queue uses real subscription workers and existing GitHub
credentials, retains those gates, and waits automatically for provider capacity.
