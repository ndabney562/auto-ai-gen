"""Real subprocess death tests; no external model requests."""
import os
import signal
import subprocess
import sys
import time

import pytest


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    pytest.fail("Timed out waiting for child process state")


@pytest.mark.parametrize("signum", [signal.SIGKILL, signal.SIGTERM])
def test_killed_coordinator_stops_cli_and_its_descendant(tmp_path, signum):
    heartbeat = tmp_path / "heartbeat"
    child = tmp_path / "child.py"
    child.write_text(
        "import pathlib,time,sys\n"
        "p=pathlib.Path(sys.argv[1])\n"
        "while True:\n"
        " p.write_text(str(time.monotonic()))\n"
        " time.sleep(.02)\n")
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n"
        "time.sleep(60)\n")
    command = [sys.executable, str(worker), str(child), str(heartbeat)]
    code = ("from providers.cli_provider import CliWorker; "
            f"CliWorker('test', {tuple(command)!r}, 60).execute({{'prompt':'fixture'}})")
    coordinator = subprocess.Popen([sys.executable, "-c", code],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(heartbeat.exists)
        os.kill(coordinator.pid, signum)
        coordinator.wait(timeout=5)
        # The guardian observes reparenting and kills its private process group.
        time.sleep(.25)
        last = heartbeat.read_text()
        time.sleep(.15)
        assert heartbeat.read_text() == last
    finally:
        if coordinator.poll() is None:
            coordinator.kill()
            coordinator.wait(timeout=5)


def test_killed_execution_recovers_after_real_lease_expiry(tmp_path):
    import json
    from datetime import UTC, datetime

    from orchestrator.scheduler import dispatch_task
    from orchestrator.store import JsonTaskStore
    from providers.health import ProviderHealth

    store_path = tmp_path / "tasks"
    store = JsonTaskStore(store_path)
    task = dispatch_task({"id": "crash", "status": "READY",
                          "checkpoint": {"completed_work": ["preserve snapshot"]}},
                         [ProviderHealth("fixture")])
    store.save("crash", task)
    started = tmp_path / "started"
    worker_code = f"import pathlib,time;pathlib.Path({str(started)!r}).write_text('ready');time.sleep(60)"
    root_code = f'''
from pathlib import Path
from orchestrator.store import JsonTaskStore
from orchestrator.executor import execute_claimed_task
from providers.cli_provider import CliWorker
from providers.health import ProviderHealth
import sys
store=JsonTaskStore({str(store_path)!r})
execute_claimed_task(store,store.load('crash'),lambda:[ProviderHealth('fixture')],
    {{'fixture':lambda:CliWorker('fixture',(sys.executable,'-c',{worker_code!r}))}},
    audit_path={str(tmp_path / 'audit')!r},heartbeat_seconds=.05,lease_seconds=.3)
'''
    coordinator = subprocess.Popen([sys.executable, "-c", root_code],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(started.exists)
        assert store.load("crash")["status"] == "EXECUTING"
        coordinator.kill()
        coordinator.wait(timeout=5)
        wait_for(lambda: datetime.fromisoformat(store.load("crash")["lease"]["expires_at"])
                 <= datetime.now(UTC))
        restart_code = f'''
import json
from orchestrator.store import JsonTaskStore
from orchestrator.recovery import recover_after_restart
from orchestrator.scheduler import dispatch_task
from orchestrator.executor import execute_claimed_task
from providers.base import WorkerResult
from providers.health import ProviderHealth
store=JsonTaskStore({str(store_path)!r})
task=store.load('crash')
recovered=recover_after_restart(task)
assert recovered['status']=='READY'
assert store.compare_and_save(task,recovered)
claimed=dispatch_task(recovered,[ProviderHealth('fixture')])
assert store.compare_and_save(recovered,claimed)
class RecoveredWorker:
    def execute(self,handoff):
        assert handoff['completed_work']==['preserve snapshot']
        return WorkerResult('resumed fixture')
result=execute_claimed_task(store,claimed,lambda:[ProviderHealth('fixture')],
    {{'fixture':RecoveredWorker}},audit_path={str(tmp_path / 'audit')!r})
print(json.dumps(result))
'''
        completed = subprocess.run([sys.executable, "-c", restart_code], check=True,
                                   capture_output=True, text=True, timeout=5)
        result = json.loads(completed.stdout)
        assert result["status"] == "VALIDATING"
        assert result["attempt"] == 2
        assert result["checkpoint"]["completed_work"] == ["preserve snapshot"]
        assert result["lease"] is None
    finally:
        if coordinator.poll() is None:
            coordinator.kill()
            coordinator.wait(timeout=5)


def test_guard_refuses_an_inherited_process_group(tmp_path):
    from pathlib import Path

    from providers import cli_provider

    marker = tmp_path / "must-not-start"
    guard = Path(cli_provider.__file__).with_name("_cli_guard.py")
    result = subprocess.run([sys.executable, str(guard), str(os.getpid()), "[]",
                             sys.executable, "-c", f"open({str(marker)!r},'w').close()"],
                            check=False, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert "private process group" in result.stderr
    assert not marker.exists()
