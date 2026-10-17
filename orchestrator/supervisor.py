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
from threading import Thread

from orchestrator import task_queue
from orchestrator.github_publish import GitHubPublisher
from orchestrator.store import JsonTaskStore

PAUSED = {"WAIT_HUMAN", "BLOCKED", "DONE", "CANCELLED", "FAILED_PERMANENT"}
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ServiceAlreadyRunning(RuntimeError):
    """The nonblocking lifetime service lock is busy."""


def records(state):
    return JsonTaskStore(state / "supervisor")


def configure(state: Path, task_id: str | None, repository: str, image: str, *, merge=False,
              interval=30) -> None:
    GitHubPublisher(repository)  # validate before persisting any configuration
    if not IMAGE_ID.fullmatch(image):
        raise ValueError("An immutable local sha256 image ID is required")
    if not 5 <= interval <= 3600:
        raise ValueError("Polling interval must be 5 to 3600 seconds")
    if task_id is not None:
        JsonTaskStore(state / "tasks").load(task_id)
    with service_lock(state):
        records(state).save("config", {"id": "config", "task_id": task_id,
            "repository": repository, "image": image, "merge": merge, "interval": interval,
            "mode": "queue" if task_id is None else "single"})
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
            raise ServiceAlreadyRunning("Supervisor is already running") from exc
        yield


def running(state):
    try:
        with service_lock(state):
            return False
    except ServiceAlreadyRunning:
        return True


def status(state):
    store = records(state)
    config = store.load("config")
    diagnostics = {}
    for name in ("last-phase", "last-phase-failure"):
        try:
            diagnostics[name.replace("-", "_")] = store.load(name)
        except FileNotFoundError:
            pass
    if config.get("mode") == "queue":
        return {**diagnostics, "running": running(state), "mode": "queue", "merge_enabled": config["merge"],
                **task_queue.summary(state)}
    task = JsonTaskStore(state / "tasks").load(config["task_id"])
    health = store.load("health")
    return {**diagnostics, "running": running(state), "task_id": task["id"], "task_status": task["status"],
            "next_action": task.get("next_action", {}).get("type"),
            "merge_enabled": config["merge"], "retry_at": task.get("retry_at"),
            "waiting_provider": task.get("next_action", {}).get("provider"),
            "supervisor": health}


def request(state, command, task_id=None):
    try:
        config = (json.loads((state / "supervisor" / "config.json").read_text())
                  if task_id or command == "cancel" else {})
    except FileNotFoundError:
        config = {}
    if task_id and config.get("mode", "single") != "queue" and task_id != config.get("task_id"):
        raise ValueError("--task must match the configured single task")
    if command == "cancel" and config.get("mode") == "queue":
        with task_queue.locked_queue(state) as queue:
            task_id = task_id or queue["active_task"]
            if not task_id or task_id not in {j["task_id"] for j in queue["jobs"]}:
                raise ValueError("Queue cancellation requires an enrolled --task ID")
        marker = task_queue.cancel_path(state, task_id)
    else:
        records(state)
        marker = state / "supervisor" / command
    marker.touch(mode=0o600)
    if command != "cancel":
        return
    try:
        with service_lock(state):
            # Holding the lifetime lock excludes a service and its active phase.
            # Publish the marker first so a busy service can reap its own phase.
            config = records(state).load("config")
            cancel_task(state, task_id or config["task_id"])
            marker.unlink(missing_ok=True)
    except ServiceAlreadyRunning:
        # The running service consumes the marker only after phase cleanup.
        return


def cancel_task(state, task_id):
    # Caller holds the lifetime lock with no active phase, or has reaped the
    # active phase first. A prior push/merge may already have happened.
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
    return [*base, "step", config["task_id"], "--image", config["image"],
            "--repository", config["repository"]]


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
    from orchestrator.phase_diagnostics import TraceMetadata

    store = records(state)
    started = datetime.now(UTC)
    clock = time.monotonic()
    metadata = TraceMetadata()
    task_id = (command[6] if len(command) > 6 and command[1:3] ==
               ["-m", "orchestrator.subscription_runner"] and command[5] in
               {"step", "publish"} and re.fullmatch(r"[0-9a-f]{32}", command[6]) else None)
    record = {"id": "last-phase", "task_id": task_id,
              "started_at": started.isoformat(), "outcome": "running"}
    try:
        previous = store.load("last-phase")
        if previous["outcome"] == "running":
            store.save("last-phase-failure", dict(previous, id="last-phase-failure",
                       outcome="interrupted_before_recording_exit"))
    except FileNotFoundError:
        pass
    store.save("last-phase", record)
    guard = Path(__file__).parents[1] / "providers" / "_cli_guard.py"
    child = None
    reader = None
    result, outcome = None, "launch_error"

    def drain():
        while chunk := child.stderr.read(4096):
            metadata.feed(chunk)

    try:
        child = subprocess.Popen([sys.executable, str(guard), str(os.getpid()), "[]", *command],
            cwd=Path(__file__).parents[1], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, start_new_session=True)
        reader = Thread(target=drain, daemon=True)
        reader.start()
        outcome = "coordinator_error"
        deadline = clock + timeout
        while child.poll() is None:
            if stopping() or (state / "supervisor" / "cancel").exists():
                outcome = "stopped"
                return None
            if time.monotonic() >= deadline:
                result, outcome = 124, "timeout"
                return result
            time.sleep(0.1)
        result = child.returncode
        outcome = "success" if result == 0 else "nonzero_exit"
        return result
    except OSError as exc:
        # Error messages may contain credentials or paths. Persist the type only.
        metadata.line(type(exc).__name__)
        raise
    finally:
        if child is not None:
            terminate(child)
            # A surviving descendant can retain stderr after its parent exits.
            # Kill only this private phase group before waiting for EOF.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if reader is not None:
            reader.join(timeout=5)
        complete = reader is None or not reader.is_alive()
        if child is not None and complete:
            child.stderr.close()
        ended = datetime.now(UTC)
        record.update(outcome=outcome, exit_code=result, ended_at=ended.isoformat(),
                      wall_elapsed_seconds=max(0, (ended - started).total_seconds()),
                      monotonic_elapsed_seconds=max(0, time.monotonic() - clock),
                      stderr_complete=complete, **(metadata.finish() if complete else
                          {"exception_type": None, "frames": [],
                           "stderr_bytes": metadata.bytes_seen}))
        store.save("last-phase", record)
        if outcome != "success":
            store.save("last-phase-failure", dict(record, id="last-phase-failure"))


def tick(state, config, stopping, *, phase=run_phase, now=time.time):
    if config.get("mode") == "queue":
        selected = task_queue.select_task(state, now=datetime.fromtimestamp(now(), UTC))
        if selected is None:
            return
        try:
            _tick_task(state, dict(config, task_id=selected), stopping, phase=phase, now=now,
                       health=task_queue.health(state, selected),
                       cancellation=task_queue.cancel_path(state, selected))
        finally:
            with task_queue.locked_queue(state) as queue:
                queue["active_task"] = None
        return
    _tick_task(state, config, stopping, phase=phase, now=now)


def _tick_task(state, config, stopping, *, phase, now, health=None, cancellation=None):
    cancellation = cancellation or state / "supervisor" / "cancel"
    if cancellation.exists():
        cancel_task(state, config["task_id"])
        cancellation.unlink(missing_ok=True)
        return
    store = records(state)
    health = health or store.load("health")
    if health["failures"] >= 3 or health["retry_after"] > now():
        return
    task = JsonTaskStore(state / "tasks").load(config["task_id"])
    if task["status"] in PAUSED:
        return
    if task.get("retry_at") and datetime.fromisoformat(task["retry_at"]) > datetime.now(UTC):
        return
    code = phase(state, phase_command(state, config, task),
                 lambda: stopping() or cancellation.exists())
    # Cancellation takes precedence over retry bookkeeping, after phase cleanup.
    if cancellation.exists():
        cancel_task(state, config["task_id"])
        cancellation.unlink(missing_ok=True)
    elif code is not None:
        failures = health["failures"] + 1 if code else 0
        store.save(health["id"], {"id": health["id"], "failures": failures,
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
    label = "local.auto-ai-gen." + (config["task_id"] or "queue")
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
    queue_setup = sub.add_parser("configure-queue")
    queue_setup.add_argument("--repository", required=True)
    queue_setup.add_argument("--image", required=True)
    queue_setup.add_argument("--interval", type=int, default=30)
    queue_setup.add_argument("--merge-verified", action="store_true")
    enroll = sub.add_parser("enqueue")
    enroll.add_argument("task_id")
    enroll.add_argument("--after", action="append", default=[])
    for name in ("start", "serve", "stop", "status"):
        sub.add_parser(name)
    cancel = sub.add_parser("cancel")
    cancel.add_argument("--task")
    agent = sub.add_parser("launch-agent")
    agent.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    state = args.state.resolve()
    if args.command == "configure":
        configure(state, args.task_id, args.repository, args.image,
                  merge=args.merge_verified, interval=args.interval)
    elif args.command == "configure-queue":
        configure(state, None, args.repository, args.image,
                  merge=args.merge_verified, interval=args.interval)
    elif args.command == "enqueue":
        task_queue.enqueue(state, args.task_id, args.after)
    elif args.command == "start":
        start(state)
    elif args.command == "serve":
        serve(state)
    elif args.command == "status":
        print(json.dumps(status(state), indent=2))
    elif args.command == "launch-agent":
        launch_agent(state, args.output)
    else:
        request(state, args.command, getattr(args, "task", None))


if __name__ == "__main__":
    main()
