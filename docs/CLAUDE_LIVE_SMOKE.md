# Live Claude + OpenAI engineering acceptance test

Stage 11 provides a real, **one-file** Claude implementation path with an
independent OpenAI review. Offline tests use stubs; they do not prove the
provider APIs were contacted.

## One-time GitHub setup (repository owner)

1. Open **Settings > Secrets and variables > Actions** in this repository.
2. Add *repository secrets* named `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`.
   Both must have usable **API** credit; consumer chat subscriptions are not
   substitutes. Never put API keys in issues, PRs, commits, or chat messages.
3. In repository **Settings > Actions > General**, make sure the workflow token
   is allowed to **create pull requests**. The workflow requests the scoped
   permissions it needs, but repository policy can still deny them.
4. Optionally set repository Actions variables `ANTHROPIC_MODEL` and
   `OPENAI_MODEL`. Defaults for this milestone are `claude-sonnet-5-5`
   and `gpt-6.1-sol`, respectively.

## Launch the live proof

After Stage 11 is merged, navigate to **Actions > Claude Implements, OpenAI Reviews >
Run workflow**, choose `main`, and use:

- **task**: `Create concise documentation explaining how this repo safely hands
  off engineering tasks between Claude and OpenAI.`
- **target_file**: `docs/claude-live-proof.md`

The workflow then:

1. calls the live Claude API to propose a complete replacement of that file;
2. calls the live OpenAI API to review the exact proposed content;
3. writes only after the reviewer explicitly approves;
4. runs `ruff check .` and `pytest -q`, opens a PR, and explicitly dispatches
   GitHub CI on its branch;
5. lets the separate SHA-checked auto-merge workflow merge the PR only after
   GitHub CI actually passes.

Acceptance requires the **workflow run log**, the **Claude-authored PR diff**,
the **independent reviewer approval**, and the successful **GitHub CI +
auto-merge**. Merely passing the repository unit tests does not meet acceptance.

## Current boundaries

- One allowlisted `.py` or `.md` file per request, within
  `orchestrator/`, `providers/`, `tests/`, or `docs/`.
- No arbitrary shell execution by either model, no direct access to the GitHub
  token, and no edits to workflows or CI by the model.
- This is not yet a general multi-file coding agent, nor does it automatically
  repair its own model-output failures. Those are later milestones.
