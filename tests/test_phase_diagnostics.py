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


def test_split_utf8_and_unknown_error_messages_are_discarded():
    trace = TraceMetadata()
    for chunk in (b'Value', b'Error: secret\n', b'SecretException: hidden\n'):
        trace.feed(chunk)
    assert trace.finish()['exception_type'] == 'ValueError'
    assert 'hidden' not in json.dumps(trace.finish())


def test_phase_failure_retains_metadata_after_success(tmp_path):
    command = [sys.executable, '-c', "raise ValueError('credential-secret')"]
    assert s.run_phase(tmp_path, command, lambda: False) == 1
    failure = s.records(tmp_path).load('last-phase-failure')
    assert failure['outcome'] == 'nonzero_exit'
    assert failure['exception_type'] == 'ValueError'
    assert failure['exit_code'] == 1
    assert failure['stderr_complete']
    assert 'credential-secret' not in json.dumps(failure)
    assert s.run_phase(tmp_path, [sys.executable, '-c', 'pass'], lambda: False) == 0
    assert s.records(tmp_path).load('last-phase-failure') == failure
    assert s.records(tmp_path).load('last-phase')['outcome'] == 'success'


def test_interrupted_record_survives_next_phase(tmp_path):
    s.records(tmp_path).save('last-phase', {'id': 'last-phase', 'outcome': 'running',
                                         'task_id': 'retained-task'})
    assert s.run_phase(tmp_path, [sys.executable, '-c', 'pass'], lambda: False) == 0
    assert s.records(tmp_path).load('last-phase-failure')['outcome'] == (
        'interrupted_before_recording_exit')


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
