"""Persist the graph choice so review and recovery use the original workflow.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('workflow_runs', sa.Column('workflow_mode', sa.String(32), nullable=False, server_default='standard'))


def downgrade():
    with op.batch_alter_table('workflow_runs') as batch:
        batch.drop_column('workflow_mode')
