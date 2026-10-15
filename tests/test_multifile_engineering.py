import json

import pytest

from orchestrator.multifile_engineering import engineer_files


def test_approved_patchset_writes_all_files(tmp_path) -> None:
    proposal = json.dumps(
        {
            "files": [
                {"path": "docs/a.md", "content": "# A\n"},
                {"path": "docs/b.md", "content": "# B\n"},
            ],
            "summary": "Added paired docs",
        }
    )
    review = json.dumps({"approved": True, "reason": "Consistent patchset"})
    result = engineer_files(
        tmp_path,
        ["docs/a.md", "docs/b.md"],
        "Add paired docs",
        lambda prompt: proposal,
        lambda prompt: review,
    )
    assert (tmp_path / "docs/a.md").read_text() == "# A\n"
    assert (tmp_path / "docs/b.md").read_text() == "# B\n"
    assert result["review"] == "Consistent patchset"


def test_rejected_patchset_writes_nothing(tmp_path) -> None:
    proposal = json.dumps(
        {
            "files": [
                {"path": "docs/a.md", "content": "# A\n"},
                {"path": "docs/b.md", "content": "# B\n"},
            ],
            "summary": "Attempted",
        }
    )
    review = json.dumps({"approved": False, "reason": "Cross-file bug"})
    with pytest.raises(ValueError, match="did not approve"):
        engineer_files(
            tmp_path,
            ["docs/a.md", "docs/b.md"],
            "Add paired docs",
            lambda prompt: proposal,
            lambda prompt: review,
        )
    assert not (tmp_path / "docs/a.md").exists()
    assert not (tmp_path / "docs/b.md").exists()


def test_missing_requested_file_is_rejected_before_write(tmp_path) -> None:
    proposal = json.dumps(
        {
            "files": [{"path": "docs/a.md", "content": "# A\n"}],
            "summary": "Incomplete",
        }
    )
    with pytest.raises(ValueError, match="every requested path"):
        engineer_files(
            tmp_path,
            ["docs/a.md", "docs/b.md"],
            "Add paired docs",
            lambda prompt: proposal,
            lambda prompt: '{"approved":true,"reason":"ok"}',
        )
    assert not (tmp_path / "docs/a.md").exists()


def test_unrequested_file_is_rejected(tmp_path) -> None:
    proposal = json.dumps(
        {
            "files": [
                {"path": "docs/a.md", "content": "# A\n"},
                {"path": "docs/evil.md", "content": "bad\n"},
            ],
            "summary": "Expanded scope",
        }
    )
    with pytest.raises(ValueError, match="unexpected or duplicate"):
        engineer_files(
            tmp_path,
            ["docs/a.md"],
            "Write A",
            lambda prompt: proposal,
            lambda prompt: '{"approved":true,"reason":"ok"}',
        )
    assert not (tmp_path / "docs/a.md").exists()
    assert not (tmp_path / "docs/evil.md").exists()


def test_duplicate_targets_are_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="unique"):
        engineer_files(
            tmp_path,
            ["docs/a.md", "docs/a.md"],
            "Write A",
            lambda prompt: "{}",
            lambda prompt: "{}",
        )


def test_patchset_file_limit(tmp_path) -> None:
    with pytest.raises(ValueError, match="1 to 8"):
        engineer_files(
            tmp_path,
            [f"docs/{index}.md" for index in range(9)],
            "Too many files",
            lambda prompt: "{}",
            lambda prompt: "{}",
        )
