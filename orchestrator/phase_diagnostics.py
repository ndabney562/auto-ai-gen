"""Bounded phase diagnostics; raw subprocess output is never persisted."""
from __future__ import annotations

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

    def feed(self, chunk):
        self.bytes_seen += len(chunk)
        for part in chunk.splitlines(keepends=True):
            complete = part.endswith(b'\n')
            if not self.discarding:
                self.pending += part
                if len(self.pending) > 1024:
                    self.pending = b''
                    self.discarding = True
            if complete:
                if not self.discarding:
                    self.line(self.pending.decode('utf-8', errors='replace').rstrip('\r\n'))
                self.pending = b''
                self.discarding = False

    def line(self, line):
        kind = line.split(':', 1)[0]
        if kind in EXCEPTIONS:
            self.exception_type = kind
        match = FRAME.fullmatch(line)
        if match:
            file, number, _function = match.groups()
            # Only real, trusted module files; arbitrary stderr paths are not records.
            path = Path(file)
            if (path.is_absolute() and path.parent in {ROOT / 'orchestrator', ROOT / 'providers'}
                    and path.is_file() and path.suffix == '.py'):
                self.frames = [*self.frames[-7:], {'file': str(path.relative_to(ROOT)),
                    'line': int(number)}]

    def finish(self):
        if self.pending and not self.discarding:
            self.line(self.pending.decode('utf-8', errors='replace'))
        return {'exception_type': self.exception_type, 'frames': self.frames,
                'stderr_bytes': self.bytes_seen}
