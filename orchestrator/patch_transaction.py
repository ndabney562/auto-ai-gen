"""Recoverable patch application for a single trusted coordinator workspace.

Each replacement is atomic; a patchset is rolled back on failure or on the
next recovery after a crash. Readers must use the workspace lock to avoid
observing intermediate files. This is not a sandbox against hostile processes.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from orchestrator.engineering import safe_target


def _replace(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".patch-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(name, path.stat().st_mode & 0o777)
        os.replace(name, path)
        _sync(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def _sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _locked(root: Path):
    with (root / ".agent-patch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _recover(root: Path) -> None:
    journal = root / ".agent-patch.json"
    if not journal.exists():
        return
    payload = json.loads(journal.read_text())
    # Keep the journal if rollback fails; never report successful recovery.
    for name, content in payload.items():
        target = safe_target(root, name)
        if content is None:
            target.unlink(missing_ok=True)
            _sync(target.parent)
        else:
            _replace(target, content)
    journal.unlink()
    _sync(root)


def recover_patch(root: Path) -> None:
    with _locked(root):
        _recover(root)


def apply_patchset(root: Path, originals: dict[str, str], replacements: dict[str, str]) -> None:
    with _locked(root):
        _recover(root)
        targets = {name: safe_target(root, name) for name in replacements}
        if len(set(targets.values())) != len(targets):
            raise ValueError("Target paths must be unique")
        before = {}
        for name, target in targets.items():
            content = target.read_text(encoding="utf-8") if target.exists() else None
            if (content or "") != originals[name]:
                raise ValueError("Target changed during review")
            before[name] = content
        journal = root / ".agent-patch.json"
        _replace(journal, json.dumps(before))
        try:
            for name, target in targets.items():
                safe_target(root, name)
                _replace(target, replacements[name])
        except BaseException:
            _recover(root)
            raise
        journal.unlink()
        _sync(root)
