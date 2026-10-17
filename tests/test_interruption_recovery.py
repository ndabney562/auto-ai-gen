"""Real process suspension/death tests; these do not claim physical host sleep."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from orchestrator.executor import execute_claimed_task
from orchestrator.recovery import recover_after_restart
from orchestrator.store import JsonTaskStore
from providers.base import WorkerResult
from providers.health import ProviderHealth

CHILD = '''
import sys,time
from pathlib import Path
from orchestrator.executor import execute_claimed_task,LeaseLost
from orchestrator.store import JsonTaskStore
from providers.base import WorkerResult
from providers.health import ProviderHealth
root=Path(sys.argv[1]); store=JsonTaskStore(root/'tasks')
class Worker:
    def execute(self, handoff):
        (root/'entered').touch()
        while not (root/'release').exists(): time.sleep(.01)
        return WorkerResult('stale result must never be saved')
try:
    execute_claimed_task(store,store.load('task'),lambda:[ProviderHealth('fixture')],
        {'fixture':Worker},audit_path=root/'audit.jsonl',heartbeat_seconds=.05,lease_seconds=.3)
except LeaseLost:
    (root/'lease-lost').touch()
'''


@pytest.mark.parametrize('interruption', ['suspend', 'kill'])
def test_expired_execution_recovers_without_accepting_stale_work(tmp_path, interruption):
    store = JsonTaskStore(tmp_path / 'tasks')
    checkpoint = {'completed_work': ['retained'], 'session_id': 'retained-session'}
    store.save('task', {'id': 'task', 'status': 'CLAIMED', 'attempt': 0, 'max_attempts': 3,
                        'checkpoint': checkpoint, 'owner': {'provider': 'fixture'}})
    child = subprocess.Popen([sys.executable, '-c', CHILD, str(tmp_path)])
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / 'entered').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / 'entered').exists()
        os.kill(child.pid, signal.SIGSTOP if interruption == 'suspend' else signal.SIGKILL)
        time.sleep(.6)  # exceeds the actual persisted lease while renewal cannot run
        if interruption == 'suspend':
            (tmp_path / 'release').touch()
            os.kill(child.pid, signal.SIGCONT)
        child.wait(timeout=5)
        if interruption == 'suspend':
            assert (tmp_path / 'lease-lost').exists()
        expired = store.load('task')
        assert expired['status'] == 'EXECUTING'
        assert expired['attempt'] == 1
        assert expired['checkpoint'] == checkpoint
        assert 'result' not in expired
        recovered = recover_after_restart(expired)
        assert recovered['status'] == 'READY'
        assert recovered['checkpoint'] == checkpoint
        assert recovered['attempt'] == 1
        assert store.compare_and_save(expired, recovered)
        claimed = dict(recovered, status='CLAIMED')
        assert store.compare_and_save(recovered, claimed)

        class Worker:
            def execute(self, handoff):
                assert handoff['completed_work'] == ['retained']
                return WorkerResult('fresh proposal requiring validation')

        result = execute_claimed_task(store, claimed, lambda: [ProviderHealth('fixture')],
            {'fixture': Worker}, audit_path=tmp_path / 'audit.jsonl',
            heartbeat_seconds=.05, lease_seconds=.3)
        assert result['status'] == 'VALIDATING'
        assert result['attempt'] == 2
        assert result['result'] == 'fresh proposal requiring validation'
        assert result['checkpoint']['completed_work'] == ['retained']
        events = [json.loads(line)['event'] for line in (tmp_path / 'audit.jsonl').read_text().splitlines()]
        assert events.count('execution_awaiting_validation') == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
