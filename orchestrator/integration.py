"""Restartable Git-object integration; never execute candidate code on the host."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _git(repo, *args, text=None, strip=True):
    from orchestrator.subscription_runner import git
    return git(repo, *args, text=text, strip=strip)


def blob(repo, ref, target):
    entry = _git(repo, 'ls-tree', ref, '--', target)
    if not entry:
        return ''
    if entry.split(' ', 1)[0] not in {'100644', '100755'}:
        raise ValueError('Integration target is not an ordinary file')
    value = _git(repo, 'show', f'{ref}:{target}', strip=False)
    if len(value.encode()) > 40000:
        raise ValueError('Integration context exceeds the source bound')
    return value


def begin(store, task, new_base, *, return_phase):
    """Save intent before modifying the dedicated workspace or writing a tree."""
    from orchestrator.subscription_runner import _save

    cp = dict(task['checkpoint'])
    old_base = cp['base_sha']
    if old_base == new_base:
        return task
    try:
        _git(Path(cp['workspace']), 'merge-base', '--is-ancestor', old_base, new_base)
    except subprocess.CalledProcessError as exc:
        if exc.returncode != 1:
            raise
        return _save(store, task, dict(task, status='BLOCKED',
            next_action={'type': 'inspect_rewritten_upstream'}))
    source = cp.get('candidate_sha') or cp.get('repair_candidate_sha')
    if source and cp.get('integration_attempt', 0) >= 5:
        return _save(store, task, dict(task, status='BLOCKED',
            next_action={'type': 'integration_attempts_exhausted'}))
    cp['integration_attempt'] = cp.get('integration_attempt', 0) + bool(source)
    cp['integration_intent'] = {'from_base': old_base, 'to_base': new_base,
        'candidate_sha': source, 'patch_base': old_base if cp.get('candidate_sha') else
        cp.get('repair_base_sha', old_base), 'return_phase': return_phase}
    return _save(store, task, dict(task, status='INTEGRATING', checkpoint=cp,
                                  next_action={'type': 'integrate_latest_main'}))


def finish(store, task):
    from orchestrator.engineering import safe_target
    from orchestrator.subscription_runner import _save

    cp = dict(task['checkpoint'])
    intent = cp['integration_intent']
    repo, new_base = Path(cp['workspace']), intent['to_base']
    source, patch_base = intent['candidate_sha'], intent['patch_base']
    try:
        conflict = False
        replacement = None
        conflicts = {}
        if source:
            changes = _git(repo, 'diff', '--name-only', '-z', patch_base, source).split('\0')
            if set(filter(None, changes)) - set(task['targets']):
                raise ValueError('Candidate contains changes outside its targets')
            env = {key: value for key, value in os.environ.items()
                   if key in {'PATH', 'HOME', 'TMPDIR'}}
            env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')
            result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(repo),
                'merge-tree', '--write-tree', '--merge-base=' + patch_base, new_base, source],
                check=False, capture_output=True, text=True, timeout=60, env=env)
            if result.returncode not in {0, 1}:
                raise subprocess.CalledProcessError(result.returncode, result.args)
            tree = result.stdout.splitlines()[0]
            if len(tree) != 40 or any(c not in '0123456789abcdef' for c in tree):
                raise ValueError('Invalid integration tree')
            conflict = result.returncode == 1
            if conflict:
                for target in task['targets']:
                    conflicts[target] = {'base': blob(repo, patch_base, target),
                        'upstream': blob(repo, new_base, target),
                        'candidate': blob(repo, source, target),
                        'merge_result': blob(repo, tree, target)}
                if len(json.dumps(conflicts).encode()) > 120000:
                    raise ValueError('Conflict context exceeds bound')
            else:
                changed = _git(repo, 'diff', '--name-only', '-z', new_base, tree).split('\0')
                if set(filter(None, changed)) - set(task['targets']):
                    raise ValueError('Integration changed an unrequested path')
                if not any(changed):
                    return _save(store, task, dict(task, status='WAIT_HUMAN',
                        next_action={'type': 'reconcile_already_applied_change'}))
                for target in task['targets']:
                    blob(repo, tree, target)
                replacement = _git(repo, 'commit-tree', tree, '-p', new_base,
                                   text='Integrate subscription candidate onto current main\n')
                _git(repo, 'update-ref', 'refs/agent/candidates/' + replacement, replacement)
        # This checkout is private and contains only trusted upstream source.
        # The persisted INTEGRATING intent makes an interrupted checkout replayable.
        _git(repo, 'checkout', '--detach', new_base)
        for target in task['targets']:
            safe_target(repo, target)
        old_pr = cp.pop('pull_request', None)
        if old_pr:
            cp['superseded_pull_requests'] = [*cp.get('superseded_pull_requests', []), old_pr]
        cp['base_sha'] = new_base
        for key in ('review', 'review_attempt', 'resume_phase', 'candidate_sha', 'integration_intent'):
            cp.pop(key, None)
        cp['integration_history'] = [*cp.get('integration_history', [])[-19:],
            {**intent, 'replacement_sha': replacement, 'conflict': conflict}]
        cp.pop('integration_conflicts', None)
        if conflict:
            cp['repair_candidate_sha'], cp['repair_base_sha'] = source, patch_base
            cp['integration_conflicts'] = conflicts
            cp['known_failures'] = [*cp.get('known_failures', []),
                'Resolve the recorded integration conflicts while preserving upstream work.']
            status = 'REPAIR'
        elif intent['return_phase'] == 'READY':
            if replacement:
                cp['repair_candidate_sha'], cp['repair_base_sha'] = replacement, new_base
            status = 'READY'
        else:
            cp['candidate_sha'] = replacement
            status = 'VALIDATING'
        updated = dict(task, status=status, checkpoint=cp, validation=None,
                       next_action={'type': 'repair_integration_conflict' if conflict else
                                    'validate_integrated_candidate' if replacement else 'implement'})
        updated.pop('retry_at', None)
        return _save(store, task, updated)
    except (ValueError, IndexError, subprocess.CalledProcessError) as exc:
        return _save(store, task, dict(task, status='WAIT_HUMAN',
            next_action={'type': 'inspect_integration_failure', 'error': type(exc).__name__}))
