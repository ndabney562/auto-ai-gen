import json
import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orchestrator import supervisor as s
from orchestrator.store import JsonTaskStore

IMAGE = 'sha256:' + 'a' * 64


def setup(tmp_path, status='READY'):
    JsonTaskStore(tmp_path / 'tasks').save('task', {
        'id': 'task', 'status': status, 'checkpoint': {'completed_work': ['retained']},
        'next_action': {'type': 'implement'}})
    s.configure(tmp_path, 'task', 'owner/repo', IMAGE, interval=5)
    return s.records(tmp_path).load('config')


def test_config_requires_immutable_image_and_existing_task(tmp_path):
    setup(tmp_path)
    with pytest.raises(ValueError, match='immutable'):
        s.configure(tmp_path, 'task', 'owner/repo', 'mutable:latest')
    with pytest.raises(FileNotFoundError):
        s.configure(tmp_path, 'absent', 'owner/repo', IMAGE)


def test_service_lock_blocks_duplicate_service_and_reconfiguration(tmp_path):
    setup(tmp_path)
    with s.service_lock(tmp_path):
        assert s.running(tmp_path)
        with pytest.raises(RuntimeError):
            s.configure(tmp_path, 'task', 'owner/repo', IMAGE)
    assert not s.running(tmp_path)


@pytest.mark.parametrize('status', sorted(s.PAUSED))
def test_paused_and_terminal_tasks_never_launch(tmp_path, status):
    config = setup(tmp_path, status)
    s.tick(tmp_path, config, lambda: False,
           phase=lambda *_: pytest.fail('unexpected external request'))


def test_publish_is_selected_and_merging_opt_in(tmp_path):
    config = setup(tmp_path, 'WAIT_CI')
    calls = []
    s.tick(tmp_path, config, lambda: False, phase=lambda *args: calls.append(args[1]) or 0)
    assert 'publish' in calls[0]
    assert '--merge-verified' not in calls[0]
    config['merge'] = True
    assert '--merge-verified' in s.phase_command(tmp_path, config, {'status': 'WAIT_CI'})


def test_failures_back_off_and_remain_bounded_across_restart(tmp_path):
    config = setup(tmp_path)
    calls = []
    for now in (100, 101, 200, 400, 1000):
        s.tick(tmp_path, config, lambda: False,
               phase=lambda *_: calls.append(1) or 1, now=lambda now=now: now)
    assert len(calls) == 3
    assert s.records(tmp_path).load('health')['failures'] == 3
    s.tick(tmp_path, config, lambda: False, phase=lambda *_: pytest.fail('reset retry budget'),
           now=lambda: 99999)


def test_provider_retry_time_and_success_reset(tmp_path):
    config = setup(tmp_path, 'WAIT_RETRY')
    store = JsonTaskStore(tmp_path / 'tasks')
    task = store.load('task')
    task['retry_at'] = '2999-01-01T00:00:00+00:00'
    store.save('task', task)
    s.tick(tmp_path, config, lambda: False, phase=lambda *_: pytest.fail('too early'))
    del task['retry_at']
    store.save('task', task)
    s.records(tmp_path).save('health', {'id': 'health', 'failures': 2, 'retry_after': 0})
    s.tick(tmp_path, config, lambda: False, phase=lambda *_: 0)
    assert s.records(tmp_path).load('health')['failures'] == 0


@pytest.mark.parametrize('status', ['VALIDATING', 'REVIEW', 'REPAIR', 'WAIT_CI', 'EXECUTING'])
def test_cancel_preserves_checkpoint_and_prevents_phase(tmp_path, status):
    config = setup(tmp_path, status)
    s.request(tmp_path, 'cancel')
    s.tick(tmp_path, config, lambda: False, phase=lambda *_: pytest.fail('cancelled phase'))
    task = JsonTaskStore(tmp_path / 'tasks').load('task')
    assert task['status'] == 'CANCELLED'
    assert task['checkpoint']['completed_work'] == ['retained']


def test_cancel_during_phase_waits_for_cleanup(tmp_path):
    config = setup(tmp_path)

    def phase(*_):
        s.request(tmp_path, 'cancel')

    s.tick(tmp_path, config, lambda: False, phase=phase)
    assert JsonTaskStore(tmp_path / 'tasks').load('task')['status'] == 'CANCELLED'


def test_launch_agent_contains_no_credentials_and_uses_absolute_paths(tmp_path, monkeypatch):
    setup(tmp_path)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'do-not-copy')
    output = tmp_path / 'service.plist'
    s.launch_agent(tmp_path, output)
    data = plistlib.loads(output.read_bytes())
    assert data['KeepAlive'] == {'SuccessfulExit': False}
    assert Path(data['ProgramArguments'][0]).is_absolute()
    assert 'do-not-copy' not in output.read_text()


def test_background_start_stop_and_restart(tmp_path):
    setup(tmp_path, 'WAIT_HUMAN')
    try:
        s.start(tmp_path)
        assert s.status(tmp_path)['running']
        with pytest.raises(RuntimeError):
            s.start(tmp_path)
        s.request(tmp_path, 'stop')
        deadline = time.monotonic() + 10
        while s.running(tmp_path) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not s.running(tmp_path)
        assert s.status(tmp_path)['task_status'] == 'WAIT_HUMAN'
        s.start(tmp_path)
        assert s.running(tmp_path)
    finally:
        s.request(tmp_path, 'stop')
        deadline = time.monotonic() + 10
        while s.running(tmp_path) and time.monotonic() < deadline:
            time.sleep(.05)


def test_phase_timeout_kills_descendant(tmp_path):
    setup(tmp_path)
    marker = tmp_path / 'heartbeat'
    code = ('import time,pathlib; p=pathlib.Path(' + repr(str(marker)) + '); '
            '\nwhile True: p.write_text(str(time.time())); time.sleep(.02)')
    assert s.run_phase(tmp_path, [sys.executable, '-c', code], lambda: False, timeout=.5) == 124
    assert marker.exists()
    before = marker.read_text()
    time.sleep(.2)
    assert marker.read_text() == before


@pytest.mark.parametrize('sig', [signal.SIGTERM, signal.SIGKILL])
def test_service_death_stops_active_phase(tmp_path, sig):
    setup(tmp_path)
    marker = tmp_path / 'heartbeat'
    code = ('import time,pathlib; p=pathlib.Path(' + repr(str(marker)) + '); '
            '\nwhile True: p.write_text(str(time.time())); time.sleep(.02)')
    coordinator = ('from pathlib import Path; from orchestrator.supervisor import run_phase; '
                   'run_phase(Path(' + repr(str(tmp_path)) + '), ' +
                   repr([sys.executable, '-c', code]) + ', lambda: False)')
    parent = subprocess.Popen([sys.executable, '-c', coordinator])
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists()
        os.kill(parent.pid, sig)
        parent.wait(timeout=5)
        time.sleep(.3)
        before = marker.read_text()
        time.sleep(.2)
        assert marker.read_text() == before
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()


def test_status_does_not_expose_task_content(tmp_path):
    setup(tmp_path)
    assert 'completed_work' not in json.dumps(s.status(tmp_path))
