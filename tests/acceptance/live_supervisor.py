"""Opt-in background-service validation/restart check; makes no model requests."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from orchestrator import supervisor
from orchestrator.store import JsonTaskStore
from orchestrator.subscription_runner import create_task


def wait_for(state, predicate, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = supervisor.status(state)
        if predicate(current):
            return current
        if not current['running']:
            raise RuntimeError('Service stopped unexpectedly')
        time.sleep(.2)
    raise TimeoutError('Service acceptance timed out')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    state = args.state.resolve()
    if state.exists():
        raise ValueError('Use a fresh acceptance-only state directory')
    # Keep Docker and Git available, but no subscription CLIs: REVIEW must wait.
    docker = shutil.which('docker')
    if not docker:
        raise RuntimeError('Docker is required')
    os.environ['PATH'] = str(Path(docker).parent) + ':/usr/bin:/bin'
    if shutil.which('codex') or shutil.which('claude'):
        raise RuntimeError('Use a Docker-only PATH for this no-model acceptance')
    task_id = create_task(state, args.repo.resolve(), 'Supervisor validation acceptance fixture',
                          ['docs/supervisor-acceptance.md'], 'HEAD')
    store = JsonTaskStore(state / 'tasks')
    task = store.load(task_id)
    task['status'] = 'VALIDATING'
    task['checkpoint']['implementation_provider'] = 'openai'
    task['result'] = json.dumps({'files': [{'path': 'docs/supervisor-acceptance.md',
                                           'content': '# Supervisor acceptance fixture\n'}]})
    store.save(task_id, task)
    supervisor.configure(state, task_id, 'ndabney562/auto-ai-gen', args.image, interval=5)
    try:
        supervisor.start(state)
        wait_for(state, lambda s: s['task_status'] == 'WAIT_PROVIDER')
        validated = store.load(task_id)
        assert validated['validation']['passed'] is True
        sha = validated['checkpoint']['candidate_sha']
        supervisor.request(state, 'stop')
        deadline = time.monotonic() + 15
        while supervisor.running(state) and time.monotonic() < deadline:
            time.sleep(.1)
        assert not supervisor.running(state)
        supervisor.start(state)
        wait_for(state, lambda s: s['task_status'] == 'WAIT_PROVIDER')
        assert store.load(task_id)['checkpoint']['candidate_sha'] == sha
        supervisor.request(state, 'cancel')
        wait_for(state, lambda s: s['task_status'] == 'CANCELLED')
        evidence = {'task_id': task_id, 'candidate_sha': sha,
                    'validation': validated['validation'], 'restart_preserved_candidate': True,
                    'cancelled': True, 'model_requests': 0}
        (state / 'evidence.json').write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence))
    finally:
        supervisor.request(state, 'stop')
        deadline = time.monotonic() + 15
        while supervisor.running(state) and time.monotonic() < deadline:
            time.sleep(.1)
        if supervisor.running(state):
            raise RuntimeError('Service did not stop')


if __name__ == '__main__':
    main()
