import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/merge-verified-stages.yml"


def test_verified_merge_has_strict_triggers_and_permissions() -> None:
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    trigger = config.get("on", config.get(True))
    assert trigger["workflow_run"]["workflows"] == ["CI"]
    assert trigger["workflow_run"]["types"] == ["completed"]
    assert config["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert config["jobs"]["merge-verified"]["steps"][0]["uses"] == "actions/github-script@v8"


def test_verified_merge_script_is_valid_javascript() -> None:
    if shutil.which("node") is None:
        pytest.skip("Node.js unavailable in local development environment")
    config = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    script = config["jobs"]["merge-verified"]["steps"][0]["with"]["script"]
    wrapped = "async function validate() {\n" + script + "\n}\n"
    result = subprocess.run(
        ["node", "--check"],
        input=wrapped,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
