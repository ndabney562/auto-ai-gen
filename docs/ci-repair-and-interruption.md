# CI repair and interruption acceptance — October 15, 2026

PR #17's CI-repair and interruption gates passed in supervised tests. These trials
did not enable unattended operation or change repository protection settings.

## Real GitHub CI failure and model repair

A dedicated task created a negative test fixture on a draft-only branch. Its first
candidate passed the offline local validator and intentionally failed only when
GITHUB_ACTIONS was true, with assertion INTENTIONAL_CI_REPAIR_PILOT_FAILURE.
Independent Claude Pro review explicitly approved that temporary negative fixture
for the acceptance experiment, not for production merging.

- Negative candidate: `58f452a942c3f071822722e5fd52782957402427`.
- [Draft fixture PR #19](https://github.com/ndabney562/auto-ai-gen/pull/19).
- [Expected CI failure](https://github.com/ndabney562/auto-ai-gen/actions/runs/38825998021):
  1 failed, 64 passed.

The production publisher fetched actual GitHub failure logs and saved REPAIR.
The next process resumed the task; real subscription-authenticated Codex replaced
the injected conditional failure with a portable test asserting 2 + 2 == 4. No
other tests were modified or weakened. The exact repair passed Docker validation
and independent real Claude review, which received the previous failure logs.

- Repaired candidate: `5fa3f4cb0a2dbbb99a0853b19cadc6de797b0193`.
- [Draft repair fixture PR #20](https://github.com/ndabney562/auto-ai-gen/pull/20).
- [Successful repair CI](https://github.com/ndabney562/auto-ai-gen/actions/runs/38826452360):
  all 65 tests passed, plus lint.
- Implementation completed on attempt 2 of the bounded 3-attempt budget.

Both fixture PRs were closed without merging. Their branches and CI evidence were
retained. The local acceptance task was retired as BLOCKED with an explicit
acceptance-completed marker and no further action; it was never falsely marked
DONE, which still requires confirmed merge for ordinary engineering tasks.

## Abrupt process death and durable recovery

Real subprocess tests sent SIGKILL and SIGTERM to coordinators running a CLI
fixture that spawned a descendant. The production worker launcher stopped the
private process group when its coordinator disappeared; the descendant heartbeat
stopped. These tests use a local CLI fixture, not a deliberately killed remote
model generation request. Provider-side cancellation is outside this local proof.

Another process test killed an EXECUTING coordinator and waited for real lease
expiry. A new process recovered and dispatched the task, retained its existing
completed-work checkpoint, incremented attempt from 1 to 2, and stopped at
VALIDATING. The test uses a 0.3-second lease to exercise actual expiry quickly;
normal execution still defaults to 120 seconds with heartbeat renewal.

Real Docker probes killed coordinators while candidate containers were running:

| Signal | Preserved candidate | Restart result |
| --- | --- | --- |
| SIGKILL | `e8d27857df506270f0ade6d80d662c031c77c9b7` | Same SHA validated; REVIEW |
| SIGTERM | `e69a1ee4698dc142bce490d14aad3101b38178fc` | Same SHA validated; REVIEW |

Both interrupted containers stopped. Fresh processes recovered their recorded
resource identities, removed stale resources, and validated the saved candidates
without regenerating implementation. Each resumed archive passed all 65 tests
and lint. Validation remained offline, non-root, and read-only throughout.

Finally, the acceptance coordinator was killed immediately after GitHub created
PR #20 but before the task saved that PR checkpoint. A fresh process reconciled
the existing branch and PR by candidate SHA, saved PR #20, and created no duplicate.
This proves recovery from an external side effect whose local checkpoint was lost.

## Defects repaired

- Add a small parent-death guardian around CLI and Docker processes. It requires a
  private process group before starting a child, preventing cleanup from signaling
  a caller's unrelated processes. The Docker probe caught an initial missing
  process-group isolation setting; the regression now checks this requirement.
- Register each unique validation container and snapshot before launch. Resume
  can clean an interrupted validation before starting another one. An inner
  container timeout remains a fallback if the Docker daemon cannot be contacted.
- Save review-attempt counts before making an external request. Repeated crashes
  can no longer reset the budget; three interrupted reviews stop at WAIT_HUMAN.
- Preserve the failed candidate as repair source context, rather than showing only
  the original base files. Clear obsolete validation/review evidence during repair.
- Give the independent reviewer prior CI failure logs so it can evaluate the repair
  against the same failure evidence as the implementation worker.

The implementation at `553923ba3a5e5f066786141a76bb085a9b680816` passed all 94 tests
and lint inside the offline ARM64 validator, with no skips. The host suite also
passed (93 tests, one Node-dependent check skipped by its default PATH).

## Reproducing the opt-in live probes

From a trusted coordinator checkout with authenticated subscription CLIs, gh, and
a running local Docker engine, build the validator as described in the setup guide.
Use state directories outside the source checkout:

```sh
.venv/bin/python tests/acceptance/live_interruption.py \
  --state /tmp/interruption-trial --repo . --image auto-ai-gen-validator:local
.venv/bin/python tests/acceptance/live_ci_repair.py \
  --state /tmp/ci-repair-trial --repo . --image auto-ai-gen-validator:local
```

The CI probe advances one phase per invocation and deliberately creates draft
fixture PRs. Repeat it after each phase and CI completion. For the repaired
candidate's publication phase, add --interrupt-after-publish once, then restart
without that flag. Close the fixture PRs without merging after recording evidence.
The harness never calls the merge API.

Protected automatic merging, immutable dependency/image locking, and a background
service remain separate work. These results establish the requested CI-repair and
interruption behavior, not unattended production readiness or power-loss durability.
