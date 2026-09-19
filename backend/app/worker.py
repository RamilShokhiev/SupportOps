import logging
import time

from sqlalchemy import select, update

from .config import settings
from .database import make_engine, make_session_factory
from .models import DocumentVersion

logger = logging.getLogger(__name__)


def index_pending(session_factory, config, limit=20):
    from .retrieval import embed_texts
    processed = 0
    for _ in range(limit):
        with session_factory() as db:
            doc_id = db.scalar(select(DocumentVersion.id).where(DocumentVersion.status == 'pending').order_by(DocumentVersion.created_at).limit(1))
            if not doc_id:
                break
            claimed = db.execute(update(DocumentVersion).where(DocumentVersion.id == doc_id, DocumentVersion.status == 'pending').values(status='indexing'))
            db.commit()
            if claimed.rowcount != 1:
                continue
            doc = db.get(DocumentVersion, doc_id)
            try:
                doc.embedding = embed_texts([doc.title + '\n' + doc.content], config)[0]
                if len(doc.embedding) != 256:
                    raise ValueError('Embedding dimensions must equal 256 for this schema')
                doc.embedding_model = 'demo-hash-256' if config.embedding_provider == 'demo' else config.embedding_model
                doc.status = 'ready'
                doc.error = None
            except Exception as exc:
                # Deliberately keep provider response text and credentials out of persisted errors.
                doc.status = 'error'
                doc.error = f'Indexing failed ({type(exc).__name__}); inspect provider configuration.'
            db.commit()
            processed += 1
    return processed


def main():
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    # Single worker deployment: recover interrupted, side-effect-free embedding jobs on startup.
    with factory() as db:
        db.execute(update(DocumentVersion).where(DocumentVersion.status == 'indexing').values(status='pending'))
        db.commit()
    while True:
        try:
            index_pending(factory, settings)
        except Exception:
            logger.error('Worker iteration failed; retrying next interval')
        time.sleep(settings.worker_poll_seconds)


if __name__ == '__main__':
    main()
