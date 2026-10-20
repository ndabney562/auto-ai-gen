"""Bounded phase diagnostics; raw subprocess output is never persisted."""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCEPTIONS = frozenset({
    'AssertionError', 'AttributeError', 'BrokenPipeError', 'CalledProcessError',
    'CliError', 'FileNotFoundError', 'ImportError', 'KeyError', 'LeaseLost',
    'ModuleNotFoundError', 'OSError', 'PermissionError', 'RuntimeError',
    'ServiceAlreadyRunning', 'SyntaxError', 'TimeoutError', 'TimeoutExpired',
    'TypeError', 'ValueError',
})
FRAME = re.compile(r'  File "([^"\n]+)", line ([0-9]{1,7}), in ([A-Za-z_][A-Za-z_0-9]{0,79}|<module>)\Z')
TASK_ID = re.compile(r'[0-9a-f]{32}\Z')
TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00\Z')
FAILURE_OUTCOMES = frozenset({
    'nonzero_exit', 'timeout', 'stopped', 'launch_error', 'coordinator_error',
    'interrupted_before_recording_exit', 'success',
})


class TraceMetadata:
    """Drain arbitrary stderr with bounded memory, retaining only allowlisted metadata."""

    def __init__(self):
        self.frames = []
        self.exception_type = None
        self.pending = b''
        self.discarding = False
        self.bytes_seen = 0
        self.in_traceback = False

    def feed(self, chunk):
        self.bytes_seen += len(chunk)
        parts = chunk.split(b'\n')
        for index, part in enumerate(parts):
            if not self.discarding:
                self.pending += part
                if len(self.pending) > 1024:
                    self.pending = b''
                    self.discarding = True
            if index < len(parts) - 1:
                if not self.discarding:
                    self.line(self.pending.decode('utf-8', errors='replace').rstrip('\r'))
                self.pending = b''
                self.discarding = False

    def line(self, line):
        if line == 'Traceback (most recent call last):':
            self.frames = []
            self.exception_type = None
            self.in_traceback = True
            return
        if not self.in_traceback:
            return
        match = FRAME.fullmatch(line)
        if match:
            file, number, _function = match.groups()
            try:
                path = Path(file).resolve()
            except (OSError, RuntimeError, ValueError):
                return
            if (path.parent in {ROOT / 'orchestrator', ROOT / 'providers'}
                    and path.is_file() and path.suffix == '.py'):
                self.frames = [*self.frames[-7:], {'file': str(path.relative_to(ROOT)),
                    'line': int(number)}]
        elif line and not line[0].isspace():
            kind = line.split(':', 1)[0].rsplit('.', 1)[-1]
            if kind in EXCEPTIONS:
                self.exception_type = kind
            self.in_traceback = False

    def finish(self):
        if self.pending and not self.discarding:
            self.line(self.pending.decode('utf-8', errors='replace'))
        return {'exception_type': self.exception_type, 'frames': self.frames,
                'stderr_bytes': self.bytes_seen}


def load_diagnostic(store, name):
    try:
        value = store.load(name)
        return value if isinstance(value, dict) and isinstance(value.get('outcome'), str) else None
    except (OSError, ValueError, TypeError):
        return None


def save_diagnostic(store, name, value):
    try:
        store.save(name, value)
    except (OSError, ValueError, TypeError):
        logging.getLogger(__name__).warning('Phase diagnostic could not be saved')


def task_failure_name(task_id):
    """One deterministic, store-safe slot for each generated task ID."""
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        return None
    return 'last-phase-failure-' + task_id


def sanitized_task_failure(value):
    """Project even recovered records onto bounded, allowlisted metadata."""
    if not isinstance(value, dict):
        return None
    name = task_failure_name(value.get('task_id'))
    outcome = value.get('outcome')
    if name is None or not isinstance(outcome, str) or outcome not in FAILURE_OUTCOMES:
        return None
    result = {'id': name, 'task_id': value['task_id'], 'outcome': outcome,
              'operation': None}
    if value.get('operation') in ('step', 'publish'):
        result['operation'] = value['operation']
    for key in ('started_at', 'ended_at'):
        item = value.get(key)
        if isinstance(item, str) and TIMESTAMP.fullmatch(item):
            result[key] = item
    for key in ('wall_elapsed_seconds', 'monotonic_elapsed_seconds'):
        item = value.get(key)
        if type(item) in (int, float):
            try:
                finite = math.isfinite(item)
            except OverflowError:
                finite = False
            if finite and (key == 'wall_elapsed_seconds' or item >= 0):
                result[key] = item
    if 'exit_code' in value:
        item = value['exit_code']
        if item is None or (type(item) is int and -(2 ** 31) <= item < 2 ** 31):
            result['exit_code'] = item
    if type(value.get('stderr_complete')) is bool:
        result['stderr_complete'] = value['stderr_complete']
    item = value.get('stderr_bytes')
    if type(item) is int and 0 <= item < 2 ** 63:
        result['stderr_bytes'] = item
    for key in ('exception_type', 'cleanup_error'):
        if key in value:
            item = value[key]
            result[key] = item if isinstance(item, str) and item in EXCEPTIONS else None
    if 'frames' in value:
        frames = value['frames']
        result['frames'] = []
        if isinstance(frames, list):
            for frame in frames[-8:]:
                if not isinstance(frame, dict):
                    continue
                file, line = frame.get('file'), frame.get('line')
                if (isinstance(file, str)
                        and re.fullmatch(r'(orchestrator|providers)/[A-Za-z_][A-Za-z_0-9]*\.py', file)
                        and (ROOT / file).is_file()
                        and type(line) is int and 0 <= line <= 9999999):
                    result['frames'].append({'file': file, 'line': line})
    if outcome == 'success' and not result.get('cleanup_error'):
        return None
    return result


def save_task_failure(store, value):
    record = sanitized_task_failure(value)
    if record is not None:
        save_diagnostic(store, record['id'], record)


def load_task_failure(store, task_id):
    name = task_failure_name(task_id)
    if name is None:
        return None
    value = load_diagnostic(store, name)
    record = sanitized_task_failure(value)
    # Reject wrong-task, malformed, or unexpectedly extended records in summaries.
    if record is None or record['task_id'] != task_id or record != value:
        return None
    return record
