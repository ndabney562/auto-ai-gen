# Local subscription runner prototype

Status: bounded local execution prototype with a durable assigned-task queue,
restart recovery, subscription cooldowns, and automatic integration recovery.
Live subscription handoffs, isolated validation, independent exact-commit reviews,
real CI-failure repair and publication recovery have passed supervised trials.
A local three-task unattended trial passed using fixture CLIs/local publication;
real-model multi-task completion after a genuine quota reset and physical reboot
acceptance remain to be observed. See [queue/integration evidence](queue-integration-acceptance.md).

The default runtime now selects installed `codex` and `claude` commands. It never
selects the API workers from environment keys. CLI children receive a minimal
environment without API keys, cloud-provider overrides, or GitHub tokens. Before
requesting a model response, the adapters require saved ChatGPT login or a
Claude.ai Pro/Max login. Unsupported or ambiguous authentication fails closed.
Existing API modules and the manual API engineering workflow remain opt-in legacy
paths; do not invoke them for subscription tasks.

The CLI adapters are proposal and review workers. They run in empty temporary
folders with tools and customizations disabled; neither receives a source checkout.
Codex uses structured events, read-only sandboxing, `--ignore-user-config`, and
explicit disabled features. Claude uses JSON output, safe/restricted modes, no
built-in or MCP tools, and no hooks. Claude `--bare` is intentionally excluded
because it does not use subscription OAuth. Current CLIs supporting these flags
are required. Credentials stay in the tools' existing local login stores.

## Prerequisites and supervised acceptance

Use Python 3.11+, subscription-authenticated Codex and Claude CLIs, a local Docker
runtime, and authenticated GitHub CLI (`gh`). The supervised host now has these tools and both subscription logins have been
verified. Desktop-app login alone does not establish CLI availability or
subscription authentication.

Install the project and tests in an isolated environment. Build the validator image
from this trusted checkout, before introducing generated changes:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r validation/requirements.lock
docker build -f validation/Dockerfile -t auto-ai-gen-validator:local .
codex login status
claude auth status
gh auth status
```

Do not configure API keys or purchase API credits. These CLI calls use account
allowances; the runner waits or stops when access is unavailable. This code cannot
change subscription limits or prevent extra usage enabled separately on an account.

Create one bounded documentation task against a specific committed ref. Substitute
your repository path and keep the state directory separate from all source folders:

```sh
.venv/bin/python -m orchestrator.subscription_runner --state /tmp/agent-pilot create \
  --repo /path/to/auto-ai-gen --ref origin/main \
  --objective 'Add a short explanation of durable checkpoints' \
  --target docs/checkpoint-pilot.md
```

The command prints a task ID. Use it in subsequent commands:

```sh
.venv/bin/python -m orchestrator.subscription_runner --state /tmp/agent-pilot run TASK_ID
.venv/bin/python -m orchestrator.subscription_runner --state /tmp/agent-pilot publish TASK_ID \
  --repository ndabney562/auto-ai-gen
```

`step` advances one phase; `run` repeats phases until an external action is needed.
A dedicated clone preserves the source checkout and concurrent work. Proposals build
an entire candidate Git tree using a private index, without sequential file edits.
The immutable candidate is validated inside a container with no network, host
credentials, Docker socket, or writable source mount. CPU, memory, process, and
time limits bound execution. A missing image/runtime stops at WAIT_HUMAN instead
of silently running tests on the host. After fixing prerequisites, use `resume TASK_ID`.
Do not point the older generic scheduler at this driver's dedicated state directory.

Validation and the other provider's review must both name the exact candidate SHA.
An unavailable independent reviewer causes a wait; failover cannot approve itself.
The persisted manifest retains candidate SHA, original base, sessions, provider,
review, validation logs, failures, attempts, and next action. Resumption uses this
explicit manifest rather than either model's hidden conversation state. A restart
may repeat an interrupted model request, but does not repeat a checkpointed phase.
Repair attempts are bounded. New provider failures persist sanitized cooldowns.

Publishing reconciles an existing branch/PR, including after a crash. Each repaired
candidate has its own branch. Re-running `publish` reads failed CI logs and routes
failures back to REPAIR; use `run` again for the bounded repair cycle. The [background supervisor](background-supervisor.md) can drive these phases for
an explicitly assigned task or durable queue, with merging disabled by default.
When main advances, [integration recovery](automatic-integration.md) creates a new
candidate and requires fresh validation, independent review and exact-head CI.

`publish --merge-verified` is opt-in for after supervised acceptance. It requires
matching candidate validation/review, latest successful exact-head CI with Lint
and Test, unchanged current main, and GitHub strict required `verify` checks
enforced for administrators. Missing protection blocks automatic merge. The GitHub
merge request includes the expected head SHA. DONE is recorded only after confirmed
merge; waiting for CI or returning model text cannot complete a task.

## Acceptance evidence and remaining work

The live [subscription pilot](live-subscription-pilot.md) and
[CI repair/interruption trials](ci-repair-and-interruption.md) cover both real
subscription CLIs, simulated quota failover, independent-reviewer unavailability,
a real GitHub CI failure/model repair, coordinator process death, active Docker
interruption, and publication checkpoint recovery. These are supervised trials.
Inspect repository branch protection and enforce immutable dependency/image
provenance before allowing unattended execution. Validation dependencies are [hash locked](reproducible-validation.md). The local
supervisor supports cancellation and process recovery; physical reboot acceptance
and additional repository context gathering remain future work.

Official interfaces: [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[Claude programmatic execution](https://code.claude.com/docs/en/headless),
[Claude CLI reference](https://code.claude.com/docs/en/cli-reference).

Review responses may be a JSON object or a single complete `json`/unlabelled
Markdown code fence containing that object. Extra prose, multiple blocks and
non-object responses are rejected. This envelope handling does not relax explicit
boolean approval, exact candidate SHA, independent-provider or validation gates.
Malformed responses consume the bounded review-attempt budget and stop at
`inspect_review_response`; they do not mark provider authentication or availability
as failed. Raw reviewer text is not stored in the failure checkpoint.
