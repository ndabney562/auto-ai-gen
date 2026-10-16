"""Abrupt-death and resume probe using real local Docker; no model calls or PRs."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchestrator.store import JsonTaskStore
from orchestrator.subscription_runner import advance_task, create_task


def running(name):
    result = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", name],
                            check=False, capture_output=True, text=True, timeout=10)
    return result.returncode == 0 and result.stdout.strip() == "true"


def wait_for(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise TimeoutError("Interruption probe did not reach expected state")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--image", default="auto-ai-gen-validator:pilot-v2")
    parser.add_argument("--child")
    args = parser.parse_args()
    if args.child:
        task = advance_task(args.state, args.child, image=args.image)
        print(json.dumps({"status": task["status"]}))
        return
    args.state.mkdir(parents=True, exist_ok=True)
    evidence = []
    for signum in (signal.SIGKILL, signal.SIGTERM):
        task_id = create_task(args.state, args.repo, "Local-only interruption fixture; never publish",
                              ["tests/test_interrupt_pilot.py"], "origin/main")
        store = JsonTaskStore(args.state / "tasks")
        task = store.load(task_id)
        task["status"] = "VALIDATING"
        task["result"] = json.dumps({"files": [{"path": "tests/test_interrupt_pilot.py",
            "content": "import time\n\n\ndef test_interruption_fixture():\n    time.sleep(5)\n    assert 2 + 2 == 4\n"}]})
        store.save(task_id, task)
        workspace = Path(task["checkpoint"]["workspace"])
        registry = JsonTaskStore(workspace / ".git" / "agent-validation")
        command = [sys.executable, str(Path(__file__).resolve()), "--state", str(args.state),
                   "--repo", str(args.repo), "--image", args.image, "--child", task_id]
        coordinator = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        container = None
        try:
            wait_for(lambda registry=registry: (registry.root / "active.json").exists())
            container = registry.load("active")["name"]
            wait_for(lambda container=container: running(container))
            before = store.load(task_id)
            sha = before["checkpoint"]["candidate_sha"]
            os.kill(coordinator.pid, signum)
            coordinator.communicate(timeout=5)
            assert store.load(task_id)["status"] == "VALIDATING"
            wait_for(lambda container=container: not running(container))
            # Restart a real process using the saved SHA, not a regenerated proposal.
            resumed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=45)
            assert json.loads(resumed.stdout)["status"] == "REVIEW"
            after = store.load(task_id)
            assert after["checkpoint"]["candidate_sha"] == sha
            assert after["validation"]["candidate_sha"] == sha
            assert after["validation"]["passed"] is True
            assert not running(registry.load("active")["name"])
            evidence.append({"signal": signal.Signals(signum).name, "candidate_sha": sha,
                             "old_container_stopped": True, "resumed_status": after["status"],
                             "validation_log": after["validation"]["log"]})
            print(json.dumps(evidence[-1]), flush=True)
        finally:
            if coordinator.poll() is None:
                coordinator.kill()
                coordinator.communicate(timeout=5)
            if container:
                subprocess.run(["docker", "rm", "-f", container], check=False,
                               capture_output=True, timeout=15)
    (args.state / "interruption-evidence.json").write_text(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
