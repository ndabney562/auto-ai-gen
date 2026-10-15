from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_WORKFLOW = ROOT / ".github/workflows/claude-engineering.yml"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
MERGE_WORKFLOW = ROOT / ".github/workflows/merge-verified-stages.yml"


def _trigger(workflow: dict) -> dict:
    # YAML 1.1 treats the GitHub 'on' key as boolean true.
    return workflow.get("on", workflow.get(True))


def test_claude_workflow_requires_manual_authorized_trigger() -> None:
    config = yaml.safe_load(CLAUDE_WORKFLOW.read_text(encoding="utf-8"))
    assert "workflow_dispatch" in _trigger(config)
    assert "pull_request" not in _trigger(config)
    assert config["permissions"] == {
        "actions": "write",
        "contents": "write",
        "pull-requests": "write",
    }
    steps = config["jobs"]["implement"]["steps"]
    assert any("Claude implements and OpenAI" in step.get("name", "") for step in steps)
    assert any("Validate entire repository" == step.get("name") for step in steps)


def test_bot_pr_is_explicitly_dispatched_to_ci() -> None:
    config = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert "workflow_dispatch" in _trigger(config)
    merge = yaml.safe_load(MERGE_WORKFLOW.read_text(encoding="utf-8"))
    script = merge["jobs"]["merge-verified"]["steps"][0]["with"]["script"]
    assert "botClaudeStage" in script
    assert "run.head_sha" in script
    assert "pr.user?.login === 'github-actions[bot]'" in script
    assert "passed('Lint')" in script
    assert "passed('Test')" in script
