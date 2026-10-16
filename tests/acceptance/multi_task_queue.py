"""Unattended local trial: production queue/runner/Docker, fixture CLIs and local Git publisher.

No model requests or GitHub publication. Fixture approvals never leave this state.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from orchestrator import supervisor, task_queue
from orchestrator.store import JsonTaskStore
from orchestrator.subscription_runner import create_task, git

IMAGE = 'sha256:a6c4472c905a556a376be193ae622a002f1b5d390fe291930d84067892824b18'


class LocalPublisher:
    def __init__(self, state, repository):
        if repository != 'fixture/local':
            raise ValueError('Local fixture must never target GitHub')
        self.state = state
        self.source = state / 'source'
        self.store = JsonTaskStore(state / 'local-publisher')
        try:
            self.store.load('registry')
        except FileNotFoundError:
            self.store.save('registry', {'id': 'registry', 'pulls': {}})

    def fetch_main(self, workspace):
        git(workspace, 'fetch', str(self.source), 'HEAD:refs/agent/upstream-main')
        return git(workspace, 'rev-parse', 'refs/agent/upstream-main')

    def publish(self, workspace, task_id, sha):
        registry = self.store.load('registry')
        if sha not in registry['pulls']:
            registry['pulls'][sha] = {'number': len(registry['pulls']) + 1,
                'headRefOid': sha, 'state': 'OPEN', 'url': 'local-fixture:' + sha}
            self.store.save('registry', registry)
        task = JsonTaskStore(self.state / 'tasks').load(task_id)
        # Inject a real conflicting upstream commit once, after C was reviewed.
        if 'TRIAL_C' in task['objective'] and not (self.state / 'conflict-injected').exists():
            (self.source / 'docs/trial-a.md').write_text('# Trial A\nfirst\nconcurrent\n')
            git(self.source, 'add', 'docs/trial-a.md')
            git(self.source, 'commit', '-m', 'Concurrent trusted upstream edit')
            (self.state / 'conflict-injected').touch()
        return registry['pulls'][sha]

    def lookup_pr(self, number):
        registry = self.store.load('registry')
        return next(p for p in registry['pulls'].values() if p['number'] == number)

    def supersede(self, old, replacement):
        registry = self.store.load('registry')
        for value in registry['pulls'].values():
            if value['number'] == old['number']:
                assert value['headRefOid'] == old['headRefOid']
                value.update(state='CLOSED', replacement=replacement['number'])
        self.store.save('registry', registry)

    def ci_failure(self, sha):
        return None  # Offline Docker validation is the local trial's test gate.

    def verified_merge(self, number, sha, base):
        from orchestrator.github_publish import MainAdvanced

        if git(self.source, 'rev-parse', 'HEAD') != base:
            raise MainAdvanced('Local main advanced')
        task = next(t for t in JsonTaskStore(self.state / 'tasks').list_tasks()
                    if t['checkpoint'].get('candidate_sha') == sha)
        assert task['validation']['passed'] and task['validation']['candidate_sha'] == sha
        assert task['checkpoint']['review']['candidate_sha'] == sha
        assert task['checkpoint']['review']['provider'] != task['checkpoint']['implementation_provider']
        git(self.source, 'fetch', task['checkpoint']['workspace'], sha)
        git(self.source, 'merge', '--no-ff', '--no-edit', sha)
        registry = self.store.load('registry')
        registry['pulls'][sha]['state'] = 'MERGED'
        self.store.save('registry', registry)
        return True


def fixture_worker(state, provider):
    """Runs as a CLI child; mirrors login and event/result protocols only."""
    if sys.argv[3:] == ['login', 'status']:
        print('Logged in using ChatGPT')
        return
    if sys.argv[3:] == ['auth', 'status']:
        print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'pro'}))
        return
    prompt = sys.stdin.read()
    payload = json.loads(prompt.split('\n', 1)[1])
    ledger = JsonTaskStore(state / 'fixture-requests')
    key = payload['objective'].split()[0]
    try:
        request = ledger.load(provider + key)
    except FileNotFoundError:
        request = {'id': provider + key, 'count': 0}
    request['count'] += 1
    ledger.save(request['id'], request)
    if provider == 'claude':
        if key == 'TRIAL_A' and request['count'] == 1:
            print(json.dumps({'is_error': True, 'subtype': 'error', 'reset_at': time.time() + 5,
                              'result': "You've hit your weekly limit"}))
            return
        output = json.dumps({'approved': True, 'candidate_sha': payload['candidate_sha'],
                             'reason': 'LOCAL FIXTURE ONLY; not a real model approval'})
        print(json.dumps({'subtype': 'success', 'result': output, 'session_id': 'fixture'}))
        return
    target = next(iter(payload['sources']))
    if key == 'TRIAL_A':
        content = '# Trial A\nfirst\n'
    elif key == 'TRIAL_B':
        content = '# Trial B\nsecond\n'
    else:
        assert 'first' in payload['sources'][target]  # dependency's merged source was refreshed
        content = '# Trial A\nfirst\n'
        if payload.get('integration_conflicts'):
            assert 'concurrent' in payload['integration_conflicts'][target]['upstream']
            content += 'concurrent\n'
        content += 'third\n'
    output = json.dumps({'files': [{'path': target, 'content': content}]})
    for event in [{'type': 'thread.started', 'thread_id': 'fixture'},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': output}},
                  {'type': 'turn.completed'}]:
        print(json.dumps(event))


def serve_fixture(state):
    command = supervisor.phase_command

    def local_phase(current, config, task):
        return [sys.executable, '-m', 'tests.acceptance.multi_task_queue', 'phase', str(state),
                *command(current, config, task)[3:]]

    supervisor.phase_command = local_phase
    supervisor.serve(state)


def phase_fixture(state):
    import orchestrator.github_publish as publication
    import orchestrator.subscription_runner as runner

    publication.GitHubPublisher = lambda repository: LocalPublisher(state, repository)
    sys.argv = ['local-fixture-phase', *sys.argv[3:]]
    runner.main()


def run(state, repo, image):
    if state.exists():
        raise ValueError('Use a fresh acceptance-only state directory')
    state.mkdir(parents=True)
    source = state / 'source'
    git(repo, 'clone', '--no-hardlinks', str(repo), str(source))
    git(source, 'checkout', '--detach', git(repo, 'rev-parse', 'HEAD'))
    tasks = [create_task(state, source, key + ' preserve all trusted upstream work', [target], 'HEAD')
             for key, target in [('TRIAL_A', 'docs/trial-a.md'), ('TRIAL_B', 'docs/trial-b.md'),
                                 ('TRIAL_C', 'docs/trial-a.md')]]
    supervisor.configure(state, None, 'fixture/local', image, merge=True, interval=5)
    task_queue.enqueue(state, tasks[0])
    task_queue.enqueue(state, tasks[1])
    task_queue.enqueue(state, tasks[2], tasks[:2])
    binary = state / 'fixture-bin'
    binary.mkdir()
    for name in ('codex', 'claude'):
        shim = binary / name
        # sh quoting protects the spaces in the installed runtime interpreter path.
        import shlex
        bootstrap = ('import sys,runpy; sys.path.insert(0,' + repr(str(repo)) +
                     '); runpy.run_module("tests.acceptance.multi_task_queue",run_name="__main__")')
        shim.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) +
            ' -c ' + shlex.quote(bootstrap) + ' worker ' + shlex.quote(str(state)) +
            ' ' + shlex.quote(name) + ' "$@"\n')
        shim.chmod(0o755)
    docker = shutil.which('docker')
    if not docker:
        raise RuntimeError('Docker is required')
    env = dict(os.environ, PYTHONPATH=str(repo),
               PATH=str(binary) + ':' + str(Path(docker).parent) + ':/usr/bin:/bin')

    def start():
        return subprocess.Popen([sys.executable, '-m', 'tests.acceptance.multi_task_queue',
                                 'serve', str(state)], env=env, cwd=repo)

    service, restarted, previous = start(), False, None
    store = JsonTaskStore(state / 'tasks')
    deadline = time.monotonic() + 600
    try:
        while time.monotonic() < deadline:
            records = [store.load(tid) for tid in tasks]
            statuses = [task['status'] for task in records]
            if statuses != previous:
                print(json.dumps({'statuses': statuses}), flush=True)
                previous = statuses
            if not restarted and records[0]['status'] == 'WAIT_PROVIDER':
                service.kill()
                service.wait(timeout=10)
                service = start()
                restarted = True
            if any(status in {'WAIT_HUMAN', 'FAILED_PERMANENT', 'BLOCKED'} for status in statuses):
                raise RuntimeError('Trial stopped: ' + str(statuses))
            if all(status == 'DONE' for status in statuses):
                assert restarted
                assert 'concurrent' in (source / 'docs/trial-a.md').read_text()
                assert 'third' in (source / 'docs/trial-a.md').read_text()
                assert (source / 'docs/trial-b.md').exists()
                events = [event for task in records
                          for event in task['checkpoint'].get('integration_history', [])]
                assert any(event['conflict'] for event in events)
                assert any(event['replacement_sha'] and not event['conflict'] for event in events)
                registry = JsonTaskStore(state / 'local-publisher').load('registry')
                assert sum(pr['state'] == 'MERGED' for pr in registry['pulls'].values()) == 3
                assert sum(pr['state'] == 'CLOSED' for pr in registry['pulls'].values()) >= 2
                evidence = {'completed': 3, 'dependencies_honored': True, 'restart_during_quota': True,
                    'clean_integration': True, 'conflict_repaired': True, 'upstream_preserved': True,
                    'real_model_requests': 0, 'github_requests': 0,
                    'validation_runs': [task['validation'] for task in records],
                    'candidates': [task['checkpoint']['merged_candidate_sha'] for task in records]}
                (state / 'evidence.json').write_text(json.dumps(evidence, indent=2))
                print(json.dumps(evidence), flush=True)
                return
            if service.poll() is not None:
                raise RuntimeError('Trial service exited')
            time.sleep(.2)
        raise TimeoutError('Multi-task unattended local trial timed out')
    finally:
        supervisor.request(state, 'stop')
        try:
            service.wait(timeout=15)
        except subprocess.TimeoutExpired:
            service.kill()
            service.wait(timeout=5)


if __name__ == '__main__':
    mode = sys.argv[1]
    state = Path(sys.argv[2]).resolve()
    if mode == 'worker':
        provider = sys.argv.pop(3)
        fixture_worker(state, provider)
    elif mode == 'phase':
        phase_fixture(state)
    elif mode == 'serve':
        serve_fixture(state)
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument('--repo', type=Path, required=True)
        parser.add_argument('--image', default=IMAGE)
        args = parser.parse_args(sys.argv[3:])
        run(state, args.repo.resolve(), args.image)
