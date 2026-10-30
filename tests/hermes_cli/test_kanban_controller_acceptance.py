"""Controller contracts never accept model metadata as an installation proof."""
import json
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_connect import connect


def test_controller_contract_requires_one_trusted_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    kb.init_db()
    from hermes_cli import plugins
    results = []
    monkeypatch.setattr(plugins, 'invoke_hook', lambda hook, **kw: list(results))
    with connect() as conn:
        task = kb.create_task(conn, title='synthetic', completion_contract='controller:sample')
        assert not kb.complete_task(conn, task, result='done', metadata={'ok': True})
        results[:] = [{'contract': 'controller:other', 'ok': True}]
        assert not kb.complete_task(conn, task, result='done')
        proof = {'contract': 'controller:sample', 'ok': True, 'head_sha': 'a'*40}
        results[:] = [proof, proof]
        assert not kb.complete_task(conn, task, result='done')
        results[:] = [proof]
        assert kb.complete_task(conn, task, result='done')
        events = conn.execute("SELECT payload FROM task_events WHERE task_id=? AND kind='controller_acceptance'", (task,)).fetchall()
        assert json.loads(events[-1][0])['head_sha'] == 'a'*40


def test_controller_evidence_cannot_survive_ownership_change(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    kb.init_db()
    from hermes_cli import plugins
    with connect() as conn:
        task = kb.create_task(conn, title='synthetic race', completion_contract='controller:sample')
        def changed(hook, **kwargs):
            conn.execute("UPDATE tasks SET assignee='other' WHERE id=?", (task,))
            conn.commit()
            return [{'contract':'controller:sample', 'ok':True, 'head_sha':'a'*40}]
        monkeypatch.setattr(plugins, 'invoke_hook', changed)
        assert not kb.complete_task(conn, task, result='done')
        assert kb.get_task(conn, task).status != 'done'


def test_provider_uses_assignee_home_and_restores_caller(tmp_path, monkeypatch):
    from hermes_cli import kanban_pr_acceptance as gate, plugins
    from hermes_cli.kanban_controller_acceptance import collect_controller_acceptance
    from hermes_constants import get_hermes_home
    before = get_hermes_home()
    home = tmp_path / 'synthetic-assignee'
    monkeypatch.setattr(gate, '_assignee_profile_home', lambda assignee: str(home))
    def callback(hook, **kwargs):
        assert str(get_hermes_home()) == str(home)
        assert kwargs == {'contract':'controller:sample','task_id':'t_1234abcd','assignee':'sample'}
        return [{'contract':'controller:sample','ok':True,'head_sha':'a'*40}]
    monkeypatch.setattr(plugins, 'invoke_hook', callback)
    assert collect_controller_acceptance('controller:sample','t_1234abcd','sample')['ok']
    assert get_hermes_home() == before
    monkeypatch.setattr(plugins, 'invoke_hook', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('PRIVATE')))
    receipt = collect_controller_acceptance('controller:sample','t_1234abcd','sample')
    assert not receipt['ok'] and 'PRIVATE' not in str(receipt)
    assert get_hermes_home() == before
