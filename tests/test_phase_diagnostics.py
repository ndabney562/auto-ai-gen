import json
import sys

import pytest

from orchestrator import supervisor as s
from orchestrator.phase_diagnostics import ROOT, TraceMetadata


def test_metadata_is_bounded_and_retains_only_trusted_locations():
    trace = TraceMetadata()
    for _ in range(2000):
        trace.feed(b'secret-token' * 1000)
        assert len(trace.pending) <= 1024
    trace.feed(b'\n')
    trace.feed(b'Traceback (most recent call last):\n')
    source = ROOT / 'orchestrator' / 'executor.py'
    for _ in range(20):
        trace.feed(f'  File "{source}", line 123, in execute_claimed_task\n'.encode())
    trace.feed(b'  File "/private/secret.py", line 5, in secret\n')
    trace.feed(b'LeaseLost: password=secret-token\n')
    result = trace.finish()
    assert result['exception_type'] == 'LeaseLost'
    assert result['frames'] == [{'file': 'orchestrator/executor.py', 'line': 123}] * 8
    assert 'secret' not in json.dumps(result)
    assert result['stderr_bytes'] > 1000000


def test_split_tokens_and_unknown_error_messages_are_discarded():
    trace = TraceMetadata()
    trace.feed(b'Traceback (most recent call last):\n')
    for chunk in (b'Value', b'Error: secret\n', b'SecretException: hidden\n'):
        trace.feed(chunk)
    assert trace.finish()['exception_type'] == 'ValueError'
    assert 'hidden' not in json.dumps(trace.finish())


def test_phase_failure_retains_metadata_after_success(tmp_path):
    command = [sys.executable, '-c', "raise ValueError('credential-secret')"]
    assert s.run_phase(tmp_path, command, lambda: False) == 1
    failure = s.records(tmp_path).load('last-phase-failure')
    assert failure['outcome'] == 'nonzero_exit'
    assert failure['operation'] is None
    assert failure['task_id'] is None
    assert failure['exception_type'] == 'ValueError'
    assert failure['exit_code'] == 1
    assert failure['stderr_complete']
    assert 'credential-secret' not in json.dumps(failure)
    assert s.run_phase(tmp_path, [sys.executable, '-c', 'pass'], lambda: False) == 0
    assert s.records(tmp_path).load('last-phase-failure') == failure
    assert s.records(tmp_path).load('last-phase')['outcome'] == 'success'
    assert s.records(tmp_path).load('last-phase')['operation'] is None


def test_interrupted_record_survives_next_phase(tmp_path):
    s.records(tmp_path).save('last-phase', {'id': 'last-phase', 'outcome': 'running',
                                         'task_id': 'retained-task'})
    assert s.run_phase(tmp_path, [sys.executable, '-c', 'pass'], lambda: False) == 0
    failure = s.records(tmp_path).load('last-phase-failure')
    assert failure['outcome'] == 'interrupted_before_recording_exit'
    assert failure['operation'] is None


@pytest.mark.parametrize('stop,timeout,outcome,code', [
    (True, 5, 'stopped', None), (False, .1, 'timeout', 124)])
def test_controlled_interruption_records_reason(tmp_path, stop, timeout, outcome, code):
    assert s.run_phase(tmp_path, [sys.executable, '-c', 'import time; time.sleep(30)'],
                       lambda: stop, timeout=timeout) == code
    record = s.records(tmp_path).load('last-phase-failure')
    assert record['outcome'] == outcome
    assert record['exit_code'] == code
    assert record['wall_elapsed_seconds'] >= 0


def test_launch_failure_is_recorded(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError('private-path-secret')
    monkeypatch.setattr(s.subprocess, 'Popen', denied)
    with pytest.raises(PermissionError):
        s.run_phase(tmp_path, ['unused'], lambda: False)
    record = s.records(tmp_path).load('last-phase-failure')
    assert record['outcome'] == 'launch_error'
    assert record['exception_type'] == 'PermissionError'
    assert 'private-path-secret' not in json.dumps(record)


@pytest.mark.parametrize('exception', ['LeaseLost', 'CalledProcessError'])
def test_real_qualified_traceback(tmp_path, exception):
    code = ("from orchestrator.executor import LeaseLost; raise LeaseLost('private')"
            if exception == 'LeaseLost' else
            "import subprocess; subprocess.run(['false'],check=True)")
    assert s.run_phase(tmp_path, [sys.executable, '-c', code], lambda: False) == 1
    assert s.records(tmp_path).load('last-phase-failure')['exception_type'] == exception


def test_trailing_logs_do_not_replace_traceback():
    trace = TraceMetadata()
    trace.feed(b'Traceback (most recent call last):\nValueError: private\nKeyError: log\n')
    assert trace.finish()['exception_type'] == 'ValueError'
    trace.feed(b'Traceback (most recent call last):\nRuntimeError: new\n')
    assert trace.finish()['exception_type'] == 'RuntimeError'


def test_large_subprocess_stderr_is_drained(tmp_path):
    code = "import sys; sys.stderr.write('private'*200000+'\\n'); raise ValueError('private')"
    assert s.run_phase(tmp_path, [sys.executable, '-c', code], lambda: False, timeout=5) == 1
    result = s.records(tmp_path).load('last-phase-failure')
    assert result['stderr_bytes'] > 1000000
    assert result['stderr_complete']
    assert result['exception_type'] == 'ValueError'
    assert 'private' not in json.dumps(result)


def test_corrupt_diagnostics_do_not_break_phases_or_status(tmp_path):
    from tests.test_supervisor import setup
    setup(tmp_path)
    for name in ('last-phase', 'last-phase-failure'):
        (tmp_path / 'supervisor' / f'{name}.json').write_text('{broken')
    assert s.status(tmp_path)['task_status'] == 'READY'
    assert s.run_phase(tmp_path, [sys.executable, '-c', 'pass'], lambda: False) == 0
    assert s.status(tmp_path)['last_phase']['outcome'] == 'success'


def test_diagnostic_save_failure_does_not_change_result(tmp_path, monkeypatch):
    def fail(*args):
        raise PermissionError('private')
    monkeypatch.setattr(s.JsonTaskStore, 'save', fail)
    assert s.run_phase(tmp_path, [sys.executable, '-c', 'pass'], lambda: False) == 0


def test_live_phase_cleanup_permission_error_is_recorded(tmp_path, monkeypatch):
    original = s.terminate
    def failed_cleanup(child):
        original(child)
        raise PermissionError('private')
    monkeypatch.setattr(s, 'terminate', failed_cleanup)
    assert s.run_phase(tmp_path, [sys.executable, '-c', 'pass'], lambda: False) == 0
    record = s.records(tmp_path).load('last-phase-failure')
    assert record['cleanup_error'] == 'PermissionError'


def test_detached_pipe_holder_does_not_leak_reader_or_fd(tmp_path):
    import os
    import signal
    import threading
    code = ("import subprocess,sys,pathlib; p=subprocess.Popen([sys.executable,'-c',"
            "'import time; time.sleep(30)'],start_new_session=True); "
            f"pathlib.Path({str(tmp_path / 'pid')!r}).write_text(str(p.pid))")
    before = {thread.ident for thread in threading.enumerate()}
    before_fds = len(os.listdir('/dev/fd'))
    try:
        assert s.run_phase(tmp_path, [sys.executable, '-c', code], lambda: False) == 0
        assert not s.records(tmp_path).load('last-phase')['stderr_complete']
        assert {thread.ident for thread in threading.enumerate()} == before
        assert len(os.listdir('/dev/fd')) == before_fds
    finally:
        pid = int((tmp_path / 'pid').read_text())
        os.kill(pid, signal.SIGKILL)


def test_runner_task_id_and_trusted_frame_are_visible_in_status(tmp_path):
    from tests.test_supervisor import setup
    setup(tmp_path)
    task_id = 'a' * 32
    s.JsonTaskStore(tmp_path / 'tasks').save(task_id, {'id': task_id, 'status': 'READY',
                                                    'checkpoint': {}})
    command = [sys.executable, '-m', 'orchestrator.subscription_runner', '--state',
               str(tmp_path), 'step', task_id]
    assert s.run_phase(tmp_path, command, lambda: False) == 1
    status = s.status(tmp_path)
    record = status['last_phase_failure']
    assert record['task_id'] == task_id
    assert record['operation'] == 'step'
    assert record['exception_type'] == 'KeyError'
    assert any(frame['file'] == 'orchestrator/subscription_runner.py' for frame in record['frames'])


@pytest.mark.parametrize('operation,task_status', [('step', 'READY'), ('publish', 'WAIT_CI')])
def test_generated_phase_operation_is_saved_before_and_after_launch(
        tmp_path, monkeypatch, operation, task_status):
    task_id = 'a' * 32
    config = {'task_id': task_id, 'repository': 'private/repository-secret',
              'image': 'sha256:' + 'b' * 64, 'merge': True}
    command = s.phase_command(tmp_path, config, {'status': task_status})

    def denied(argv, **kwargs):
        running = s.records(tmp_path).load('last-phase')
        assert running['outcome'] == 'running'
        assert running['operation'] == operation
        assert running['task_id'] == task_id
        assert argv[-len(command):] == command
        raise PermissionError('raw-stderr-secret')

    monkeypatch.setattr(s.subprocess, 'Popen', denied)
    with pytest.raises(PermissionError):
        s.run_phase(tmp_path, command, lambda: False)
    for name in ('last-phase', 'last-phase-failure'):
        record = s.records(tmp_path).load(name)
        assert record['operation'] == operation
        assert record['task_id'] == task_id
        encoded = json.dumps(record)
        for private in (str(tmp_path), config['repository'], config['image'],
                        sys.executable, 'subscription_runner', 'raw-stderr-secret'):
            assert private not in encoded
        assert 'argv' not in record
        assert 'command' not in record
        assert 'stderr' not in record


@pytest.mark.parametrize('case', [
    'empty', 'short', 'unrelated', 'executable', 'module', 'flag', 'state',
    'operation', 'task', 'uppercase_task', 'trailing_task', 'nonstring_task',
    'nonstring_operation', 'command_string',
])
def test_untrusted_or_malformed_command_has_no_runner_metadata(tmp_path, monkeypatch, case):
    command = [sys.executable, '-m', 'orchestrator.subscription_runner', '--state',
               str(tmp_path), 'publish', 'a' * 32]
    if case == 'empty':
        command = []
    elif case == 'short':
        command = command[:6]
    elif case == 'unrelated':
        command = [sys.executable, '-c', "print('step publish private-secret')"]
    elif case == 'command_string':
        command = ' '.join(command)
    else:
        index, value = {
            'executable': (0, '/private/python-secret'),
            'module': (2, 'private.module'),
            'flag': (3, '--private-secret'),
            'state': (4, str(tmp_path / 'other-secret')),
            'operation': (5, 'create'),
            'task': (6, 'private-task-secret'),
            'uppercase_task': (6, 'A' * 32),
            'trailing_task': (6, 'a' * 32 + '\n'),
            'nonstring_task': (6, None),
            'nonstring_operation': (5, []),
        }[case]
        command[index] = value

    def denied(*args, **kwargs):
        raise PermissionError('private-secret')

    monkeypatch.setattr(s.subprocess, 'Popen', denied)
    with pytest.raises(PermissionError):
        s.run_phase(tmp_path, command, lambda: False)
    for name in ('last-phase', 'last-phase-failure'):
        record = s.records(tmp_path).load(name)
        assert record['operation'] is None
        assert record['task_id'] is None
        assert 'private' not in json.dumps(record)


@pytest.mark.parametrize('operation', ['step', 'publish'])
def test_interrupted_phase_retains_original_operation(tmp_path, operation):
    s.records(tmp_path).save('last-phase', {
        'id': 'last-phase', 'outcome': 'running', 'task_id': 'a' * 32,
        'operation': operation})
    assert s.run_phase(tmp_path, [sys.executable, '-c', 'pass'], lambda: False) == 0
    failure = s.records(tmp_path).load('last-phase-failure')
    assert failure['outcome'] == 'interrupted_before_recording_exit'
    assert failure['operation'] == operation
    assert failure['task_id'] == 'a' * 32
    assert s.records(tmp_path).load('last-phase')['operation'] is None


def test_cleanup_signal_failure_does_not_hide_timeout(tmp_path, monkeypatch):
    import os
    import signal
    original_signal = os.killpg
    original_spawn = s.subprocess.Popen
    children = []
    def spawn(*args, **kwargs):
        child = original_spawn(*args, **kwargs)
        children.append(child)
        return child
    def denied(*args):
        raise PermissionError('private')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(s.subprocess, 'Popen', spawn)
            patch.setattr(s.os, 'killpg', denied)
            assert s.run_phase(tmp_path, [sys.executable, '-c', 'import time; time.sleep(30)'],
                               lambda: False, timeout=.1) == 124
        record = s.records(tmp_path).load('last-phase-failure')
        assert record['outcome'] == 'timeout'
        assert record['cleanup_error'] == 'PermissionError'
    finally:
        for child in children:
            if child.poll() is None:
                original_signal(child.pid, signal.SIGKILL)
                child.wait(timeout=5)


def test_invalid_traceback_path_does_not_interrupt_phase():
    trace = TraceMetadata()
    trace.feed(b'Traceback (most recent call last):\n')
    trace.feed(b'  File "/invalid\x00path.py", line 5, in ignored\n')
    trace.feed(b'ValueError: private\n')
    assert trace.finish()['exception_type'] == 'ValueError'
    assert trace.finish()['frames'] == []
