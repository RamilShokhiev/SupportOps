import json
from datetime import timedelta

from sqlalchemy import select

from .models import AuditEvent, DocumentVersion, Organization, Ticket, User, utcnow
from .security import hash_password


def seed_demo(db, settings):
    if not settings.seed_demo:
        return
    for org_id, org_name in [('northstar', 'Northstar Retail'), ('contoso', 'Contoso Markets')]:
        if db.get(Organization, org_id) is None:
            db.add(Organization(id=org_id, name=org_name))
    db.flush()
    for email, name, org_id, role in [('support@northstar.demo', 'Alex Morgan', 'northstar', 'admin'), ('viewer@northstar.demo', 'Jamie Lee', 'northstar', 'viewer'), ('support@contoso.demo', 'Deniz Kaya', 'contoso', 'agent')]:
        if not db.scalar(select(User).where(User.email == email)):
            db.add(User(email=email, name=name, organization_id=org_id, role=role, password_hash=hash_password(settings.demo_password)))
    db.flush()
    manifest_path = settings.data_dir / 'knowledge' / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        if isinstance(manifest, dict):
            manifest = manifest['documents']
        for entry in manifest:
            organizations = ['northstar', 'contoso'] if entry.get('organization', 'both') == 'both' else [entry['organization']]
            for org_id in organizations:
                exists = db.scalar(select(DocumentVersion).where(DocumentVersion.organization_id == org_id, DocumentVersion.document_key == entry['document_key'], DocumentVersion.version == entry.get('version', 1)))
                if exists:
                    continue
                content = (settings.data_dir / 'knowledge' / entry['filename']).read_text(encoding='utf-8-sig')
                db.add(DocumentVersion(organization_id=org_id, document_key=entry['document_key'], title=entry['title'], version=entry.get('version', 1), product=entry.get('product', 'RetailBridge'), min_version=entry['min_version'], max_version=entry['max_version'], conflict_group=entry.get('conflict_group'), content=content, status='pending'))
    for org_id in ['northstar', 'contoso']:
        if db.scalar(select(Ticket.id).where(Ticket.organization_id == org_id).limit(1)):
            continue
        user = db.scalar(select(User).where(User.organization_id == org_id, User.role != 'viewer'))
        examples = [('Checkout blocked after 3.8 rollout', 'After upgrading RetailBridge to 3.8, three stores cannot complete checkout. Error E-214.', 'en'), ('Ошибка синхронизации остатков', 'RetailBridge 3.8: ошибка E-409 при синхронизации остатков в двух магазинах.', 'ru'), ('More details needed', 'Our registers cannot complete checkout. Please help.', 'en')]
        for subject, text, language in examples:
            ticket = Ticket(organization_id=org_id, created_by=user.id, subject=subject, text=text, language=language, due_at=utcnow() + timedelta(hours=8))
            db.add(ticket)
            db.flush()
            db.add(AuditEvent(organization_id=org_id, ticket_id=ticket.id, actor_id=user.id, event_type='ticket_created', payload={'synthetic': True}))
    db.commit()
