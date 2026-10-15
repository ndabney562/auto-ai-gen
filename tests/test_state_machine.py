import pytest

from orchestrator.state_machine import TaskState, can_transition, transition


def test_happy_path_transitions() -> None:
    path = [
        TaskState.NEW,
        TaskState.PLANNING,
        TaskState.READY,
        TaskState.CLAIMED,
        TaskState.EXECUTING,
        TaskState.VALIDATING,
        TaskState.REVIEW,
        TaskState.DONE,
    ]

    for current, target in zip(path, path[1:]):
        assert can_transition(current, target)
        assert transition(current, target) == target


def test_provider_failure_can_checkpoint_and_resume() -> None:
    assert can_transition(TaskState.EXECUTING, TaskState.WAIT_PROVIDER)
    assert can_transition(TaskState.WAIT_PROVIDER, TaskState.READY)


def test_terminal_state_cannot_restart() -> None:
    assert not can_transition(TaskState.DONE, TaskState.READY)
    with pytest.raises(ValueError):
        transition(TaskState.DONE, TaskState.READY)
