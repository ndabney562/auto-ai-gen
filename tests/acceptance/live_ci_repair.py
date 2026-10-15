"""Opt-in real CI repair trial. Creates draft fixture PRs; never merges anything.

Run from the coordinator checkout with --state outside the source tree.
Each invocation advances one phase, allowing process restarts between phases.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchestrator.github_publish import GitHubPublisher
from orchestrator.store import JsonTaskStore
from orchestrator.subscription_runner import advance_task, create_task, publish_task

OBJECTIVE = """Create tests/test_ci_repair_pilot.py for a temporary, draft-only CI fault-injection
acceptance trial that will NEVER be merged. There are two stages:
1. If no previous failure logs are supplied, create a pytest test that passes locally but
intentionally fails when os.environ.get('GITHUB_ACTIONS') == 'true'. Use a clear assertion
message 'INTENTIONAL_CI_REPAIR_PILOT_FAILURE'. This deliberate negative fixture tests CI feedback.
2. When previous GitHub CI failure logs ARE supplied, remove all environment-dependent
failure injection and replace the fixture with a simple portable passing test asserting
2 + 2 == 4. No skips, xfails, environment checks, or weakening other tests. This is the final
successful repair. Modify exactly this file. No other source changes.
"""


class DraftPublisher(GitHubPublisher):
    def _gh(self, *args):
        if args[:2] == ("pr", "create"):
            options = list(args)
            options[options.index("--title") + 1] = "[DO NOT MERGE] CI-repair acceptance fixture"
            options[options.index("--body") + 1] = (
                "Temporary fault-injection acceptance test for PR #17. The first candidate is "
                "expected to fail GitHub CI; a later candidate tests model repair. "
                "This fixture must never be merged and will be closed after evidence is recorded.")
            return super()._gh(*options, "--draft")
        return super()._gh(*args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--repository", default="ndabney562/auto-ai-gen")
    parser.add_argument("--image", default="auto-ai-gen-validator:pilot-v2")
    args = parser.parse_args()
    args.state.mkdir(parents=True, exist_ok=True)
    identity = args.state / "task-id.txt"
    if not identity.exists():
        identity.write_text(create_task(args.state, args.repo, OBJECTIVE,
                            ["tests/test_ci_repair_pilot.py"], "origin/main"))
    task_id = identity.read_text().strip()
    task = JsonTaskStore(args.state / "tasks").load(task_id)
    if task["status"] == "WAIT_CI":
        task = publish_task(args.state, task_id, args.repository,
                            publisher=DraftPublisher(args.repository))
    else:
        task = advance_task(args.state, task_id, image=args.image)
    print(json.dumps({"task_id": task_id, "status": task["status"],
                      "attempt": task["attempt"],
                      "candidate_sha": task["checkpoint"].get("candidate_sha"),
                      "review": task["checkpoint"].get("review"),
                      "validation": task.get("validation"),
                      "pull_request": task["checkpoint"].get("pull_request"),
                      "previous_pull_request": task["checkpoint"].get("previous_pull_request")}))


if __name__ == "__main__":
    main()
