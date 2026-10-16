import json
from pathlib import Path

import pytest

from orchestrator.integration import begin, finish
from orchestrator.store import JsonTaskStore
from orchestrator.subscription_runner import (
    advance_task,
    candidate_commit,
    create_task,
    git,
    publish_task,
)
from providers.base import WorkerResult
from providers.health import ProviderHealth


@pytest.fixture
def prepared(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    git(repo, 'init')
    (repo / 'docs').mkdir()
    (repo / 'docs/a.md').write_text('base\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'base')
    state = tmp_path / 'state'
    task_id = create_task(state, repo, 'Keep goal and upstream changes', ['docs/a.md'], 'HEAD')
    store = JsonTaskStore(state / 'tasks')
    task = store.load(task_id)
    cp = dict(task['checkpoint'])
    workspace = Path(cp['workspace'])
    sha = candidate_commit(workspace, cp['base_sha'], task['targets'],
        json.dumps({'files': [{'path': 'docs/a.md', 'content': 'goal\n'}]}))
    cp.update(candidate_sha=sha, implementation_provider='openai',
              review={'provider': 'anthropic', 'candidate_sha': sha, 'approved': True})
    task.update(status='WAIT_CI', checkpoint=cp, result='unused',
                validation={'candidate_sha': sha, 'passed': True})
    store.save(task_id, task)
    return repo, state, task_id, workspace, store


class Publisher:
    def __init__(self, source):
        self.source = source
        self.prs = {}
        self.closed = []
        self.merges = []

    def fetch_main(self, workspace):
        git(workspace, 'fetch', str(self.source), 'HEAD:refs/agent/upstream-main')
        return git(workspace, 'rev-parse', 'refs/agent/upstream-main')

    def publish(self, workspace, task_id, sha):
        self.prs.setdefault(sha, {'number': len(self.prs) + 1, 'headRefOid': sha,
                                 'url': 'https://example/pr/' + sha, 'state': 'OPEN'})
        return self.prs[sha]

    def lookup_pr(self, number):
        return next(p for p in self.prs.values() if p['number'] == number)

    def supersede(self, old, replacement):
        self.closed.append((old['number'], replacement['number']))

    def ci_failure(self, sha):
        return None

    def verified_merge(self, number, sha, base):
        self.merges.append(sha)
        assert base == git(self.source, 'rev-parse', 'HEAD')
        return True


def upstream(repo, *, conflict=False):
    path = repo / 'docs' / ('a.md' if conflict else 'b.md')
    path.write_text('upstream\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'concurrent upstream')
    return git(repo, 'rev-parse', 'HEAD')


class Reviewer:
    def execute(self, handoff):
        payload = json.loads(handoff['prompt'].split('\n', 1)[1])
        return WorkerResult(json.dumps({'approved': True, 'candidate_sha': payload['candidate_sha'],
                                        'reason': 'Fixture confirms both changes preserved'}))


def test_clean_integration_invalidates_proofs_revalidates_and_supersedes(prepared):
    repo, state, task_id, workspace, store = prepared
    original = store.load(task_id)
    latest = upstream(repo)
    publisher = Publisher(repo)
    integrated = publish_task(state, task_id, 'owner/repo', merge=True, publisher=publisher)
    cp = integrated['checkpoint']
    assert integrated['status'] == 'VALIDATING'
    assert integrated['validation'] is None
    assert 'review' not in cp
    assert cp['candidate_sha'] != original['checkpoint']['candidate_sha']
    assert cp['base_sha'] == latest
    assert git(workspace, 'show', cp['candidate_sha'] + ':docs/a.md') == 'goal'
    assert git(workspace, 'show', cp['candidate_sha'] + ':docs/b.md') == 'upstream'
    assert publisher.merges == [] and publisher.closed == []
    # The upstream working tree and commit have not been modified.
    assert (repo / 'docs/a.md').read_text() == 'base\n'
    assert git(repo, 'rev-parse', 'HEAD') == latest

    def validate(_, sha, image):
        assert sha == cp['candidate_sha']
        return {'candidate_sha': sha, 'passed': True}

    assert advance_task(state, task_id, validator=validate)['status'] == 'REVIEW'
    assert advance_task(state, task_id, factories={'anthropic': Reviewer},
                        health=[ProviderHealth('anthropic')])['status'] == 'WAIT_CI'
    final = publish_task(state, task_id, 'owner/repo', merge=True, publisher=publisher)
    assert final['status'] == 'DONE'
    assert publisher.closed == [(1, 2)]
    assert publisher.merges == [cp['candidate_sha']]


def test_conflicts_return_bounded_context_to_model_and_require_new_review(prepared):
    repo, state, task_id, workspace, _store = prepared
    upstream(repo, conflict=True)
    integrated = publish_task(state, task_id, 'owner/repo', publisher=Publisher(repo))
    assert integrated['status'] == 'REPAIR'
    assert integrated['validation'] is None
    conflict = integrated['checkpoint']['integration_conflicts']['docs/a.md']
    assert conflict['upstream'] == 'upstream\n' and conflict['candidate'] == 'goal\n'
    assert '<<<<<<<' in conflict['merge_result']
    assert (workspace / 'docs/a.md').read_text() == 'upstream\n'
    assert advance_task(state, task_id)['status'] == 'READY'

    class Resolve:
        def execute(self, handoff):
            request = json.loads(handoff['prompt'].split('\n', 1)[1])
            assert request['integration_conflicts']['docs/a.md'] == conflict
            assert request['sources']['docs/a.md'] == 'goal\n'
            return WorkerResult(json.dumps({'files': [
                {'path': 'docs/a.md', 'content': 'goal\nupstream\n'}]}))

    assert advance_task(state, task_id, factories={'openai': Resolve},
                        health=[ProviderHealth('openai')])['status'] == 'VALIDATING'
    task = advance_task(state, task_id, validator=lambda _, sha, image: {
        'candidate_sha': sha, 'passed': True})
    assert task['status'] == 'REVIEW'
    assert git(workspace, 'show', task['checkpoint']['candidate_sha'] + ':docs/a.md') == 'goal\nupstream'
    assert 'review' not in task['checkpoint']


def test_integration_intent_is_replayed_after_checkout_interruption(prepared):
    repo, state, task_id, workspace, store = prepared
    latest = upstream(repo)
    Publisher(repo).fetch_main(workspace)
    task = begin(store, store.load(task_id), latest, return_phase='VALIDATING')
    assert task['status'] == 'INTEGRATING'
    git(workspace, 'checkout', '--detach', latest)
    recovered = advance_task(state, task_id)
    assert recovered['status'] == 'VALIDATING'
    assert recovered['checkpoint']['base_sha'] == latest
    assert recovered['validation'] is None
    assert 'review' not in recovered['checkpoint']


def test_queued_ready_task_refreshes_before_implementation(prepared, monkeypatch):
    from orchestrator.github_publish import GitHubPublisher

    repo, state, task_id, _workspace, store = prepared
    task = store.load(task_id)
    task.update(status='READY', validation=None)
    task['checkpoint'].pop('candidate_sha')
    task['checkpoint'].pop('review')
    store.save(task_id, task)
    latest = upstream(repo, conflict=True)
    monkeypatch.setattr(GitHubPublisher, 'fetch_main', Publisher(repo).fetch_main)
    updated = advance_task(state, task_id, repository='owner/repo')
    assert updated['status'] == 'READY' and updated['checkpoint']['base_sha'] == latest

    class Worker:
        def execute(self, handoff):
            request = json.loads(handoff['prompt'].split('\n', 1)[1])
            assert request['sources']['docs/a.md'] == 'upstream\n'
            return WorkerResult(json.dumps({'files': [
                {'path': 'docs/a.md', 'content': 'upstream\ngoal\n'}]}))

    assert advance_task(state, task_id, repository='owner/repo',
                        factories={'openai': Worker}, health=[ProviderHealth('openai')])['status'] == 'VALIDATING'


def test_main_move_at_merge_gate_routes_back_to_integration(prepared):
    from orchestrator.github_publish import MainAdvanced

    repo, state, task_id, _workspace, _store = prepared

    class Race(Publisher):
        def verified_merge(self, *args):
            upstream(repo)
            raise MainAdvanced('concurrent merge')

    task = publish_task(state, task_id, 'owner/repo', merge=True, publisher=Race(repo))
    assert task['status'] == 'VALIDATING'
    assert 'review' not in task['checkpoint']
    assert task['validation'] is None


def test_already_merged_candidate_is_reconciled_without_rebase(prepared):
    repo, state, task_id, _workspace, _store = prepared
    upstream(repo)

    class Merged(Publisher):
        def lookup_pr(self, number):
            return dict(super().lookup_pr(number), state='MERGED')

    task = publish_task(state, task_id, 'owner/repo', publisher=Merged(repo))
    assert task['status'] == 'DONE'
    assert 'integration_history' not in task['checkpoint']


def test_rewrite_and_excessive_churn_stop_without_discarding_evidence(prepared):
    repo, _state, task_id, workspace, store = prepared
    original = store.load(task_id)
    tree = git(repo, 'rev-parse', 'HEAD^{tree}')
    root = git(repo, 'commit-tree', tree, text='unrelated root')
    git(repo, 'update-ref', 'refs/heads/rewrite', root)
    git(workspace, 'fetch', str(repo), 'refs/heads/rewrite:refs/agent/rewrite')
    blocked = begin(store, original, root, return_phase='VALIDATING')
    assert blocked['status'] == 'BLOCKED'
    assert blocked['validation'] == original['validation']
    store.save(task_id, original)
    latest = upstream(repo)
    Publisher(repo).fetch_main(workspace)
    task = store.load(task_id)
    task['checkpoint']['integration_attempt'] = 5
    store.save(task_id, task)
    assert begin(store, task, latest, return_phase='VALIDATING')['status'] == 'BLOCKED'


def test_unrequested_candidate_changes_cannot_be_integrated(prepared):
    repo, _state, task_id, workspace, store = prepared
    task = store.load(task_id)
    base = task['checkpoint']['base_sha']
    bad = candidate_commit(workspace, base, ['docs/a.md', 'docs/extra.md'], json.dumps({'files': [
        {'path': 'docs/a.md', 'content': 'goal'}, {'path': 'docs/extra.md', 'content': 'extra'}]}))
    task['checkpoint']['candidate_sha'] = bad
    store.save(task_id, task)
    latest = upstream(repo)
    Publisher(repo).fetch_main(workspace)
    integrating = begin(store, task, latest, return_phase='VALIDATING')
    stopped = finish(store, integrating)
    assert stopped['status'] == 'WAIT_HUMAN'
    assert stopped['checkpoint']['candidate_sha'] == bad
    assert git(workspace, 'rev-parse', 'HEAD') == base


def test_blob_context_preserves_leading_and_trailing_whitespace(prepared):
    from orchestrator.integration import blob

    repo, _state, _task_id, workspace, _store = prepared
    (repo / 'docs/a.md').write_text('    indented markdown\n\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'whitespace semantics')
    latest = Publisher(repo).fetch_main(workspace)
    assert blob(workspace, latest, 'docs/a.md') == '    indented markdown\n\n'


def test_unresolved_conflict_markers_are_rejected_before_validation(prepared):
    repo, state, task_id, _workspace, store = prepared
    upstream(repo, conflict=True)
    publish_task(state, task_id, 'owner/repo', publisher=Publisher(repo))
    advance_task(state, task_id)

    class Broken:
        def execute(self, handoff):
            return WorkerResult(json.dumps({'files': [{'path': 'docs/a.md',
                'content': '<<<<<<< branch\ngoal\n=======\nupstream\n>>>>>>> main\n'}]}))

    advance_task(state, task_id, factories={'openai': Broken}, health=[ProviderHealth('openai')])
    task = advance_task(state, task_id, validator=lambda *_: pytest.fail('unresolved conflicts'))
    assert task['status'] == 'REPAIR'
    assert task['validation'] is None
    assert store.load(task_id)['attempt'] == 1
