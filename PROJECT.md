# auto-ai-gen

Durable control plane for autonomous multi-model AI engineering agents that use OpenAI, Anthropic and GitHub.

## Milestones

- [x] Stage 11: one-file Claude implementation path with independent OpenAI review (`claude-engineering.yml`)
- [x] Offline pytest suite and ruff lint in GitHub CI (`ci.yml`)
- [x] Durable task queue with dependencies, round-robin phase selection and per-task error budgets
- [x] Multi-file patchsets with transactional apply, rollback and crash recovery
- [x] Bounded automatic repair for validation failures, review rejections, CI failures and integration conflicts
- [x] Background supervisor validated in an offline, non-root Docker container (Oct 16, 2026)
- [x] Local unattended three-task queue trial with zero real model and GitHub requests
- [ ] Live Claude + OpenAI API acceptance run through `claude-engineering.yml`, with reviewed PR and auto-merge
- [ ] Wire the multi-file engine into the `claude-engineering.yml` workflow
- [ ] Real-model multi-task completion across a genuine subscription quota reset
- [ ] Physical logout and reboot acceptance for the generated macOS LaunchAgent
