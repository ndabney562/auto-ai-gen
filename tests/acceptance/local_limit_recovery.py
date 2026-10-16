"""Opt-in real-process limit/restart trial with local CLI fixtures, never GitHub/model calls."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from orchestrator import supervisor
from orchestrator.store import JsonTaskStore
from orchestrator.subscription_runner import create_task


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    state, repo = args.state.resolve(), args.repo.resolve()
    if state.exists():
        raise ValueError('Use a fresh acceptance-only state directory')
    docker = shutil.which('docker')
    if not docker:
        raise RuntimeError('Docker must be installed')
    task_id = create_task(state, repo, 'Local subscription-limit recovery fixture',
                          ['docs/limit-recovery-fixture.md'], 'HEAD')
    store = JsonTaskStore(state / 'tasks')
    task = store.load(task_id)
    task.update(status='VALIDATING', result=json.dumps({'files': [
        {'path': 'docs/limit-recovery-fixture.md', 'content': '# Local limit recovery fixture\n'}]}))
    task['checkpoint']['implementation_provider'] = 'openai'
    store.save(task_id, task)
    supervisor.configure(state, task_id, 'fixture/no-publication', args.image, interval=5)
    binary = state / 'fixture-bin'
    binary.mkdir()
    requests = state / 'fixture-requests.json'
    shim = binary / 'claude'
    shim.write_text('''#!/usr/bin/env python3
import sys,json,time
from pathlib import Path
requests=Path(''' + repr(str(requests)) + ''')
if sys.argv[1:]==['auth','status']:
    print(json.dumps({'loggedIn':True,'authMethod':'claude.ai','subscriptionType':'pro'}))
else:
    count=json.loads(requests.read_text())['count'] if requests.exists() else 0
    requests.write_text(json.dumps({'count':count+1,'at':time.time()}))
    prompt=sys.stdin.read()
    if not count:
        print(json.dumps({'is_error':True,'subtype':'error','reset_at':time.time()+5,
            'result':"You've hit your weekly limit"}))
    else:
        payload=json.loads(prompt.split('\\n',1)[1])
        verdict={'approved':True,'candidate_sha':payload['candidate_sha'],
                 'reason':'LOCAL FIXTURE ONLY; no model review or publication'}
        print(json.dumps({'subtype':'success','result':json.dumps(verdict),
                          'session_id':'local-limit-fixture'}))
''')
    shim.chmod(0o755)
    (binary / 'python3').symlink_to(sys.executable)
    env = dict(os.environ, PATH=str(binary) + ':' + str(Path(docker).parent) + ':/usr/bin:/bin',
               PYTHONPATH=str(repo))
    # Use the production service/runner/CLI subprocess protocol. Replace ONLY the
    # publication phase so the fixture cannot publish even if observation is late.
    harness = ('from pathlib import Path; from orchestrator import supervisor as s; '
               'original=s.phase_command; '
               's.phase_command=lambda state,config,task: '
               '([__import__("sys").executable,"-c","pass"] '
               'if task["status"]=="WAIT_CI" else original(state,config,task)); '
               's.serve(Path(' + repr(str(state)) + '))')

    def start():
        return subprocess.Popen([sys.executable, '-c', harness], cwd=repo, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    service = start()
    waiting = None
    restarted = False
    deadline = time.monotonic() + 180
    try:
        while time.monotonic() < deadline:
            task = store.load(task_id)
            if task['status'] == 'WAIT_PROVIDER' and waiting is None:
                waiting = task
                assert requests.exists() and json.loads(requests.read_text())['count'] == 1
                assert waiting['checkpoint']['review_attempt'] == 0
                print(json.dumps({'waiting_until': task['retry_at'], 'candidate':
                                  task['checkpoint']['candidate_sha']}), flush=True)
                service.kill()
                service.wait(timeout=10)
                service = start()
                restarted = True
            if waiting:
                assert task['checkpoint']['candidate_sha'] == waiting['checkpoint']['candidate_sha']
                assert task['validation'] == waiting['validation']
                if time.time() < __import__('datetime').datetime.fromisoformat(waiting['retry_at']).timestamp():
                    assert json.loads(requests.read_text())['count'] == 1
                if task['status'] == 'WAIT_CI':
                    assert json.loads(requests.read_text())['count'] == 2
                    assert task['checkpoint']['review']['provider'] == 'anthropic'
                    assert task['checkpoint']['review_attempt'] == 1
                    assert restarted
                    blocked = dict(task, status='BLOCKED', next_action={'type': 'none'})
                    assert store.compare_and_save(task, blocked)
                    evidence = {'restart_during_limit_wait': True, 'no_early_requests': True,
                        'resumed_automatically': True, 'same_candidate': True,
                        'review_attempts_used': 1, 'fixture_requests': 2,
                        'real_model_requests': 0, 'github_requests': 0,
                        'validation': waiting['validation']}
                    (state / 'evidence.json').write_text(json.dumps(evidence, indent=2))
                    print(json.dumps(evidence), flush=True)
                    return
            if task['status'] in {'WAIT_HUMAN', 'FAILED_PERMANENT'}:
                raise RuntimeError('Fixture unexpectedly stopped: ' + task['status'])
            if service.poll() is not None:
                raise RuntimeError('Fixture service exited')
            time.sleep(.1)
        raise TimeoutError('Limit recovery acceptance timed out')
    finally:
        supervisor.request(state, 'stop')
        try:
            service.wait(timeout=15)
        except subprocess.TimeoutExpired:
            service.kill()
            service.wait(timeout=5)


if __name__ == '__main__':
    main()
