import os


def test_ci_repair_pilot():
    assert os.environ.get("GITHUB_ACTIONS") != "true", "INTENTIONAL_CI_REPAIR_PILOT_FAILURE"
