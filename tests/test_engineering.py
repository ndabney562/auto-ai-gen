import json

import pytest

from orchestrator.engineering import engineer_one_file, safe_target


def test_claude_change_requires_explicit_independent_review(tmp_path) -> None:
    target = tmp_path / "docs" / "sample.md"
    proposal = json.dumps({"content": "# Claude contribution\n", "summary": "Added a document"})
    review = json.dumps({"approved": True, "reason": "Focused documentation change"})
    result = engineer_one_file(
        tmp_path,
        "docs/sample.md",
        "Write a sample project document",
        lambda prompt: proposal,
        lambda prompt: review,
    )
    assert target.read_text() == "# Claude contribution\n"
    assert result["review"] == "Focused documentation change"


def test_rejected_review_does_not_write_file(tmp_path) -> None:
    proposal = json.dumps({"content": "unsafe", "summary": "Attempted"})
    review = json.dumps({"approved": False, "reason": "Not related"})
    with pytest.raises(ValueError, match="did not approve"):
        engineer_one_file(
            tmp_path,
            "docs/sample.md",
            "Write a sample project document",
            lambda prompt: proposal,
            lambda prompt: review,
        )
    assert not (tmp_path / "docs/sample.md").exists()


@pytest.mark.parametrize(
    "name",
    [
        "../README.md",
        ".github/workflows/ci.yml",
        "/tmp/out.py",
        "docs/secrets.env",
        "docs/../../etc/passwd",
        "orchestrator/escape.txt",
        "tests/malicious.py/other.py",
    ],
)
def test_rejects_unauthorized_paths(tmp_path, name) -> None:
    with pytest.raises(ValueError):
        safe_target(tmp_path, name)


def test_rejects_model_json_that_is_not_object(tmp_path) -> None:
    with pytest.raises(TypeError, match="one JSON object"):
        engineer_one_file(
            tmp_path,
            "docs/sample.md",
            "Write a sample project document",
            lambda prompt: "[]",
            lambda prompt: '{"approved":true,"reason":"ok"}',
        )


def test_reviewer_must_approve_as_boolean(tmp_path) -> None:
    proposal = json.dumps({"content": "# New\n", "summary": "New"})
    with pytest.raises(ValueError, match="did not approve"):
        engineer_one_file(
            tmp_path,
            "docs/sample.md",
            "Write a sample project document",
            lambda prompt: proposal,
            lambda prompt: '{"approved":"true","reason":"looks good"}',
        )


def test_symlink_outside_workspace_is_rejected(tmp_path) -> None:
    external = tmp_path.parent / "outside-stage11.py"
    link = tmp_path / "tests"
    link.symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        safe_target(tmp_path, "tests/outside-stage11.py")
    assert not external.exists()


def test_internal_symlink_cannot_write_workflow(tmp_path):
    workflow = tmp_path / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("trusted")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/alias.py").symlink_to(workflow)
    with pytest.raises(ValueError, match="symlink"):
        safe_target(tmp_path, "docs/alias.py")
    assert workflow.read_text() == "trusted"


def test_rejects_directory_alias(tmp_path):
    (tmp_path / "providers").mkdir()
    (tmp_path / "docs").symlink_to(tmp_path / "providers", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        safe_target(tmp_path, "docs/new.py")


def test_review_cannot_overwrite_concurrent_edit(tmp_path):
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs/a.md"
    target.write_text("original")

    def review(prompt):
        target.write_text("concurrent work")
        return '{"approved":true,"reason":"ok"}'

    with pytest.raises(ValueError, match="changed during review"):
        engineer_one_file(tmp_path, "docs/a.md", "Change A",
                          lambda p: '{"content":"proposal","summary":"edit"}', review)
    assert target.read_text() == "concurrent work"
