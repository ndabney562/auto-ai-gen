"""Single-task local service around the durable subscription runner.

Control files avoid signalling saved PIDs, which may belong to another process
following a restart. Only the service's current child process group is signalled.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import plistlib
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.github_publish import GitHubPublisher
from orchestrator.store import JsonTaskStore

PAUSED = {"WAIT_HUMAN", "BLOCKED", "DONE", "CANCELLED", "FAILED_PERMANENT"}
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def records(state):
    return JsonTaskStore(state / "supervisor")


def configure(state: Path, task_id: str, repository: str, image: str, *, merge=False,
              interval=30) -> None:
    GitHubPublisher(repository)  # validate before persisting any configuration
    if not IMAGE_ID.fullmatch(image):
        raise ValueError("An immutable local sha256 image ID is required")
    if not 5 <= interval <= 3600:
        raise ValueError("Polling interval must be 5 to 3600 seconds")
    JsonTaskStore(state / "tasks").load(task_id)
    with service_lock(state):
        records(state).save("config", {"id": "config", "task_id": task_id,
            "repository": repository, "image": image, "merge": merge, "interval": interval})
        records(state).save("health", {"id": "health", "failures": 0, "retry_after": 0})
        for name in ("stop", "cancel"):
            (state / "supervisor" / name).unlink(missing_ok=True)


@contextmanager
def service_lock(state):
    records(state)
    with (state / "supervisor" / ".service.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Supervisor is already running") from exc
        yield


def running(state):
    try:
        with service_lock(state):
            return False
    except RuntimeError:
        return True


def status(state):
    store = records(state)
    config = store.load("config")
    task = JsonTaskStore(state / "tasks").load(config["task_id"])
    health = store.load("health")
    return {"running": running(state), "task_id": task["id"], "task_status": task["status"],
            "next_action": task.get("next_action", {}).get("type"),
            "merge_enabled": config["merge"], "supervisor": health}


def request(state, command):
    records(state)
    (state / "supervisor" / command).touch(mode=0o600)


def cancel_task(state, task_id):
    # Caller has reaped the active phase first, so no publishing side effect can
    # start after this checkpoint. A prior push/merge may already have happened.
    store = JsonTaskStore(state / "tasks")
    task = store.load(task_id)
    if task["status"] in {"DONE", "CANCELLED", "FAILED_PERMANENT"}:
        return
    updated = dict(task, status="CANCELLED", next_action={"type": "none"})
    if not store.compare_and_save(task, updated):
        raise RuntimeError("Task changed during cancellation")


def phase_command(state, config, task):
    base = [sys.executable, "-m", "orchestrator.subscription_runner", "--state", str(state)]
    if task["status"] == "WAIT_CI":
        return [*base, "publish", config["task_id"], "--repository", config["repository"],
                *(["--merge-verified"] if config["merge"] else [])]
    return [*base, "step", config["task_id"], "--image", config["image"]]


def terminate(child):
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)


def run_phase(state, command, stopping, *, timeout=1200):
    # Guard owns a private group and stops the whole phase if the service dies.
    # Nested CLI/Docker guards see their coordinator disappear and clean up too.
    guard = Path(__file__).parents[1] / "providers" / "_cli_guard.py"
    child = subprocess.Popen([sys.executable, str(guard), str(os.getpid()), "[]", *command],
        cwd=Path(__file__).parents[1], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while child.poll() is None:
            if stopping() or (state / "supervisor" / "cancel").exists():
                return None
            if time.monotonic() >= deadline:
                return 124
            time.sleep(0.1)
        return child.returncode
    finally:
        terminate(child)


def tick(state, config, stopping, *, phase=run_phase, now=time.time):
    control = state / "supervisor"
    if (control / "cancel").exists():
        cancel_task(state, config["task_id"])
        (control / "cancel").unlink(missing_ok=True)
        return
    store = records(state)
    health = store.load("health")
    if health["failures"] >= 3 or health["retry_after"] > now():
        return
    task = JsonTaskStore(state / "tasks").load(config["task_id"])
    if task["status"] in PAUSED:
        return
    if task.get("retry_at") and datetime.fromisoformat(task["retry_at"]) > datetime.now(UTC):
        return
    code = phase(state, phase_command(state, config, task), stopping)
    # Cancellation takes precedence over retry bookkeeping, after phase cleanup.
    if (control / "cancel").exists():
        cancel_task(state, config["task_id"])
        (control / "cancel").unlink(missing_ok=True)
    elif code is not None:
        failures = health["failures"] + 1 if code else 0
        store.save("health", {"id": "health", "failures": failures,
            "retry_after": now() + min(300, 30 * 2 ** failures) if failures else 0,
            "last_exit_code": code, "updated_at": datetime.now(UTC).isoformat()})


def serve(state):
    state = state.resolve()
    with service_lock(state):
        config = records(state).load("config")
        stopped = False

        def stop_signal(*_):
            nonlocal stopped
            stopped = True

        previous = {sig: signal.signal(sig, stop_signal) for sig in (signal.SIGTERM, signal.SIGINT)}

        def stopping():
            return stopped or (state / "supervisor" / "stop").exists()

        try:
            while not stopping():
                tick(state, config, stopping)
                deadline = time.monotonic() + config["interval"]
                while not stopping() and time.monotonic() < deadline:
                    if (state / "supervisor" / "cancel").exists():
                        break
                    time.sleep(0.1)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def start(state):
    # Serialize launch requests separately from the lifetime service lock.
    with (state / "supervisor" / ".start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if running(state):
            raise RuntimeError("Supervisor is already running")
        records(state).load("config")
        (state / "supervisor" / "stop").unlink(missing_ok=True)
        child = subprocess.Popen([sys.executable, "-m", "orchestrator.supervisor",
            "--state", str(state.resolve()), "serve"], cwd=Path(__file__).parents[1],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if running(state):
                return
            if child.poll() is not None:
                raise RuntimeError("Supervisor failed to start")
            time.sleep(0.1)
        terminate(child)
        raise RuntimeError("Supervisor startup timed out")


def launch_agent(state, output):
    # Write a reviewable macOS login-agent artifact; installation is explicit.
    config = records(state).load("config")
    label = "local.auto-ai-gen." + config["task_id"]
    output.write_bytes(plistlib.dumps({"Label": label,
        "ProgramArguments": [sys.executable, "-m", "orchestrator.supervisor", "--state",
                             str(state.resolve()), "serve"],
        "WorkingDirectory": str(Path(__file__).parents[1]),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 30,
        "ProcessType": "Background"}))


def main():
    parser = argparse.ArgumentParser(description="Local subscription task supervisor")
    parser.add_argument("--state", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure")
    setup.add_argument("task_id")
    setup.add_argument("--repository", required=True)
    setup.add_argument("--image", required=True)
    setup.add_argument("--interval", type=int, default=30)
    setup.add_argument("--merge-verified", action="store_true")
    for name in ("start", "serve", "stop", "status", "cancel"):
        sub.add_parser(name)
    agent = sub.add_parser("launch-agent")
    agent.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    state = args.state.resolve()
    if args.command == "configure":
        configure(state, args.task_id, args.repository, args.image,
                  merge=args.merge_verified, interval=args.interval)
    elif args.command == "start":
        start(state)
    elif args.command == "serve":
        serve(state)
    elif args.command == "status":
        print(json.dumps(status(state), indent=2))
    elif args.command == "launch-agent":
        launch_agent(state, args.output)
    else:
        request(state, args.command)


if __name__ == "__main__":
    main()
