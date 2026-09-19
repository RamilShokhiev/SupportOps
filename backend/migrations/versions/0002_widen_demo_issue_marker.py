"""Store the complete HTML execution marker including UUID and content hash.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('demo_issues') as batch:
        batch.alter_column('marker', existing_type=sa.String(100), type_=sa.String(160), existing_nullable=False)


def downgrade():
    with op.batch_alter_table('demo_issues') as batch:
        batch.alter_column('marker', existing_type=sa.String(160), type_=sa.String(100), existing_nullable=False)
