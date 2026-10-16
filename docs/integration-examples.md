# Automatic integration recovery examples

Automatic recovery preserves upstream work and creates a fresh, reviewable candidate. See [automatic integration](automatic-integration.md) for the integration lifecycle and [durable queue](durable-queue.md) for persisted intent and scheduling.

## Example 1: Main advances and replay is clean

A candidate updates a settings page. Before it lands, main advances with an unrelated logging change. Persist durable `INTEGRATING` intent before starting recovery so a restart can resume the integration rather than lose the pending operation.

Replay the candidate onto current main, retaining the logging change. Publish the clean replacement on a new branch and PR; do not overwrite a user branch. The replacement goes through `VALIDATING`, then `REVIEW`, then `WAIT_CI`. Invalidate the old candidate's validation and review: neither proves anything about the replacement head. Require fresh exact-head CI and an independent review by a provider other than the implementation provider.

Once the replacement is ready, retire the superseded PR only if it is unchanged from the recorded superseded state. If someone has changed it, stop its automatic retirement and preserve their work.

## Example 2: Overlapping edits require model repair

A candidate changes a retry policy while main changes the same policy to honor a new server limit. Replay conflicts. Preserve durable `INTEGRATING` intent and give the repair model bounded base, upstream, and candidate context, together with the existing task targets. The repair must retain the upstream server limit while implementing the requested retry behavior; the conflict does not authorize additional scope.

Reject a repair containing unresolved conflict markers. A conflict-free repair still needs a new branch and PR, invalidation of the old validation and review, and the full `VALIDATING` → `REVIEW` → `WAIT_CI` sequence. Require independent other-provider review and fresh CI for the exact replacement head. Retire the superseded PR only if it remains unchanged.

## Safety stops

Stop automatic recovery for the affected candidate when history has been rewritten, recovery touches unrequested paths, conflict context exceeds the permitted bound, or recovery would require more than five candidate integrations. Preserve the pending state and the reason for intervention. These stops apply to the affected candidate; unrelated queued work continues.
