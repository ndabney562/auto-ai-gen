# HANDOFF

**Goal:** Build auto-ai-gen into a durable control plane for autonomous multi-model engineering agents (Claude implements, OpenAI reviews), with safe queueing, integration, CI repair, and supervised unattended operation.

**Done:**
- Durable task queue with dependencies, round-robin phases, error budgets, and targeted cancellation (PR #25).
- Subscription-based local runner and background supervisor (PRs #26–#30).
- Automatic integration recovery with fresh verification, and stale-candidate handling (PR #26).
- Phase diagnostics and interruption recovery with lease fencing (PR #31).
- Fenced review-JSON parsing and clearer review-format error handling (PRs #32, #36).
- Source-context size guard before worker dispatch, with a pinned retained-blob ceiling (PR #37).
- Local unattended three-task queue trial, including quota restart and integration conflicts (`tests/acceptance/multi_task_queue.py`; zero real model or GitHub requests).
- Live-proof docs updated with current boundaries (PR #38).

**In progress:**
- Nothing is in progress on this branch (`hivemind/optimize-20261104-225403`). The working tree was clean at handoff; the only change is the new HANDOFF.md.

**Next step (exact):** Run the live subscription queue with three real tasks across a genuine Claude quota reset and a physical reboot, then record the evidence in `docs/queue-integration-acceptance.md`.

**Blockers:**
- No real-model unattended completion has been observed across a real quota reset or reboot (`docs/queue-integration-acceptance.md`).
- The API live-proof workflow (`claude-engineering.yml`) needs repository owner setup: `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` secrets with API credit, and workflow permission to create PRs.
- The multi-file patchset engine (`orchestrator/multifile_engineering.py`) is not yet wired into that API workflow, which still allows one file per request.

**Last test result:** Not run in this session; the test command required approval and was not run. The most recent recorded result is in `docs/queue-integration-acceptance.md`: 189 tests and lint passed in the pinned offline image. Re-run `pytest -q` and `ruff check .` before relying on this.

**Last commit hash:** `193f393ca0307481451270c2d930e646a99a0783` (docs: update CLAUDE_LIVE_SMOKE current boundaries (#38))

**Model + timestamp:** Haiku 5.5 (`claude-haiku-5-5`), 2026-11-05T03:54:34Z
