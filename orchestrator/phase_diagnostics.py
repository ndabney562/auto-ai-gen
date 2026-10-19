"""Bounded phase diagnostics; raw subprocess output is never persisted."""
from __future__ import annotations

import logging
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
            path = Path(file).resolve()
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
