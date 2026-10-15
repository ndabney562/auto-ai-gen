from __future__ import annotations

from enum import StrEnum


class TaskState(StrEnum):
    NEW = "NEW"
    PLANNING = "PLANNING"
    READY = "READY"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    VALIDATING = "VALIDATING"
    REVIEW = "REVIEW"
    REPAIR = "REPAIR"
    WAIT_PROVIDER = "WAIT_PROVIDER"
    WAIT_RETRY = "WAIT_RETRY"
    WAIT_DEPENDENCY = "WAIT_DEPENDENCY"
    WAIT_CI = "WAIT_CI"
    WAIT_HUMAN = "WAIT_HUMAN"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"
    FAILED_PERMANENT = "FAILED_PERMANENT"


TERMINAL_STATES = {
    TaskState.DONE,
    TaskState.CANCELLED,
    TaskState.FAILED_PERMANENT,
}

ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.NEW: {TaskState.PLANNING, TaskState.CANCELLED},
    TaskState.PLANNING: {TaskState.READY, TaskState.BLOCKED, TaskState.WAIT_HUMAN},
    TaskState.READY: {TaskState.CLAIMED, TaskState.WAIT_DEPENDENCY, TaskState.CANCELLED},
    TaskState.CLAIMED: {TaskState.EXECUTING, TaskState.WAIT_PROVIDER, TaskState.CANCELLED},
    TaskState.EXECUTING: {
        TaskState.VALIDATING,
        TaskState.WAIT_PROVIDER,
        TaskState.WAIT_RETRY,
        TaskState.WAIT_HUMAN,
        TaskState.BLOCKED,
    },
    TaskState.VALIDATING: {TaskState.REVIEW, TaskState.REPAIR, TaskState.WAIT_CI},
    TaskState.REVIEW: {TaskState.DONE, TaskState.REPAIR, TaskState.BLOCKED},
    TaskState.REPAIR: {TaskState.VALIDATING, TaskState.WAIT_PROVIDER, TaskState.WAIT_RETRY},
    TaskState.WAIT_PROVIDER: {TaskState.READY, TaskState.CLAIMED, TaskState.CANCELLED},
    TaskState.WAIT_RETRY: {TaskState.READY, TaskState.CLAIMED, TaskState.CANCELLED},
    TaskState.WAIT_DEPENDENCY: {TaskState.READY, TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.WAIT_CI: {TaskState.REVIEW, TaskState.REPAIR, TaskState.BLOCKED},
    TaskState.WAIT_HUMAN: {TaskState.READY, TaskState.CANCELLED, TaskState.BLOCKED},
    TaskState.BLOCKED: {TaskState.READY, TaskState.CANCELLED, TaskState.FAILED_PERMANENT},
    TaskState.DONE: set(),
    TaskState.CANCELLED: set(),
    TaskState.FAILED_PERMANENT: set(),
}


def can_transition(current: TaskState, target: TaskState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def transition(current: TaskState, target: TaskState) -> TaskState:
    if not can_transition(current, target):
        raise ValueError(f"Invalid task transition: {current} -> {target}")
    return target
