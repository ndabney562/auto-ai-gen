from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from orchestrator import supervisor as s
from orchestrator import task_queue as q
from orchestrator.store import JsonTaskStore


def setup(state, names=('a', 'b', 'c')):
    store = JsonTaskStore(state / 'tasks')
    for name in names:
        store.save(name, {'id': name, 'status': 'READY', 'checkpoint': {},
                          'next_action': {'type': 'implement'}})
    s.configure(state, None, 'owner/repo', 'sha256:' + 'a' * 64, interval=5)
    return s.records(state).load('config')


def test_membership_is_explicit_durable_and_idempotent(tmp_path):
    setup(tmp_path)
    q.enqueue(tmp_path, 'a')
    q.enqueue(tmp_path, 'b', ['a'])
    q.enqueue(tmp_path, 'a')
    assert [j['task_id'] for j in q.summary(tmp_path)['tasks']] == ['a', 'b']
    assert q.select_task(tmp_path) == 'a'
    with pytest.raises(ValueError):
        q.enqueue(tmp_path, 'a', ['b'])
    with pytest.raises(ValueError):
        q.enqueue(tmp_path, 'c', ['absent'])
    with pytest.raises(ValueError):
        q.enqueue(tmp_path, 'c', ['c'])


def test_concurrent_enrollment_loses_no_tasks(tmp_path):
    setup(tmp_path, [str(i) for i in range(20)])
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda i: q.enqueue(tmp_path, str(i)), range(20)))
    assert len(q.summary(tmp_path)['tasks']) == 20


def test_round_robin_cursor_survives_interrupted_phase(tmp_path):
    setup(tmp_path)
    for task_id in ('a', 'b', 'c'):
        q.enqueue(tmp_path, task_id)
    assert q.select_task(tmp_path) == 'a'
    # A new process reads the saved cursor rather than beginning again at a.
    assert q.select_task(tmp_path) == 'b'
    assert q.select_task(tmp_path) == 'c'
    assert q.select_task(tmp_path) == 'a'


def test_quota_wait_and_failed_dependencies_do_not_starve_other_tasks(tmp_path):
    config = setup(tmp_path)
    q.enqueue(tmp_path, 'a')
    q.enqueue(tmp_path, 'b', ['a'])
    q.enqueue(tmp_path, 'c')
    store = JsonTaskStore(tmp_path / 'tasks')
    task = store.load('a')
    task.update(status='WAIT_PROVIDER', retry_at=(datetime.now(UTC) + timedelta(days=1)).isoformat())
    store.save('a', task)
    calls = []
    s.tick(tmp_path, config, lambda: False, phase=lambda _, command, __: calls.append(command) or 0)
    assert calls[0][calls[0].index('step') + 1] == 'c'
    assert q.summary(tmp_path)['tasks'][1]['blocked_dependencies'] == ['a']
    task.update(status='FAILED_PERMANENT')
    store.save('a', task)
    assert q.select_task(tmp_path) == 'c'
    task.update(status='DONE')
    store.save('a', task)
    assert q.select_task(tmp_path) == 'b'


def test_per_task_service_budget_persists_without_blocking_other_work(tmp_path):
    config = setup(tmp_path)
    q.enqueue(tmp_path, 'a')
    q.enqueue(tmp_path, 'b')
    calls = []

    def phase(_, command, __):
        task = command[command.index('step') + 1]
        calls.append(task)
        return 1 if task == 'a' else 0

    for now in (100, 200, 400, 800, 1600, 3200, 6400, 12800):
        s.tick(tmp_path, config, lambda: False, phase=phase, now=lambda now=now: now)
    assert calls.count('a') == 3
    assert calls.count('b') == 5
    assert q.health(tmp_path, 'a')['failures'] == 3
    assert q.health(tmp_path, 'b')['failures'] == 0


def test_live_enqueue_is_visible_to_existing_service_config(tmp_path):
    config = setup(tmp_path)
    assert q.select_task(tmp_path) is None
    q.enqueue(tmp_path, 'b')
    calls = []
    s.tick(tmp_path, config, lambda: False, phase=lambda *args: calls.append(args[1]) or 0)
    assert 'b' in calls[0]
    assert q.summary(tmp_path)['active_task'] is None


@pytest.mark.parametrize('active', [True, False])
def test_targeted_cancel_does_not_cancel_another_task(tmp_path, active):
    config = setup(tmp_path)
    q.enqueue(tmp_path, 'a')
    q.enqueue(tmp_path, 'b')
    if active:
        with s.service_lock(tmp_path):
            s.request(tmp_path, 'cancel', 'b')
            assert JsonTaskStore(tmp_path / 'tasks').load('b')['status'] == 'READY'
            for _ in range(2):
                s.tick(tmp_path, config, lambda: False, phase=lambda *_: 0)
    else:
        s.request(tmp_path, 'cancel', 'b')
    assert JsonTaskStore(tmp_path / 'tasks').load('b')['status'] == 'CANCELLED'
    assert JsonTaskStore(tmp_path / 'tasks').load('a')['status'] == 'READY'


def test_cancel_active_phase_only_after_cleanup(tmp_path):
    config = setup(tmp_path)
    q.enqueue(tmp_path, 'a')
    q.enqueue(tmp_path, 'b')
    store = JsonTaskStore(tmp_path / 'tasks')
    events = []

    def phase(_, command, stopping):
        s.request(tmp_path, 'cancel')  # resolves the durable active task a
        assert stopping()
        assert store.load('a')['status'] == 'READY'
        events.append('cleanup')

    with s.service_lock(tmp_path):
        s.tick(tmp_path, config, lambda: False, phase=phase)
    assert events == ['cleanup']
    assert store.load('a')['status'] == 'CANCELLED'
    assert store.load('b')['status'] == 'READY'


def test_idle_cancel_requires_explicit_target_and_wait_can_be_cancelled(tmp_path):
    config = setup(tmp_path)
    q.enqueue(tmp_path, 'a')
    with pytest.raises(ValueError):
        s.request(tmp_path, 'cancel')
    with pytest.raises(ValueError):
        s.request(tmp_path, 'cancel', '../not-enrolled')
    store = JsonTaskStore(tmp_path / 'tasks')
    task = store.load('a')
    task.update(status='WAIT_PROVIDER', retry_at='2999-01-01T00:00:00+00:00')
    store.save('a', task)
    with s.service_lock(tmp_path):
        s.request(tmp_path, 'cancel', 'a')
        s.tick(tmp_path, config, lambda: False, phase=lambda *_: pytest.fail('cancelled wait'))
    assert store.load('a')['status'] == 'CANCELLED'


def test_queue_status_contains_no_objectives_or_source(tmp_path):
    setup(tmp_path)
    q.enqueue(tmp_path, 'a')
    result = s.status(tmp_path)
    assert result['mode'] == 'queue'
    assert result['tasks'][0]['status'] == 'READY'
    assert 'checkpoint' not in result['tasks'][0]


def test_phase_with_live_lease_waits_for_recovery(tmp_path):
    setup(tmp_path)
    q.enqueue(tmp_path, 'a')
    store = JsonTaskStore(tmp_path / 'tasks')
    task = store.load('a')
    task.update(status='EXECUTING', lease={'expires_at': '2999-01-01T00:00:00+00:00'})
    store.save('a', task)
    assert q.select_task(tmp_path) is None


def test_enqueue_during_service_phase_is_not_lost(tmp_path):
    config = setup(tmp_path)
    q.enqueue(tmp_path, 'a')
    entered, release = Event(), Event()

    def phase(*_):
        entered.set()
        assert release.wait(5)
        return 0

    with ThreadPoolExecutor(max_workers=1) as pool:
        run = pool.submit(s.tick, tmp_path, config, lambda: False, phase=phase)
        assert entered.wait(5)
        q.enqueue(tmp_path, 'b')
        release.set()
        run.result(timeout=5)
    assert [j['task_id'] for j in q.summary(tmp_path)['tasks']] == ['a', 'b']
