"""Apply versioned schema migrations and initialize the synthetic local dataset."""
from alembic import command
from alembic.config import Config

from .config import PROJECT_ROOT, Settings, settings
from .database import make_engine, make_session_factory
from .seed import seed_demo
from .worker import index_pending
from .workflow import persistent_graph


def migrate(config: Settings):
    migration_config = Config(str(PROJECT_ROOT / 'alembic.ini'))
    engine = make_engine(config.database_url)
    try:
        with engine.begin() as connection:
            migration_config.attributes['connection'] = connection
            command.upgrade(migration_config, 'head')
    finally:
        engine.dispose()


def bootstrap(config: Settings = settings):
    if config.embedding_dimensions != 256:
        raise ValueError('The schema requires 256-dimensional embeddings')
    migrate(config)
    engine = make_engine(config.database_url)
    try:
        factory = make_session_factory(engine)
        with factory() as db:
            seed_demo(db, config)
        if config.embedding_provider == 'demo':
            index_pending(factory, config, limit=100)
    finally:
        engine.dispose()
    with persistent_graph(config):
        pass


if __name__ == '__main__':
    bootstrap()
    print('SupportOps schema, checkpoints and configured seed are ready.')
