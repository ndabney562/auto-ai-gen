# Automatic integration recovery

The trusted runner fetches current main before READY implementation phases. If a
queued task was created against an older main, it refreshes its dedicated clone
before requesting a proposal. Source context is read from immutable Git blobs,
including whitespace, rather than potentially interrupted working-tree files.

If main advances after a candidate has been reviewed or CI has started, the
publisher saves an INTEGRATING intent before touching its private checkout. Git's
three-way merge builds a replacement tree without running candidate code on the
host. An interrupted integration replays that saved intent on restart.

A clean integration creates a new candidate parented on current main. Its prior
validation and review are discarded. The new SHA must pass isolated validation,
independent review and fresh exact-head CI before merging. Publication uses a new
branch/PR; the previous PR is closed only after the replacement is reviewed and
published, and only if its recorded head has not changed. No user branch is
force-pushed. A main move at the final merge gate routes through the same recovery.

Conflicts return bounded base/upstream/candidate/merge context to the implementation
worker. It must preserve upstream work and resolve the conflict within the task's
existing allowlisted targets and implementation-attempt budget. Unresolved Git
conflict markers are rejected before validation. The resolved SHA gets a new
independent review; an old approval cannot approve the integrated replacement.

Upstream history rewrites, changes outside the target allowlist, excessively large
conflict context, or more than five candidate integrations stop that task safely.
Other independent queued tasks continue. An already-applied change without a
confirmed matching merged PR stops for explicit reconciliation rather than falsely
recording DONE. Initial no-candidate base refreshes do not spend the integration
budget. These bounds are deliberate; this is not unrestricted conflict resolution.
