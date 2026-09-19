"""HTTP scenarios with isolated stores, durable checkpoints, and no external writes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Lock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import integrations, services
from app.config import Settings
from app.main import create_app
from app.models import Approval, DemoIssue, Execution, ProposedAction, Ticket, User, WorkflowRun, utcnow


ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_TEXT = 'After upgrading RetailBridge to 3.8, three stores cannot complete payment. Error E-214.'


@pytest.fixture
def upstream(monkeypatch):
    """Exercise the real diagnostic adapter, including its HTTP retry boundary."""
    calls = []
    lock = Lock()
    original_client = httpx.Client

    def respond(request):
        assert request.url.host == 'retailbridge.test', 'Unexpected outbound HTTP request'
        with lock:
            calls.append(request)
        mode = request.url.params.get('mode', 'normal')
        if mode == 'timeout':
            raise httpx.ReadTimeout('Synthetic timeout', request=request)
        if mode == 'error':
            return httpx.Response(503, json={'detail': 'Synthetic outage'})
        if request.url.path == '/service-status':
            service = request.url.params['service']
            version = request.url.params['version']
            return httpx.Response(200, json={
                'service': service, 'version': version, 'synthetic': True,
                'status': 'degraded' if service == 'checkout' and version == '3.8' else 'operational',
            })
        assert request.url.path == '/recent-changes'
        return httpx.Response(200, json={'changes': [], 'synthetic': True})

    def client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(respond)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(integrations.httpx, 'Client', client)
    return calls


@pytest.fixture
def config(tmp_path, upstream):
    return Settings(
        _env_file=None, database_url=f'sqlite:///{(tmp_path / "app.db").as_posix()}',
        checkpoint_sqlite_path=str(tmp_path / 'checkpoints.db'), data_dir=ROOT / 'data',
        frontend_dist=tmp_path / 'no-ui', mode='demo', llm_provider='demo',
        embedding_provider='demo', issue_provider='demo', demo_issue_mode='normal',
        retailbridge_url='http://retailbridge.test', seed_demo=True, cookie_secure=False,
        langfuse_enabled=False, demo_password='demo-supportops',
    )


def login(client, email='support@northstar.demo'):
    response = client.post('/api/auth/login', json={'email': email, 'password': 'demo-supportops'})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def client(config):
    with TestClient(create_app(config, initialize=True)) as client:
        login(client)
        yield client


def create_ticket(client, text=CHECKOUT_TEXT, **overrides):
    response = client.post('/api/tickets', json={
        'subject': 'Synthetic scenario', 'text': text, 'language': 'en', **overrides,
    })
    assert response.status_code == 201, response.text
    return response.json()


def analyze_ticket(client, **overrides):
    ticket = create_ticket(client, **overrides)
    response = client.post(f'/api/tickets/{ticket["id"]}/analyze')
    assert response.status_code == 200, response.text
    return response.json()


def action_request(client, action, operation, **body):
    return client.post(f'/api/actions/{action["id"]}/{operation}', json={
        'version': action['version'], **body,
    })


def approve(client, ticket):
    response = action_request(client, ticket['action'], 'approve')
    assert response.status_code == 200, response.text
    return response.json()


def count(client, model):
    with client.app.state.session_factory() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_happy_path_requires_review_and_escalation_is_not_resolution(client):
    ticket = analyze_ticket(client)
    assert ticket['status'] == 'awaiting_approval'
    assert ticket['category'] == 'Incident'
    assert ticket['queue'] == 'Technical Support'
    assert ticket['priority'] == 'P1'
    assert datetime.fromisoformat(ticket['due_at']) - datetime.fromisoformat(ticket['created_at']) == timedelta(hours=1)
    assert ticket['analysis']['next_step'] == 'escalate'
    assert ticket['analysis']['sources']
    assert ticket['reviewed_at'] is None and ticket['resolved_at'] is None
    assert client.post(f'/api/tickets/{ticket["id"]}/resolve').status_code == 409

    ticket = approve(client, ticket)
    assert ticket['reviewed_at'] is not None
    assert count(client, DemoIssue) == 0
    result = action_request(client, ticket['action'], 'execute')
    assert result.status_code == 200, result.text
    ticket = result.json()
    assert ticket['status'] == 'escalated' and ticket['resolved_at'] is None
    assert ticket['action']['execution']['status'] == 'succeeded'
    assert ticket['action']['execution']['mode'] == 'demo'
    issue = client.get(ticket['action']['execution']['url'])
    assert issue.status_code == 200 and 'SYNTHETIC ISSUE' in issue.text
    assert count(client, Execution) == count(client, DemoIssue) == 1
    assert client.get('/api/dashboard').json()['executed'] == 1
    resolved = client.post(f'/api/tickets/{ticket["id"]}/resolve').json()
    assert resolved['status'] == 'resolved' and resolved['resolved_at']


def test_missing_fields_clarify_then_create_a_new_review_run(client, upstream):
    ticket = analyze_ticket(client, text='Payment failed at three stores. E-214.')
    assert ticket['status'] == 'awaiting_clarification' and ticket['action'] is None
    assert ticket['analysis']['fields']['product'] is None
    assert ticket['analysis']['fields']['version'] is None
    assert ticket['analysis']['sources'] == [] and upstream == []
    response = client.post(f'/api/tickets/{ticket["id"]}/clarify', json={'text': 'This is RetailBridge 3.8.'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'awaiting_approval'
    with client.app.state.session_factory() as db:
        runs = db.scalars(select(WorkflowRun).where(WorkflowRun.ticket_id == ticket['id']).order_by(WorkflowRun.created_at)).all()
        assert [run.status for run in runs] == ['completed', 'waiting']


def test_answer_branch_requires_human_acceptance_before_resolution(client):
    ticket = analyze_ticket(client, text='RetailBridge 3.8 receipt printer failed with P-102 at one store.')
    assert ticket['status'] == 'ready' and ticket['action'] is None
    assert client.post(f'/api/tickets/{ticket["id"]}/resolve').status_code == 409
    response = client.post(f'/api/tickets/{ticket["id"]}/review', json={'decision': 'accepted'})
    assert response.status_code == 200 and response.json()['reviewed_at']
    assert client.post(f'/api/tickets/{ticket["id"]}/resolve').json()['status'] == 'resolved'
    assert count(client, Execution) == count(client, DemoIssue) == 0


def test_clarification_does_not_approve_the_next_draft_or_reset_first_review_sla(client):
    ticket = analyze_ticket(client, text='Receipt printer failed with P-102 at one store.')
    assert ticket['status'] == 'awaiting_clarification' and ticket['reviewed_at'] is None
    response = client.post(f'/api/tickets/{ticket["id"]}/clarify', json={'text': 'This is RetailBridge 3.8.'})
    assert response.status_code == 200, response.text
    ticket = response.json()
    assert ticket['status'] == 'ready' and ticket['review_pending'] is True
    first_review = ticket['reviewed_at']
    assert first_review is not None
    assert client.post(f'/api/tickets/{ticket["id"]}/resolve').status_code == 409
    response = client.post(f'/api/tickets/{ticket["id"]}/review', json={'decision': 'accepted'})
    assert response.status_code == 200 and response.json()['review_pending'] is False
    assert response.json()['reviewed_at'] == first_review
    assert client.post(f'/api/tickets/{ticket["id"]}/resolve').json()['status'] == 'resolved'


@pytest.mark.parametrize('mode', ['timeout', 'error'])
def test_diagnostic_http_failures_retry_and_clarify_without_healthy_facts(client, upstream, mode):
    ticket = analyze_ticket(client, diagnostic_mode=mode)
    assert ticket['status'] == 'awaiting_clarification' and ticket['action'] is None
    diagnostics = ticket['analysis']['diagnostics']
    assert len(upstream) == 4
    assert len(diagnostics) == 2
    assert all(item['status'] == mode and item['attempts'] == 2 and item['data'] is None for item in diagnostics)
    assert all(fact['kind'] != 'api' for fact in ticket['analysis']['facts'])
    assert client.get('/api/dashboard').json()['api_errors'] == 2


def test_conflicting_documents_require_clarification_via_api(client):
    ticket = analyze_ticket(client, text='Please plan a RetailBridge 3.9 upgrade next month.')
    assert ticket['status'] == 'awaiting_clarification' and ticket['action'] is None
    assert any('conflict' in warning.lower() for warning in ticket['analysis']['warnings'])
    assert {source['document_key'] for source in ticket['analysis']['sources']} >= {'upgrade-v39-a', 'upgrade-v39-b'}
    assert count(client, Execution) == 0


def test_org_isolation_covers_tickets_actions_documents_issues_and_reports(client):
    ticket = approve(client, analyze_ticket(client, subject='Northstar private sentinel'))
    ticket = action_request(client, ticket['action'], 'execute').json()
    document = next(doc for doc in client.get('/api/documents').json() if doc['document_key'] == 'data-export')
    login(client, 'support@contoso.demo')
    assert ticket['id'] not in {row['id'] for row in client.get('/api/tickets').json()}
    assert client.get(f'/api/tickets/{ticket["id"]}').status_code == 404
    for operation in ['analyze', 'resume', 'resolve']:
        assert client.post(f'/api/tickets/{ticket["id"]}/{operation}').status_code == 404
    for operation in ['approve', 'reject', 'execute']:
        assert action_request(client, ticket['action'], operation).status_code == 404
    assert client.post(f'/api/actions/{ticket["action"]["id"]}/reconcile').status_code == 404
    assert client.get(f'/api/documents/{document["id"]}').status_code == 404
    assert 'data-export' not in {doc['document_key'] for doc in client.get('/api/documents').json()}
    assert client.get(ticket['action']['execution']['url']).status_code == 404
    assert 'Northstar private sentinel' not in client.get('/api/reports/export.csv').text
    dashboard = client.get('/api/dashboard').json()
    assert dashboard['executed'] == 0
    assert ticket['id'] not in {event['ticket_id'] for event in dashboard['recent_activity']}


def test_other_tenant_knowledge_cannot_ground_analysis(client):
    text = 'Please export Northstar transaction CSV data from RetailBridge 3.8.'
    northstar = analyze_ticket(client, text=text)
    assert 'data-export' in {source['document_key'] for source in northstar['analysis']['sources']}
    login(client, 'support@contoso.demo')
    contoso = analyze_ticket(client, text=text)
    assert 'data-export' not in {source['document_key'] for source in contoso['analysis']['sources']}
    assert not any('Northstar transaction export' in fact.get('source', '') for fact in contoso['analysis']['facts'])


def test_viewer_can_read_but_cannot_mutate_or_approve(client):
    ticket = analyze_ticket(client)
    login(client, 'viewer@northstar.demo')
    assert client.get(f'/api/tickets/{ticket["id"]}').status_code == 200
    assert client.get('/api/documents').status_code == 200
    assert client.get('/api/dashboard').status_code == 200
    assert client.post('/api/tickets', json={'subject': 'Blocked', 'text': CHECKOUT_TEXT}).status_code == 403
    for operation in ['analyze', 'resume', 'resolve']:
        assert client.post(f'/api/tickets/{ticket["id"]}/{operation}').status_code == 403
    for operation in ['approve', 'reject', 'execute']:
        assert action_request(client, ticket['action'], operation).status_code == 403
    assert action_request(client, ticket['action'], 'edit', title='Updated issue', body='Updated issue body').status_code == 403
    assert client.post(f'/api/actions/{ticket["action"]["id"]}/reconcile').status_code == 403
    assert count(client, Approval) == count(client, Execution) == count(client, DemoIssue) == 0


def test_authorization_is_rechecked_after_approval(client):
    ticket = approve(client, analyze_ticket(client))
    with client.app.state.session_factory() as db:
        user = db.scalar(select(User).where(User.email == 'support@northstar.demo'))
        user.role = 'viewer'
        db.commit()
    assert action_request(client, ticket['action'], 'execute').status_code == 403
    assert count(client, Execution) == count(client, DemoIssue) == 0


def test_unapproved_and_rejected_actions_cannot_execute(client):
    ticket = analyze_ticket(client)
    assert action_request(client, ticket['action'], 'execute').status_code == 409
    assert action_request(client, ticket['action'], 'reject').status_code == 200
    assert action_request(client, ticket['action'], 'execute').status_code == 409
    assert count(client, Execution) == count(client, DemoIssue) == 0


def test_edit_invalidates_approval_and_stale_requests(client):
    ticket = approve(client, analyze_ticket(client))
    original = ticket['action']
    edited = action_request(client, original, 'edit', title='Human revised title', body='Human revised issue details.')
    assert edited.status_code == 200, edited.text
    action = edited.json()['action']
    assert action['version'] == original['version'] + 1
    assert action['status'] == 'pending' and action['approved_version'] is None
    for operation in ['approve', 'execute']:
        assert action_request(client, original, operation).status_code == 409
    assert action_request(client, action, 'execute').status_code == 409
    assert count(client, Execution) == 0
    assert action_request(client, action, 'approve').status_code == 200
    executed = action_request(client, action, 'execute')
    assert executed.status_code == 200 and executed.json()['status'] == 'escalated'
    issue = client.get(executed.json()['action']['execution']['url']).text
    assert 'Human revised title' in issue and 'Human revised issue details.' in issue


def test_content_hash_is_rechecked_immediately_before_execution(client):
    ticket = approve(client, analyze_ticket(client))
    with client.app.state.session_factory() as db:
        action = db.get(ProposedAction, ticket['action']['id'])
        action.body = 'Changed outside the approval API'
        db.commit()
    assert action_request(client, ticket['action'], 'execute').status_code == 409
    assert count(client, Execution) == count(client, DemoIssue) == 0


def test_repeat_execution_returns_existing_result_without_second_write(client):
    ticket = approve(client, analyze_ticket(client))
    action = ticket['action']
    first = action_request(client, action, 'execute')
    assert first.status_code == 200
    second = action_request(client, action, 'execute')
    assert second.status_code == 200
    assert first.json()['action']['execution'] == second.json()['action']['execution']
    assert count(client, Execution) == count(client, DemoIssue) == 1
    assert client.get('/api/dashboard').json()['executed'] == 1


def test_overlapping_execute_requests_have_one_durable_claim(client, monkeypatch):
    ticket = approve(client, analyze_ticket(client))
    action = ticket['action']
    entered = Event()
    release = Event()
    original = services.create_issue
    writes = []

    def delayed_write(*args, **kwargs):
        writes.append(args[1].id)
        entered.set()
        assert release.wait(timeout=10), 'Concurrent request never released outbound write'
        return original(*args, **kwargs)

    monkeypatch.setattr(services, 'create_issue', delayed_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(action_request, client, action, 'execute')
        try:
            assert entered.wait(timeout=10), 'First execution never reached outbound boundary'
            second = executor.submit(action_request, client, action, 'execute').result(timeout=10)
            assert second.status_code == 200, second.text
            assert second.json()['action']['execution']['status'] == 'executing'
            assert count(client, Execution) == 1
        finally:
            release.set()
        assert first.result(timeout=10).json()['status'] == 'escalated'
    assert len(writes) == 1 and count(client, DemoIssue) == 1


@pytest.mark.parametrize('mode,created,final_status', [
    ('timeout_after', 1, 'escalated'), ('timeout_before', 0, 'needs_review'),
])
def test_uncertain_outcome_reconciles_without_repeating_post(client, mode, created, final_status):
    client.app.state.settings.demo_issue_mode = mode
    ticket = approve(client, analyze_ticket(client))
    response = action_request(client, ticket['action'], 'execute')
    assert response.status_code == 200, response.text
    ticket = response.json()
    assert ticket['status'] == 'needs_review'
    assert ticket['action']['execution']['external_id'] is None
    assert count(client, DemoIssue) == created
    client.app.state.settings.demo_issue_mode = 'normal'
    assert action_request(client, ticket['action'], 'execute').json()['status'] == 'needs_review'
    response = client.post(f'/api/actions/{ticket["action"]["id"]}/reconcile')
    assert response.status_code == 200 and response.json()['status'] == final_status
    assert count(client, Execution) == 1 and count(client, DemoIssue) == created
    assert client.post(f'/api/actions/{ticket["action"]["id"]}/reconcile').json()['status'] == final_status
    assert client.get('/api/dashboard').json()['executed'] == created


def test_confirmed_issue_failure_is_logged_and_not_silently_retried(client):
    client.app.state.settings.demo_issue_mode = 'error'
    ticket = approve(client, analyze_ticket(client))
    response = action_request(client, ticket['action'], 'execute')
    assert response.status_code == 200 and response.json()['status'] == 'failed'
    assert response.json()['action']['execution']['status'] == 'failed'
    client.app.state.settings.demo_issue_mode = 'normal'
    assert action_request(client, ticket['action'], 'execute').json()['status'] == 'failed'
    assert count(client, Execution) == 1 and count(client, DemoIssue) == 0


class SimulatedProcessCrash(BaseException):
    """Bypass recovery handlers as a killed worker would."""


@pytest.mark.parametrize('checkpoint', ['diagnostics', 'human_review'])
def test_crash_after_durable_checkpoint_resumes_with_new_app(config, upstream, checkpoint):
    first_app = create_app(config, initialize=True)
    with TestClient(first_app) as first:
        login(first)
        ticket = create_ticket(first)
        graph = first_app.state.graph

        class CrashAfterCheckpoint:
            def invoke(self, *args, **kwargs):
                if checkpoint == 'diagnostics':
                    kwargs['interrupt_after'] = ['diagnostics']
                graph.invoke(*args, **kwargs)
                raise SimulatedProcessCrash()

        first_app.state.graph = CrashAfterCheckpoint()
        with first_app.state.session_factory() as db:
            user = db.scalar(select(User).where(User.email == 'support@northstar.demo'))
            with pytest.raises(SimulatedProcessCrash):
                services.analyze_ticket(db, db.get(Ticket, ticket['id']), user, first_app)
        with first_app.state.session_factory() as db:
            run = db.scalar(select(WorkflowRun).where(WorkflowRun.ticket_id == ticket['id']))
            assert run.status == 'running'
            run_id = run.id
            run.created_at = utcnow() - timedelta(minutes=3)
            db.commit()
        assert len(upstream) == 2
    with TestClient(create_app(config)) as restarted:
        login(restarted)
        response = restarted.post(f'/api/tickets/{ticket["id"]}/resume')
        assert response.status_code == 200, response.text
        recovered = response.json()
        assert recovered['status'] == 'awaiting_approval'
        assert len(upstream) == 2, 'Persisted analysis and diagnostics must not rerun'
        with restarted.app.state.session_factory() as db:
            assert db.get(WorkflowRun, run_id).status == 'waiting'
            assert db.scalar(select(func.count()).select_from(WorkflowRun)) == 1
        assert restarted.post(f'/api/tickets/{ticket["id"]}/resume').status_code == 409
        assert action_request(restarted, recovered['action'], 'approve').status_code == 200
        with restarted.app.state.session_factory() as db:
            run = db.get(WorkflowRun, run_id)
            assert run.status == 'completed'
            assert restarted.app.state.graph.get_state(services.graph_config(run)).values['decision']['decision'] == 'action_approved'


def test_failure_before_first_checkpoint_can_resume(client, monkeypatch):
    ticket = create_ticket(client)
    graph = client.app.state.graph

    class UnavailableGraph:
        def invoke(self, *args, **kwargs):
            raise RuntimeError('Synthetic failure before checkpoint creation')

    monkeypatch.setattr(client.app.state, 'graph', UnavailableGraph())
    failed = client.post(f'/api/tickets/{ticket["id"]}/analyze')
    assert failed.status_code == 503
    assert client.get(f'/api/tickets/{ticket["id"]}').json()['status'] == 'failed'
    monkeypatch.setattr(client.app.state, 'graph', graph)
    recovered = client.post(f'/api/tickets/{ticket["id"]}/resume')
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()['status'] == 'awaiting_approval'
    assert count(client, WorkflowRun) == 1


def test_crash_after_external_write_reconciles_on_restart(config):
    first_app = create_app(config, initialize=True)
    original = services.create_issue
    with TestClient(first_app) as first:
        login(first)
        ticket = approve(first, analyze_ticket(first))

        def write_then_crash(*args, **kwargs):
            original(*args, **kwargs)
            raise SimulatedProcessCrash()

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(services, 'create_issue', write_then_crash)
            with first_app.state.session_factory() as db:
                user = db.scalar(select(User).where(User.email == 'support@northstar.demo'))
                action = db.get(ProposedAction, ticket['action']['id'])
                with pytest.raises(SimulatedProcessCrash):
                    services.execute_action(db, action, db.get(Ticket, ticket['id']), user, action.version, first_app)
        assert count(first, DemoIssue) == 1
    with TestClient(create_app(config)) as restarted:
        login(restarted)
        repeated = action_request(restarted, ticket['action'], 'execute')
        assert repeated.status_code == 200
        assert repeated.json()['action']['execution']['status'] == 'executing'
        reconciled = restarted.post(f'/api/actions/{ticket["action"]["id"]}/reconcile')
        assert reconciled.status_code == 200 and reconciled.json()['status'] == 'escalated'
        assert count(restarted, Execution) == count(restarted, DemoIssue) == 1


def test_active_workflow_cannot_be_resumed_before_recovery_window(client):
    ticket = create_ticket(client)
    with client.app.state.session_factory() as db:
        db.get(Ticket, ticket['id']).status = 'analyzing'
        db.add(WorkflowRun(ticket_id=ticket['id'], organization_id='northstar'))
        db.commit()
    response = client.post(f'/api/tickets/{ticket["id"]}/resume')
    assert response.status_code == 409 and 'still running' in response.json()['detail']
