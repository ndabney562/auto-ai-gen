# Local subscription runner prototype

Status: draft prototype. Unit and process-fixture tests exercise the orchestration;
the live two-provider/Docker/GitHub acceptance run has not passed. Do not enable
unattended merging until that run succeeds.

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
runtime, and authenticated GitHub CLI (`gh`). The current execution host has no
Claude or Docker executable available. Desktop-app login alone does not establish
CLI availability or subscription authentication.

Install the project and tests in an isolated environment. Build the validator image
from this trusted checkout, before introducing generated changes:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
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
failures back to REPAIR; use `run` again for the bounded repair cycle. CI and PR
publishing currently require these explicit commands, not a background daemon.

`publish --merge-verified` is opt-in for after supervised acceptance. It requires
matching candidate validation/review, latest successful exact-head CI with Lint
and Test, unchanged current main, and GitHub strict required `verify` checks
enforced for administrators. Missing protection blocks automatic merge. The GitHub
merge request includes the expected head SHA. DONE is recorded only after confirmed
merge; waiting for CI or returning model text cannot complete a task.

## Remaining acceptance work

Run the pilot with both real subscription CLIs and Docker. Interrupt after each
checkpoint, simulate a usage-limit failover, make the independent reviewer
unavailable, introduce and repair a CI failure, and verify PR/merge reconciliation.
Inspect CLI versions, account authentication, container isolation, and repository
branch protection before allowing unattended execution. Dependency locking,
additional repository context gathering, and richer cancellation of in-flight
external phases remain future work.

Official interfaces: [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[Claude programmatic execution](https://code.claude.com/docs/en/headless),
[Claude CLI reference](https://code.claude.com/docs/en/cli-reference).
