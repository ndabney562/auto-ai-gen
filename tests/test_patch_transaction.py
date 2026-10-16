import json

import pytest

from orchestrator.patch_transaction import apply_patchset, recover_patch


def test_second_write_failure_rolls_back_existing_and_new_files(tmp_path, monkeypatch):
    from orchestrator import patch_transaction as transaction

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("before")
    original_replace = transaction._replace
    failed = False

    def replace(path, content):
        nonlocal failed
        if path.name == "b.md" and not failed:
            failed = True
            raise OSError("disk failure")
        original_replace(path, content)

    monkeypatch.setattr(transaction, "_replace", replace)
    with pytest.raises(OSError, match="disk failure"):
        apply_patchset(tmp_path, {"docs/a.md": "before", "docs/b.md": ""},
                       {"docs/a.md": "after", "docs/b.md": "new"})
    assert (tmp_path / "docs/a.md").read_text() == "before"
    assert not (tmp_path / "docs/b.md").exists()
    assert not (tmp_path / ".agent-patch.json").exists()


def test_crash_recovery_replays_durable_undo_journal(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("partial update")
    (tmp_path / "docs/b.md").write_text("created")
    (tmp_path / ".agent-patch.json").write_text(
        json.dumps({"docs/a.md": "original", "docs/b.md": None}))
    recover_patch(tmp_path)
    recover_patch(tmp_path)
    assert (tmp_path / "docs/a.md").read_text() == "original"
    assert not (tmp_path / "docs/b.md").exists()


def test_concurrent_source_edit_prevents_entire_patch(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("other worker")
    with pytest.raises(ValueError, match="changed during review"):
        apply_patchset(tmp_path, {"docs/a.md": "old", "docs/b.md": ""},
                       {"docs/a.md": "new", "docs/b.md": "new"})
    assert (tmp_path / "docs/a.md").read_text() == "other worker"
    assert not (tmp_path / "docs/b.md").exists()


def test_rollback_before_new_parent_created(tmp_path, monkeypatch):
    from orchestrator import patch_transaction as transaction

    original_replace = transaction._replace

    def replace(path, content):
        if path.name == "new.md":
            raise OSError("parent creation failed")
        return original_replace(path, content)

    monkeypatch.setattr(transaction, "_replace", replace)
    with pytest.raises(OSError, match="parent creation failed"):
        apply_patchset(tmp_path, {"docs/nested/new.md": ""}, {"docs/nested/new.md": "new"})
    assert not (tmp_path / ".agent-patch.json").exists()
