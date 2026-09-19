import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from backend.app.bootstrap import bootstrap, migrate
from backend.app.config import PROJECT_ROOT, Settings
from backend.app.database import make_engine, make_session_factory
from backend.app.main import create_app
from backend.app.models import DocumentVersion, Organization, Ticket, User


def demo_config(**overrides):
    return Settings(_env_file=None, mode='demo', llm_provider='demo',
                    embedding_provider='demo', issue_provider='demo',
                    langfuse_enabled=False, seed_demo=True, **overrides)


def test_repository_paths_are_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = demo_config()
    assert config.data_dir == PROJECT_ROOT / 'data'
    assert (config.data_dir / 'knowledge' / 'manifest.json').is_file()
    assert config.frontend_dist == PROJECT_ROOT / 'frontend' / 'dist'
    assert config.model_config['env_file'] == str(PROJECT_ROOT / '.env')


def test_container_database_credentials_roundtrip_reserved_characters():
    config = demo_config(postgres_host='db', postgres_password='demo@p:/?#%ass')
    parsed = make_url(config.database_url)
    assert parsed.password == 'demo@p:/?#%ass'
    assert parsed.host == 'db'
    assert parsed.database == 'supportops'


def test_bootstrap_is_repeatable_and_preserves_seeded_data(tmp_path):
    config = demo_config(database_url=f'sqlite:///{(tmp_path / "app.db").as_posix()}',
                         checkpoint_sqlite_path=str(tmp_path / 'checkpoints.db'))
    bootstrap(config)
    engine = make_engine(config.database_url)
    factory = make_session_factory(engine)
    with factory() as db:
        ids = set(db.scalars(select(Ticket.id)))
        assert len(ids) == 6
        assert db.scalar(select(func.count()).select_from(Organization)) == 2
        assert db.scalar(select(func.count()).select_from(User)) == 3
        assert db.scalar(select(func.count()).select_from(DocumentVersion)) == 19
        assert set(db.scalars(select(DocumentVersion.status))) == {'ready'}
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0002'
    bootstrap(config)
    with factory() as db:
        assert set(db.scalars(select(Ticket.id))) == ids
    # Default app startup consumes a migrated database; no create_all shortcut.
    with TestClient(create_app(config)) as client:
        assert client.get('/api/health').json()['database'] == 'sqlite'
        assert client.post('/api/auth/login', json={'email': 'support@northstar.demo', 'password': config.demo_password}).status_code == 200
        assert len(client.get('/api/tickets').json()) == 3
    engine.dispose()


def test_initial_migration_can_roundtrip_on_empty_database(tmp_path):
    config = demo_config(database_url=f'sqlite:///{(tmp_path / "schema.db").as_posix()}')
    migrate(config)
    engine = make_engine(config.database_url)
    migration_config = Config(str(PROJECT_ROOT / 'alembic.ini'))
    with engine.begin() as connection:
        migration_config.attributes['connection'] = connection
        command.check(migration_config)
        command.downgrade(migration_config, 'base')
        command.upgrade(migration_config, 'head')
        command.check(migration_config)
    engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not os.environ.get('SUPPORTOPS_TEST_DATABASE_URL'), reason='Disposable PostgreSQL URL not configured')
def test_postgres_migrations_vector_storage_and_checkpoint_restart():
    """CI supplies a dedicated database. This test does not drop existing data."""
    config = demo_config(database_url=os.environ['SUPPORTOPS_TEST_DATABASE_URL'])
    bootstrap(config)
    bootstrap(config)
    engine = make_engine(config.database_url)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one()
        assert connection.execute(text('SELECT vector_dims(embedding) FROM document_versions WHERE embedding IS NOT NULL LIMIT 1')).scalar_one() == 256
        migration_config = Config(str(PROJECT_ROOT / 'alembic.ini'))
        migration_config.attributes['connection'] = connection
        command.check(migration_config)
    # Missing details require no outbound diagnostics, but persist a full graph interrupt.
    with TestClient(create_app(config)) as client:
        assert client.post('/api/auth/login', json={'email': 'support@northstar.demo', 'password': config.demo_password}).status_code == 200
        ticket = client.post('/api/tickets', json={'subject': 'Postgres checkpoint restart', 'text': 'Our checkout is blocked. Please help.', 'language': 'en'}).json()
        result = client.post(f'/api/tickets/{ticket["id"]}/analyze')
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'awaiting_clarification'
    with TestClient(create_app(config)) as client:
        client.post('/api/auth/login', json={'email': 'support@northstar.demo', 'password': config.demo_password})
        result = client.post(f'/api/tickets/{ticket["id"]}/review', json={'decision': 'accepted'})
        assert result.status_code == 200, result.text
        assert result.json()['reviewed_at'] is not None
    engine.dispose()


@pytest.mark.postgres
@pytest.mark.skipif(not os.environ.get('SUPPORTOPS_TEST_DATABASE_URL'), reason='Disposable PostgreSQL URL not configured')
@pytest.mark.parametrize('issue_mode', ['normal', 'timeout_after'])
def test_postgres_approved_issue_survives_restart_without_duplicate_write(monkeypatch, tmp_path, issue_mode):
    """Exercise VARCHAR constraints, durable execution claims, and marker reconciliation."""
    import httpx

    from backend.app import integrations
    from backend.app.models import AuditEvent, DemoIssue, Execution

    config = demo_config(database_url=os.environ['SUPPORTOPS_TEST_DATABASE_URL'],
                         retailbridge_url='http://retailbridge.test',
                         frontend_dist=tmp_path / 'no-ui', demo_issue_mode=issue_mode)
    bootstrap(config)
    original_client = httpx.Client

    def respond(request):
        assert request.url.host == 'retailbridge.test', 'Unexpected outbound HTTP request'
        if request.url.path == '/service-status':
            return httpx.Response(200, json={
                'service': 'checkout', 'version': '3.8', 'synthetic': True, 'status': 'degraded',
            })
        assert request.url.path == '/recent-changes'
        return httpx.Response(200, json={'changes': [], 'synthetic': True})

    def diagnostic_client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(respond)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(integrations.httpx, 'Client', diagnostic_client)
    login = {'email': 'support@northstar.demo', 'password': config.demo_password}
    with TestClient(create_app(config)) as client:
        assert client.post('/api/auth/login', json=login).status_code == 200
        created = client.post('/api/tickets', json={
            'subject': 'Postgres issue execution and recovery',
            'text': 'After upgrading RetailBridge to 3.8, three stores cannot complete payment. Error E-214.',
            'language': 'en',
        })
        assert created.status_code == 201, created.text
        ticket_id = created.json()['id']
        analyzed = client.post(f'/api/tickets/{ticket_id}/analyze')
        assert analyzed.status_code == 200, analyzed.text
        ticket = analyzed.json()
        assert ticket['status'] == 'awaiting_approval' and ticket['analysis']['sources']
        assert all(item['status'] == 'ok' for item in ticket['analysis']['diagnostics'])
        action = ticket['action']
        action_path = f'/api/actions/{action["id"]}'
        version = {'version': action['version']}
        assert client.post(action_path + '/approve', json=version).status_code == 200
        executed = client.post(action_path + '/execute', json=version)
        assert executed.status_code == 200, executed.text
        expected_status = 'escalated' if issue_mode == 'normal' else 'needs_review'
        assert executed.json()['status'] == expected_status
        execution_id = executed.json()['action']['execution']['id']
        with client.app.state.session_factory() as db:
            execution = db.get(Execution, execution_id)
            marker = integrations.marker_for(execution)
            assert len(marker) > 100, 'Use the full UUID/hash marker that exceeds the original column size'
            assert db.scalar(select(func.count()).select_from(DemoIssue).where(DemoIssue.marker == marker)) == 1

    config.demo_issue_mode = 'normal'
    with TestClient(create_app(config)) as restarted:
        assert restarted.post('/api/auth/login', json=login).status_code == 200
        replay = restarted.post(action_path + '/execute', json=version)
        assert replay.status_code == 200 and replay.json()['status'] == expected_status
        assert replay.json()['action']['execution']['id'] == execution_id
        recovered = restarted.post(action_path + '/reconcile')
        assert recovered.status_code == 200, recovered.text
        ticket = recovered.json()
        assert ticket['status'] == 'escalated' and ticket['resolved_at'] is None
        execution = ticket['action']['execution']
        assert execution['status'] == 'succeeded' and execution['mode'] == 'demo'
        assert execution['external_id'] is not None
        issue = restarted.get(execution['url'])
        assert issue.status_code == 200 and 'SYNTHETIC ISSUE' in issue.text and marker in issue.text
        assert restarted.post(action_path + '/reconcile').json()['action']['execution'] == execution
        assert restarted.post(action_path + '/execute', json=version).json()['action']['execution'] == execution
        with restarted.app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(Execution).where(Execution.action_id == action['id'])) == 1
            assert db.scalar(select(func.count()).select_from(DemoIssue).where(DemoIssue.marker == marker)) == 1
            assert db.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.ticket_id == ticket_id, AuditEvent.event_type == 'execution_succeeded')) == 1
