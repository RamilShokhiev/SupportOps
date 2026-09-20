"""Review-team HTTP boundaries using synthetic diagnostics and isolated databases."""

import csv
import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import services
from app.main import create_app
from app.models import Approval, DemoIssue, Execution, Ticket, User, WorkflowRun, utcnow
from test_scenarios import (
    CHECKOUT_TEXT,
    SimulatedProcessCrash,
    action_request,
    approve,
    client,
    config,
    count,
    create_ticket,
    login,
    upstream,
)


TEAM = 'multi_agent_review'


def analyze_team(client, **overrides):
    ticket = create_ticket(client, **overrides)
    response = client.post(f'/api/tickets/{ticket["id"]}/analyze', json={'workflow_mode': TEAM})
    assert response.status_code == 200, response.text
    return response.json()


def current_run(client, ticket):
    with client.app.state.session_factory() as db:
        return db.scalar(select(WorkflowRun).where(WorkflowRun.ticket_id == ticket['id']).order_by(WorkflowRun.created_at.desc()))


def test_review_team_has_six_recorded_roles_and_stops_before_human_approval(client):
    ticket = analyze_team(client)
    assert ticket['workflow_mode'] == TEAM
    assert ticket['status'] == 'awaiting_approval'
    assert ticket['review_pending'] is True
    team = ticket['analysis']['review_team']
    assert team['provider'] == 'demo'
    assert team['human_review_required'] is True
    assert {role['role'] for role in team['roles']} == {'triage', 'knowledge', 'diagnostics', 'response', 'safety', 'coordinator'}
    assert team['rounds'] >= 1 and team['revisions'] <= team['limits']['max_revisions'] == 1
    run = current_run(client, ticket)
    assert run.workflow_mode == TEAM and run.status == 'waiting'
    snapshot = client.app.state.review_graph.get_state(services.graph_config(run))
    assert snapshot.interrupts and 'decision' not in snapshot.values
    assert action_request(client, ticket['action'], 'execute').status_code == 409
    assert count(client, Approval) == count(client, Execution) == count(client, DemoIssue) == 0


def test_review_team_approval_resumes_correct_graph_and_preserves_evidence(client):
    ticket = analyze_team(client)
    recorded = ticket['analysis']['review_team']
    ticket = approve(client, ticket)
    run = current_run(client, ticket)
    assert run.status == 'completed' and run.workflow_mode == TEAM
    snapshot = client.app.state.review_graph.get_state(services.graph_config(run))
    assert not snapshot.next and not snapshot.interrupts
    assert snapshot.values['decision']['decision'] == 'action_approved'
    assert ticket['analysis']['review_team'] == recorded
    result = action_request(client, ticket['action'], 'execute')
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'escalated'
    assert result.json()['analysis']['review_team'] == recorded
    assert count(client, Execution) == count(client, DemoIssue) == 1


@pytest.mark.parametrize('body', [None, {}, {'workflow_mode': 'standard'}])
def test_standard_analysis_remains_the_compatible_default(client, body):
    ticket = create_ticket(client)
    response = client.post(f'/api/tickets/{ticket["id"]}/analyze', **({'json': body} if body is not None else {}))
    assert response.status_code == 200, response.text
    ticket = response.json()
    assert ticket['workflow_mode'] == 'standard'
    assert ticket['analysis']['next_step'] == 'escalate'
    assert not ticket['analysis'].get('review_team')
    assert current_run(client, ticket).workflow_mode == 'standard'


@pytest.mark.parametrize('body', [
    {'workflow_mode': 'autonomous'},
    {'workflow_mode': TEAM, 'auto_approve': True},
])
def test_invalid_analysis_options_do_not_start_a_workflow(client, upstream, body):
    ticket = create_ticket(client)
    response = client.post(f'/api/tickets/{ticket["id"]}/analyze', json=body)
    assert response.status_code == 422
    assert client.get(f'/api/tickets/{ticket["id"]}').json()['status'] == 'new'
    assert count(client, WorkflowRun) == 0 and upstream == []


def test_viewer_cannot_run_team_or_complete_its_review(client):
    ticket = analyze_team(client)
    new_ticket = create_ticket(client)
    login(client, 'viewer@northstar.demo')
    assert client.get(f'/api/tickets/{ticket["id"]}').json()['analysis']['review_team']
    assert client.post(f'/api/tickets/{new_ticket["id"]}/analyze', json={'workflow_mode': TEAM}).status_code == 403
    assert client.post(f'/api/tickets/{ticket["id"]}/resume').status_code == 403
    assert action_request(client, ticket['action'], 'approve').status_code == 403
    assert action_request(client, ticket['action'], 'execute').status_code == 403
    assert count(client, WorkflowRun) == 1
    assert count(client, Approval) == count(client, Execution) == count(client, DemoIssue) == 0


def test_review_team_keeps_tickets_and_knowledge_scoped_to_current_tenant(client):
    text = 'Please export Northstar transaction CSV data from RetailBridge 3.8.'
    northstar = analyze_team(client, text=text)
    assert 'data-export' in {source['document_key'] for source in northstar['analysis']['sources']}
    northstar_source_ids = {source['id'] for source in northstar['analysis']['sources']}
    login(client, 'support@contoso.demo')
    assert client.get(f'/api/tickets/{northstar["id"]}').status_code == 404
    assert client.post(f'/api/tickets/{northstar["id"]}/analyze', json={'workflow_mode': TEAM}).status_code == 404
    assert client.post(f'/api/tickets/{northstar["id"]}/resume').status_code == 404
    contoso = analyze_team(client, text=text)
    assert 'data-export' not in {source['document_key'] for source in contoso['analysis']['sources']}
    for role in contoso['analysis']['review_team']['roles']:
        assert not northstar_source_ids.intersection(role['source_ids'])


def test_conflicting_sources_cannot_be_voted_into_a_team_answer(client):
    ticket = analyze_team(client, text='Please plan a RetailBridge 3.9 upgrade next month.')
    assert ticket['status'] == 'awaiting_clarification' and ticket['action'] is None
    assert ticket['analysis']['next_step'] == 'clarify'
    assert {source['document_key'] for source in ticket['analysis']['sources']} >= {'upgrade-v39-a', 'upgrade-v39-b'}
    assert any('conflict' in warning.lower() for warning in ticket['analysis']['warnings'])
    assert count(client, Execution) == count(client, DemoIssue) == 0


@pytest.mark.parametrize('mode', ['timeout', 'error'])
def test_team_diagnostic_failure_stays_unknown_and_cannot_create_action(client, upstream, mode):
    ticket = analyze_team(client, diagnostic_mode=mode)
    assert ticket['status'] == 'awaiting_clarification' and ticket['action'] is None
    assert len(upstream) == 4
    assert all(item['status'] == mode and item['data'] is None for item in ticket['analysis']['diagnostics'])
    assert all(fact['kind'] != 'api' for fact in ticket['analysis']['facts'])
    assert ticket['analysis']['review_team']['revisions'] == 0
    assert not any(f['code'] == 'unknown_citation' for f in ticket['analysis']['review_team']['disagreements'])
    assert client.get('/api/dashboard').json()['api_errors'] == 2


def test_clarification_keeps_selected_mode_even_if_default_changes(client):
    ticket = analyze_team(client, text='Receipt printer failed with P-102 at one store.')
    assert ticket['status'] == 'awaiting_clarification'
    client.app.state.settings.default_workflow_mode = 'standard'
    response = client.post(f'/api/tickets/{ticket["id"]}/clarify', json={'text': 'This is RetailBridge 3.8.'})
    assert response.status_code == 200, response.text
    ticket = response.json()
    assert ticket['workflow_mode'] == TEAM and ticket['analysis']['review_team']
    assert ticket['status'] == 'ready' and ticket['review_pending'] is True
    assert client.post(f'/api/tickets/{ticket["id"]}/resolve').status_code == 409
    with client.app.state.session_factory() as db:
        runs = db.scalars(select(WorkflowRun).where(WorkflowRun.ticket_id == ticket['id']).order_by(WorkflowRun.created_at)).all()
        assert [run.workflow_mode for run in runs] == [TEAM, TEAM]
        assert [run.status for run in runs] == ['completed', 'waiting']
    accepted = client.post(f'/api/tickets/{ticket["id"]}/review', json={'decision': 'accepted'})
    assert accepted.status_code == 200 and accepted.json()['review_pending'] is False
    assert client.post(f'/api/tickets/{ticket["id"]}/resolve').json()['status'] == 'resolved'


def test_configured_team_default_can_be_overridden_per_analysis(client):
    client.app.state.settings.default_workflow_mode = TEAM
    ticket = create_ticket(client)
    response = client.post(f'/api/tickets/{ticket["id"]}/analyze')
    assert response.status_code == 200 and response.json()['workflow_mode'] == TEAM
    standard = create_ticket(client)
    response = client.post(f'/api/tickets/{standard["id"]}/analyze', json={'workflow_mode': 'standard'})
    assert response.status_code == 200 and response.json()['workflow_mode'] == 'standard'
    assert not response.json()['analysis'].get('review_team')


@pytest.mark.parametrize('checkpoint', ['knowledge', 'human_review'])
def test_team_crash_restart_uses_persisted_mode_and_saved_diagnostics(config, upstream, checkpoint):
    first_app = create_app(config, initialize=True)
    with TestClient(first_app) as first:
        login(first)
        ticket = create_ticket(first, text=CHECKOUT_TEXT)
        graph = first_app.state.review_graph

        class CrashAfterCheckpoint:
            def invoke(self, *args, **kwargs):
                if checkpoint != 'human_review':
                    kwargs['interrupt_after'] = [checkpoint]
                graph.invoke(*args, **kwargs)
                raise SimulatedProcessCrash()

        first_app.state.review_graph = CrashAfterCheckpoint()
        with first_app.state.session_factory() as db:
            user = db.scalar(select(User).where(User.email == 'support@northstar.demo'))
            with pytest.raises(SimulatedProcessCrash):
                services.analyze_ticket(db, db.get(Ticket, ticket['id']), user, first_app, workflow_mode=TEAM)
        with first_app.state.session_factory() as db:
            run = db.scalar(select(WorkflowRun).where(WorkflowRun.ticket_id == ticket['id']))
            assert run.status == 'running' and run.workflow_mode == TEAM
            run_id = run.id
            run.created_at = utcnow() - timedelta(minutes=3)
            db.commit()
        assert len(upstream) == 2
    config.default_workflow_mode = 'standard'
    with TestClient(create_app(config)) as restarted:
        login(restarted)
        response = restarted.post(f'/api/tickets/{ticket["id"]}/resume')
        assert response.status_code == 200, response.text
        recovered = response.json()
        assert recovered['workflow_mode'] == TEAM and recovered['analysis']['review_team']
        assert recovered['status'] == 'awaiting_approval'
        assert len(upstream) == 2, 'Saved diagnostic reads must not run again after restart'
        assert count(restarted, WorkflowRun) == 1
        assert approve(restarted, recovered)['review_pending'] is False
        with restarted.app.state.session_factory() as db:
            run = db.get(WorkflowRun, run_id)
            assert run.status == 'completed'
            assert restarted.app.state.review_graph.get_state(services.graph_config(run)).values['decision']['decision'] == 'action_approved'


def test_team_failure_before_first_checkpoint_resumes_same_mode(client, monkeypatch):
    ticket = create_ticket(client)
    graph = client.app.state.review_graph

    class UnavailableGraph:
        def invoke(self, *args, **kwargs):
            raise RuntimeError('Synthetic failure before team checkpoint creation')

    monkeypatch.setattr(client.app.state, 'review_graph', UnavailableGraph())
    failed = client.post(f'/api/tickets/{ticket["id"]}/analyze', json={'workflow_mode': TEAM})
    assert failed.status_code == 503
    assert client.get(f'/api/tickets/{ticket["id"]}').json()['workflow_mode'] == TEAM
    monkeypatch.setattr(client.app.state, 'review_graph', graph)
    recovered = client.post(f'/api/tickets/{ticket["id"]}/resume')
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()['workflow_mode'] == TEAM
    assert recovered.json()['analysis']['review_team']
    assert count(client, WorkflowRun) == 1


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_action_feedback_is_persisted_in_audit_and_checkpoint(client, decision):
    ticket = analyze_team(client)
    path = f'/api/actions/{ticket["action"]["id"]}/{decision}'
    response = client.post(path, json={'version': ticket['action']['version'], 'reason': '  Checked quoted evidence.  '})
    assert response.status_code == 200, response.text
    saved = client.get(f'/api/tickets/{ticket["id"]}').json()
    feedback = [e['payload'] for e in saved['events'] if e['event_type'] == 'review_completed']
    assert len(feedback) == 1 and feedback[0]['reason'] == 'Checked quoted evidence.'
    run = current_run(client, ticket)
    assert feedback[0]['run_id'] == run.id and feedback[0]['workflow_mode'] == TEAM
    checkpoint = client.app.state.review_graph.get_state(services.graph_config(run))
    assert checkpoint.values['decision']['reason'] == feedback[0]['reason']
    assert checkpoint.values['decision']['reviewer_id']
    assert saved['analysis']['review_team'] == ticket['analysis']['review_team']
    if decision == 'approve':
        assert client.post(path, json={'version': ticket['action']['version'], 'reason': 'Replay'}).status_code == 200
        assert len([e for e in client.get(f'/api/tickets/{ticket["id"]}').json()['events'] if e['event_type'] == 'review_completed']) == 1


def test_review_feedback_has_input_limits_and_no_effect_before_valid_review(client):
    ticket = analyze_team(client, text='RetailBridge 3.8 receipt printer error P-102 at one store.')
    path = f'/api/tickets/{ticket["id"]}/review'
    assert client.post(path, json={'decision': 'accepted', 'reason': 'x' * 1001}).status_code == 422
    assert client.get(f'/api/tickets/{ticket["id"]}').json()['review_pending'] is True
    response = client.post(path, json={'decision': 'rejected', 'reason': 'Need an exact reproduction.'})
    assert response.status_code == 200 and response.json()['review_pending'] is False
    assert response.json()['status'] == 'awaiting_clarification'
    assert any(e['event_type'] == 'review_completed' and e['payload']['reason'] == 'Need an exact reproduction.' for e in response.json()['events'])


def test_team_dashboard_and_csv_count_runs_and_keep_feedback_tenant_scoped(client):
    ticket = analyze_team(client, text='Receipt printer failed with P-102 at one store.')
    first = ticket['analysis']['review_team']
    response = client.post(f'/api/tickets/{ticket["id"]}/clarify', json={'text': 'The product is RetailBridge 3.8.'})
    assert response.status_code == 200, response.text
    latest = response.json()['analysis']['review_team']
    assert client.post(f'/api/tickets/{ticket["id"]}/review', json={'decision': 'accepted', 'reason': 'Northstar private feedback'}).status_code == 200
    # A standard run must not inflate the review-team counters.
    other = create_ticket(client)
    assert client.post(f'/api/tickets/{other["id"]}/analyze', json={'workflow_mode': 'standard'}).status_code == 200
    dashboard = client.get('/api/dashboard').json()['review_team']
    assert dashboard['tickets'] == 1 and dashboard['runs'] == 2
    assert dashboard['blocked'] == sum(t['status'] == 'needs_review' for t in (first, latest))
    assert dashboard['disagreements'] == sum(len(t['disagreements']) for t in (first, latest))
    assert dashboard['human_decisions'] == {'clarified': 1, 'accepted': 1}
    assert sum(dashboard['by_role'].values()) == dashboard['disagreements']
    exported = client.get('/api/reports/export.csv')
    rows = list(csv.DictReader(io.StringIO(exported.text.lstrip('\ufeff'))))
    row = next(r for r in rows if r['ticket_id'] == ticket['id'])
    assert row['workflow_mode'] == TEAM and row['team_review_status'] == latest['status']
    assert int(row['team_disagreements']) == len(latest['disagreements'])
    login(client, 'support@contoso.demo')
    dashboard = client.get('/api/dashboard').json()['review_team']
    assert dashboard['tickets'] == dashboard['runs'] == dashboard['disagreements'] == 0
    assert dashboard['human_decisions'] == {}
    assert ticket['id'] not in client.get('/api/reports/export.csv').text
    assert client.get(f'/api/tickets/{ticket["id"]}').status_code == 404
