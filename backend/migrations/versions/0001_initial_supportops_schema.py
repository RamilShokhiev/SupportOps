"""Initial SupportOps schema

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.create_table('organizations',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('demo_issues',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('marker', sa.String(length=100), nullable=False),
    sa.Column('organization_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('marker')
    )
    op.create_table('document_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=64), nullable=False),
    sa.Column('document_key', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('product', sa.String(length=80), nullable=False),
    sa.Column('min_version', sa.String(length=30), nullable=False),
    sa.Column('max_version', sa.String(length=30), nullable=False),
    sa.Column('conflict_group', sa.String(length=80), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', sa.JSON().with_variant(Vector(256), 'postgresql'), nullable=True),
    sa.Column('embedding_model', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'document_key', 'version')
    )
    op.create_index(op.f('ix_document_versions_organization_id'), 'document_versions', ['organization_id'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=64), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('role', sa.String(length=30), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_users_organization_id'), 'users', ['organization_id'], unique=False)
    op.create_table('auth_sessions',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('token_hash')
    )
    op.create_table('tickets',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=64), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('language', sa.String(length=2), nullable=False),
    sa.Column('diagnostic_mode', sa.String(length=20), nullable=False),
    sa.Column('category', sa.String(length=30), nullable=False),
    sa.Column('queue', sa.String(length=40), nullable=False),
    sa.Column('priority', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('analysis', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tickets_organization_id'), 'tickets', ['organization_id'], unique=False)
    op.create_table('audit_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=64), nullable=False),
    sa.Column('ticket_id', sa.String(length=36), nullable=True),
    sa.Column('actor_id', sa.String(length=36), nullable=True),
    sa.Column('event_type', sa.String(length=60), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_events_organization_id'), 'audit_events', ['organization_id'], unique=False)
    op.create_index(op.f('ix_audit_events_ticket_id'), 'audit_events', ['ticket_id'], unique=False)
    op.create_table('proposed_actions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=64), nullable=False),
    sa.Column('ticket_id', sa.String(length=36), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('repository', sa.String(length=200), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('approved_version', sa.Integer(), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ticket_id')
    )
    op.create_index(op.f('ix_proposed_actions_organization_id'), 'proposed_actions', ['organization_id'], unique=False)
    op.create_table('workflow_runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=64), nullable=False),
    sa.Column('ticket_id', sa.String(length=36), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_workflow_runs_organization_id'), 'workflow_runs', ['organization_id'], unique=False)
    op.create_index(op.f('ix_workflow_runs_ticket_id'), 'workflow_runs', ['ticket_id'], unique=False)
    op.create_table('approvals',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('action_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('decision', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['action_id'], ['proposed_actions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_approvals_action_id'), 'approvals', ['action_id'], unique=False)
    op.create_table('executions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('action_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('external_id', sa.String(length=100), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['action_id'], ['proposed_actions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('action_id')
    )


def downgrade():
    op.drop_table('executions')
    op.drop_index(op.f('ix_approvals_action_id'), table_name='approvals')
    op.drop_table('approvals')
    op.drop_index(op.f('ix_workflow_runs_ticket_id'), table_name='workflow_runs')
    op.drop_index(op.f('ix_workflow_runs_organization_id'), table_name='workflow_runs')
    op.drop_table('workflow_runs')
    op.drop_index(op.f('ix_proposed_actions_organization_id'), table_name='proposed_actions')
    op.drop_table('proposed_actions')
    op.drop_index(op.f('ix_audit_events_ticket_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_organization_id'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index(op.f('ix_tickets_organization_id'), table_name='tickets')
    op.drop_table('tickets')
    op.drop_table('auth_sessions')
    op.drop_index(op.f('ix_users_organization_id'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_document_versions_organization_id'), table_name='document_versions')
    op.drop_table('document_versions')
    op.drop_table('demo_issues')
    op.drop_table('organizations')
