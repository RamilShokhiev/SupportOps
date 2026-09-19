from alembic import context

from backend.app.config import settings
from backend.app.database import make_engine
from backend.app.models import Base


def configure(connection=None):
    options = dict(target_metadata=Base.metadata, compare_type=True)
    # LangGraph owns and versions its checkpoint tables independently.
    options['include_object'] = lambda obj, name, kind, reflected, compare_to: not (
        kind == 'table' and name.startswith('checkpoint')
    )
    if connection is None:
        context.configure(url=settings.database_url, literal_binds=True, dialect_opts={'paramstyle': 'named'}, **options)
    else:
        context.configure(connection=connection, **options)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    configure()
elif context.config.attributes.get('connection') is not None:
    configure(context.config.attributes['connection'])
else:
    engine = make_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            configure(connection)
    finally:
        engine.dispose()
