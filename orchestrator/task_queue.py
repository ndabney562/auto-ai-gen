"""Explicit, fsynced queue membership; task records remain the execution authority."""
from __future__ import annotations

import fcntl
import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.store import JsonTaskStore

PAUSED = {'DONE', 'CANCELLED', 'FAILED_PERMANENT', 'WAIT_HUMAN', 'BLOCKED'}


@contextmanager
def locked_queue(state: Path):
    store = JsonTaskStore(state / 'supervisor')
    with (state / 'supervisor' / '.queue.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            queue = store.load('queue')
        except FileNotFoundError:
            queue = {'id': 'queue', 'jobs': [], 'cursor': 0, 'active_task': None}
        yield queue
        store.save('queue', queue)


def cancel_path(state, task_id):
    digest = hashlib.sha256(task_id.encode()).hexdigest()
    return state / 'supervisor' / ('cancel-' + digest)


def enqueue(state, task_id, dependencies=()):
    JsonTaskStore(state / 'tasks').load(task_id)
    dependencies = list(dict.fromkeys(dependencies))
    with locked_queue(state) as queue:
        existing = next((j for j in queue['jobs'] if j['task_id'] == task_id), None)
        if existing:
            if existing['dependencies'] != dependencies:
                raise ValueError('An enrolled task cannot change dependencies')
            return
        members = {j['task_id'] for j in queue['jobs']}
        if any(dep not in members for dep in dependencies) or task_id in dependencies:
            raise ValueError('Dependencies must already be enrolled; cycles are forbidden')
        if len(queue['jobs']) >= 1000:
            raise ValueError('Queue capacity reached')
        queue['jobs'].append({'task_id': task_id, 'dependencies': dependencies})


def health(state, task_id):
    store = JsonTaskStore(state / 'supervisor')
    key = 'health-' + hashlib.sha256(task_id.encode()).hexdigest()
    try:
        return store.load(key)
    except FileNotFoundError:
        return {'id': key, 'failures': 0, 'retry_after': 0}


def select_task(state, *, now=None):
    """Persist the round-robin cursor before any external phase starts."""
    now = now or datetime.now(UTC)
    tasks = {t['id']: t for t in JsonTaskStore(state / 'tasks').list_tasks()}
    with locked_queue(state) as queue:
        jobs = queue['jobs']
        queue['active_task'] = None
        for offset in range(len(jobs)):
            index = (queue['cursor'] + offset) % len(jobs)
            job = jobs[index]
            task = tasks.get(job['task_id'])
            if not task:
                continue
            cancellation = cancel_path(state, task['id']).exists()
            if not cancellation:
                if task['status'] in PAUSED:
                    continue
                if any(tasks.get(dep, {}).get('status') != 'DONE' for dep in job['dependencies']):
                    continue
                if task.get('retry_at') and datetime.fromisoformat(task['retry_at']) > now:
                    continue
                if (task['status'] in {'CLAIMED', 'EXECUTING'} and task.get('lease')
                        and datetime.fromisoformat(task['lease']['expires_at']) > now):
                    continue
                budget = health(state, task['id'])
                if budget['failures'] >= 3 or budget['retry_after'] > now.timestamp():
                    continue
            queue['cursor'] = (index + 1) % len(jobs)
            queue['active_task'] = task['id']
            return task['id']
    return None


def summary(state):
    tasks = {t['id']: t for t in JsonTaskStore(state / 'tasks').list_tasks()}
    with locked_queue(state) as queue:
        rows = []
        for job in queue['jobs']:
            task = tasks.get(job['task_id'], {})
            rows.append({'task_id': job['task_id'], 'status': task.get('status', 'MISSING'),
                'dependencies': job['dependencies'],
                'blocked_dependencies': [dep for dep in job['dependencies']
                                         if tasks.get(dep, {}).get('status') != 'DONE'],
                'retry_at': task.get('retry_at'),
                'next_action': task.get('next_action', {}).get('type'),
                'supervisor': health(state, job['task_id'])})
        return {'active_task': queue['active_task'], 'tasks': rows}
