from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def make_engine(url):
    engine = create_engine(url, pool_pre_ping=True, connect_args={'check_same_thread': False, 'timeout': 30} if url.startswith('sqlite') else {})
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def sqlite_pragmas(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
    return engine


def make_session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
