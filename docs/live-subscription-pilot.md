# Supervised subscription pilot — October 15, 2026

The local runner completed live work through both subscription-authenticated
coding tools. No model API keys or purchased API credits were used. Model requests
consume the existing account allowances.

## Verified results

- Codex CLI 0.162.1 generated a bounded documentation proposal using saved ChatGPT
  authentication. Claude Code 2.1.292, authenticated with Claude.ai Pro, independently
  approved candidate `8fc4fa112104162fc99f2a58baf3cf1f6970b84a`.
- The candidate passed isolated Docker validation. Its original image ran 63 tests
  and skipped the Node syntax check; exact-head GitHub CI passed with Node available.
  [Pilot PR #18](https://github.com/ndabney562/auto-ai-gen/pull/18) changed one document
  and was merged only after explicit user approval of a supervised merge into
  unprotected main. Merge commit: `0ee5d93997ecf6611bc8163b211db41c660315ef`.
- Re-running publication reconciled the same PR without creating a duplicate.
  Reconciliation after confirmed merge recorded the durable task as DONE.
- A separate trial injected a retryable Codex quota error before any model call.
  The real Claude subscription worker took over implementation. This did not
  exhaust or bypass a real account limit.
- The failover candidate `8a9c769f6d36024279e15e94c69d8948df12f1b3`
  passed all 64 current-main tests and lint in the updated offline container.
  When the independent reviewer was deliberately unavailable, the runner entered
  WAIT_PROVIDER. A separate process restored REVIEW and the real Codex worker
  approved the unchanged candidate. This second candidate remains local.
- The complete prototype at `0a1786848d414fc3f00f5a8ef08067a6810789d4`
  passed all 88 tests and lint inside the restricted ARM64 container, with no skips.
  The archive was mounted read-only, network disabled, capabilities removed,
  non-root identity selected, and no host credentials or Docker socket mounted.

## Repairs found by live testing

The minimal CLI environment omitted USER, preventing Claude from finding the
macOS Keychain login. Preserve this non-secret identity variable while continuing
to exclude API keys and paid-provider overrides. Both real adapter login checks
now succeed without exposing account details.

The validator image lacked Git and Node, which newer integration tests need.
Include both tools. A subprocess fixture also attempted to execute a script from
its noexec temporary mount; invoke that fixture through the installed Python
interpreter while retaining the container restriction.

Repair now clears an earlier review-resume phase and approval, preventing a later
implementation retry from accidentally resuming an obsolete review. Reject review
JSON that is not an object. Regression tests cover these changes.

## Boundaries and remaining work

The automatic merge guard refused main because branch protection is absent. An
initial supervised merge attempt was also rejected by automatic approval review;
no merge occurred until the user answered the explicit risk-specific approval
question. This was permission for one documentation-only merge, not permission to
enable unattended merging or change repository settings.

The runner uses explicit commands for publication and CI advancement. The later
[CI repair and interruption acceptance](ci-repair-and-interruption.md) verified a
real GitHub failure/model repair, process-death containment, lease-expiry recovery,
active Docker interruption, and a kill between PR creation and checkpoint saving.
Server-enforced protected merging, dependency/image locking, and a background
service remain follow-up work before unattended use. Repository protection settings
were not changed, and draft PR #17 was not merged.
