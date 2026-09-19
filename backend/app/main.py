import csv
import io
import re
import secrets
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from .config import Settings, settings
from .database import make_engine, make_session_factory
from .models import Approval, AuditEvent, AuthSession, Base, DemoIssue, DocumentVersion, Execution, Organization, ProposedAction, Ticket, User, utcnow
from .schemas import ActionEdit, ActionVersion, Clarification, Login, Review, TicketCreate, TicketImport
from .security import action_hash, check_password, token_hash
from .seed import seed_demo
from .services import analyze_ticket, audit, complete_review, document_dict, execute_action, get_action, get_ticket, iso, latest_run, reconcile, ticket_dict
from .worker import index_pending
from .workflow import persistent_graph


def create_app(config: Settings | None = None, initialize=False):
    config = config or settings
    engine = make_engine(config.database_url)
    factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(application):
        if config.embedding_dimensions != 256:
            raise RuntimeError('This migration expects 256-dimensional embeddings')
        if initialize:
            if engine.dialect.name == 'postgresql':
                with engine.begin() as conn:
                    conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
            Base.metadata.create_all(engine)
        with factory() as db:
            seed_demo(db, config)
        if config.embedding_provider == 'demo':
            index_pending(factory, config, limit=100)
        with persistent_graph(config) as graph:
            application.state.graph = graph
            yield
        engine.dispose()

    application = FastAPI(title='SupportOps AI', version='1.0.0', lifespan=lifespan, description='Synthetic RetailBridge support workflow. Human-approved escalation, durable state and tenant isolation.')
    application.state.engine = engine
    application.state.session_factory = factory
    application.state.settings = config
    application.add_middleware(CORSMiddleware, allow_origins=config.allowed_origins, allow_credentials=True, allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

    @application.middleware('http')
    async def browser_boundary(request, call_next):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if origin and origin not in config.allowed_origins:
                return PlainTextResponse('Origin not permitted', status_code=403)
            length = request.headers.get('content-length')
            if length and int(length) > 2_000_000:
                return PlainTextResponse('Request too large', status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        if request.url.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def session():
        with factory() as db:
            yield db

    def current_user(request: Request, db=Depends(session)):
        cookie = request.cookies.get('supportops_session', '')
        auth_session = db.get(AuthSession, token_hash(cookie)) if cookie else None
        if not auth_session or auth_session.expires_at.replace(tzinfo=timezone.utc) < utcnow():
            raise HTTPException(401, 'Sign in to continue')
        user = db.get(User, auth_session.user_id)
        if not user:
            raise HTTPException(401, 'Session is invalid')
        return user

    def writer(user=Depends(current_user)):
        if user.role not in {'agent', 'admin'}:
            raise HTTPException(403, 'An agent or administrator role is required')
        return user

    def admin(user=Depends(current_user)):
        if user.role != 'admin':
            raise HTTPException(403, 'An administrator role is required')
        return user

    def user_view(user, db):
        org = db.get(Organization, user.organization_id)
        return {'id': user.id, 'name': user.name, 'email': user.email, 'role': user.role, 'organization': {'id': org.id, 'name': org.name}}

    attempts = defaultdict(list)

    @application.get('/api/health')
    def health(db=Depends(session)):
        db.execute(text('SELECT 1'))
        return {'status': 'ok', 'mode': config.mode, 'llm_provider': config.llm_provider, 'embedding_provider': config.embedding_provider, 'issue_provider': config.issue_provider, 'database': engine.dialect.name, 'synthetic_diagnostics': True}

    @application.post('/api/auth/login')
    def login(body: Login, request: Request, response: Response, db=Depends(session)):
        key = request.client.host if request.client else 'local'
        now = time.monotonic()
        attempts[key] = [t for t in attempts[key] if now - t < 60]
        if len(attempts[key]) >= 20:
            raise HTTPException(429, 'Too many sign-in attempts. Try again in one minute.')
        attempts[key].append(now)
        user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
        if not user or not check_password(body.password, user.password_hash):
            raise HTTPException(401, 'Email or password is incorrect')
        token = secrets.token_urlsafe(40)
        db.add(AuthSession(token_hash=token_hash(token), user_id=user.id, expires_at=utcnow() + timedelta(hours=config.session_hours)))
        db.commit()
        response.set_cookie('supportops_session', token, httponly=True, secure=config.cookie_secure, samesite='strict', max_age=config.session_hours * 3600, path='/')
        return user_view(user, db)

    @application.get('/api/auth/me')
    def me(user=Depends(current_user), db=Depends(session)):
        return user_view(user, db)

    @application.post('/api/auth/logout')
    def logout(request: Request, response: Response, db=Depends(session)):
        token = request.cookies.get('supportops_session', '')
        auth_session = db.get(AuthSession, token_hash(token))
        if auth_session:
            db.delete(auth_session)
            db.commit()
        response.delete_cookie('supportops_session', path='/')
        return {'status': 'signed_out'}

    @application.get('/api/tickets')
    def tickets(user=Depends(current_user), db=Depends(session)):
        items = db.scalars(select(Ticket).where(Ticket.organization_id == user.organization_id).order_by(Ticket.created_at.desc())).all()
        return [ticket_dict(db, item, detail=False) for item in items]

    def add_ticket(body, user, db):
        if config.mode != 'demo' and body.diagnostic_mode != 'normal':
            raise HTTPException(422, 'Failure simulation is available only in demo mode')
        ticket = Ticket(**body.model_dump(), organization_id=user.organization_id, created_by=user.id, due_at=utcnow() + timedelta(hours=8))
        db.add(ticket)
        db.flush()
        audit(db, ticket, user, 'ticket_created', {'synthetic': True})
        return ticket

    @application.post('/api/tickets', status_code=201)
    def create_ticket(body: TicketCreate, user=Depends(writer), db=Depends(session)):
        ticket = add_ticket(body, user, db)
        db.commit()
        return ticket_dict(db, ticket)

    @application.post('/api/tickets/import', status_code=201)
    def import_tickets(body: TicketImport, user=Depends(writer), db=Depends(session)):
        created = [add_ticket(item, user, db) for item in body.tickets]
        db.commit()
        return [ticket_dict(db, item) for item in created]

    @application.get('/api/tickets/{ticket_id}')
    def get_ticket_api(ticket_id: str, user=Depends(current_user), db=Depends(session)):
        return ticket_dict(db, get_ticket(db, ticket_id, user))

    @application.post('/api/tickets/{ticket_id}/analyze')
    def analyze_api(ticket_id: str, user=Depends(writer), db=Depends(session)):
        return analyze_ticket(db, get_ticket(db, ticket_id, user), user, application)

    @application.post('/api/tickets/{ticket_id}/resume')
    def resume_api(ticket_id: str, user=Depends(writer), db=Depends(session)):
        return analyze_ticket(db, get_ticket(db, ticket_id, user), user, application, resume=True)

    @application.post('/api/tickets/{ticket_id}/clarify')
    def clarify_api(ticket_id: str, body: Clarification, user=Depends(writer), db=Depends(session)):
        ticket = get_ticket(db, ticket_id, user)
        if ticket.status != 'awaiting_clarification':
            raise HTTPException(409, 'Ticket is not awaiting clarification')
        if len(ticket.text) + len(body.text) > 16000:
            raise HTTPException(422, 'Combined message exceeds the input limit')
        complete_review(db, ticket, user, application.state.graph, 'clarified')
        ticket.text += '\n\nAdditional information: ' + body.text
        ticket.status = 'new'
        audit(db, ticket, user, 'clarification_added')
        db.commit()
        return analyze_ticket(db, ticket, user, application)

    @application.post('/api/tickets/{ticket_id}/review')
    def review_api(ticket_id: str, body: Review, user=Depends(writer), db=Depends(session)):
        ticket = get_ticket(db, ticket_id, user)
        if ticket.status not in {'ready', 'awaiting_clarification'}:
            raise HTTPException(409, 'Review this ticket through its proposed action')
        complete_review(db, ticket, user, application.state.graph, body.decision)
        if body.decision == 'rejected':
            ticket.status = 'awaiting_clarification'
        audit(db, ticket, user, 'draft_' + body.decision)
        db.commit()
        return ticket_dict(db, ticket)

    @application.post('/api/tickets/{ticket_id}/resolve')
    def resolve_api(ticket_id: str, user=Depends(writer), db=Depends(session)):
        ticket = get_ticket(db, ticket_id, user)
        run = latest_run(db, ticket)
        if ticket.status not in {'ready', 'escalated'} or ticket.reviewed_at is None or not run or run.status != 'completed':
            raise HTTPException(409, 'Complete human review before recording resolution')
        ticket.status = 'resolved'
        ticket.resolved_at = utcnow()
        audit(db, ticket, user, 'problem_resolved')
        db.commit()
        return ticket_dict(db, ticket)

    @application.post('/api/actions/{action_id}/edit')
    def edit_api(action_id: str, body: ActionEdit, user=Depends(writer), db=Depends(session)):
        action = get_action(db, action_id, user)
        ticket = get_ticket(db, action.ticket_id, user)
        if action.version != body.version or action.status not in {'pending', 'approved', 'rejected'}:
            raise HTTPException(409, 'Action is stale or execution has already started')
        updated = db.execute(update(ProposedAction).where(ProposedAction.id == action_id, ProposedAction.version == body.version, ProposedAction.status.in_(['pending', 'approved', 'rejected'])).values(title=body.title.strip(), body=body.body.strip(), version=body.version + 1, approved_version=None, status='pending', content_hash=action_hash(body.title.strip(), body.body.strip(), action.repository)))
        if updated.rowcount != 1:
            raise HTTPException(409, 'Action was changed concurrently')
        ticket.status = 'awaiting_approval'
        audit(db, ticket, user, 'action_edited', {'version': body.version + 1})
        db.commit()
        db.expire_all()
        return ticket_dict(db, ticket)

    @application.post('/api/actions/{action_id}/approve')
    def approve_api(action_id: str, body: ActionVersion, user=Depends(writer), db=Depends(session)):
        action = get_action(db, action_id, user)
        ticket = get_ticket(db, action.ticket_id, user)
        if action.version != body.version or action.status not in {'pending', 'approved'}:
            raise HTTPException(409, 'Only the current pending action can be approved')
        if action.status == 'approved':
            return ticket_dict(db, ticket)
        updated = db.execute(update(ProposedAction).where(ProposedAction.id == action.id, ProposedAction.version == body.version, ProposedAction.status == 'pending').values(status='approved', approved_version=body.version))
        if updated.rowcount != 1:
            raise HTTPException(409, 'Action was changed concurrently')
        db.add(Approval(action_id=action.id, user_id=user.id, version=action.version, content_hash=action.content_hash, decision='approved'))
        complete_review(db, ticket, user, application.state.graph, 'action_approved')
        audit(db, ticket, user, 'action_approved', {'version': action.version, 'content_hash': action.content_hash})
        db.commit()
        return ticket_dict(db, ticket)

    @application.post('/api/actions/{action_id}/reject')
    def reject_api(action_id: str, body: ActionVersion, user=Depends(writer), db=Depends(session)):
        action = get_action(db, action_id, user)
        ticket = get_ticket(db, action.ticket_id, user)
        if action.version != body.version or action.status not in {'pending', 'approved'}:
            raise HTTPException(409, 'Action is stale or execution has started')
        action.status = 'rejected'
        action.approved_version = None
        ticket.status = 'awaiting_clarification'
        db.add(Approval(action_id=action.id, user_id=user.id, version=action.version, content_hash=action.content_hash, decision='rejected'))
        complete_review(db, ticket, user, application.state.graph, 'action_rejected')
        audit(db, ticket, user, 'action_rejected', {'version': action.version})
        db.commit()
        return ticket_dict(db, ticket)

    @application.post('/api/actions/{action_id}/execute')
    def execute_api(action_id: str, body: ActionVersion, user=Depends(writer), db=Depends(session)):
        action = get_action(db, action_id, user)
        return execute_action(db, action, get_ticket(db, action.ticket_id, user), user, body.version, application)

    @application.post('/api/actions/{action_id}/reconcile')
    def reconcile_api(action_id: str, user=Depends(writer), db=Depends(session)):
        action = get_action(db, action_id, user)
        return reconcile(db, action, get_ticket(db, action.ticket_id, user), user, application)

    @application.get('/api/demo-issues/{issue_id}', response_class=PlainTextResponse)
    def demo_issue(issue_id: int, user=Depends(current_user), db=Depends(session)):
        issue = db.scalar(select(DemoIssue).where(DemoIssue.id == issue_id, DemoIssue.organization_id == user.organization_id))
        if not issue:
            raise HTTPException(404, 'Demo issue not found')
        return f'SYNTHETIC ISSUE #{issue.id}\n{issue.title}\n\n{issue.body}'

    @application.get('/api/documents')
    def documents(user=Depends(current_user), db=Depends(session)):
        docs = db.scalars(select(DocumentVersion).where(DocumentVersion.organization_id == user.organization_id).order_by(DocumentVersion.document_key, DocumentVersion.version.desc())).all()
        return [document_dict(doc) for doc in docs]

    @application.post('/api/documents/upload', status_code=201)
    def upload_document(file: UploadFile = File(...), document_key: str = Form(..., min_length=1, max_length=100), title: str = Form(..., min_length=1, max_length=200), product: str = Form('RetailBridge', max_length=80), min_version: str = Form(...), max_version: str = Form(...), user=Depends(admin), db=Depends(session)):
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+', document_key):
            raise HTTPException(422, 'Use a simple document key without slashes')
        if not all(re.fullmatch(r'\d+\.\d+(\.\d+)?', v) for v in [min_version, max_version]):
            raise HTTPException(422, 'Versions must use major.minor or major.minor.patch format')
        version_tuple = lambda v: tuple(int(n) for n in v.split('.')) + (0,) * (3 - len(v.split('.')))
        if version_tuple(min_version) > version_tuple(max_version):
            raise HTTPException(422, 'Minimum version must not exceed maximum version')
        raw = file.file.read(1_000_001)
        if len(raw) > 1_000_000:
            raise HTTPException(413, 'Document limit is 1 MB')
        suffix = Path(file.filename or '').suffix.lower()
        try:
            if suffix == '.md':
                content = raw.decode('utf-8-sig')
            elif suffix == '.pdf':
                reader = PdfReader(io.BytesIO(raw))
                if reader.is_encrypted or len(reader.pages) > 25:
                    raise ValueError('PDF must be unencrypted with at most 25 pages')
                content = '\n\n'.join((p.extract_text() or '') for p in reader.pages)
            else:
                raise HTTPException(422, 'Upload Markdown (.md) or a text PDF (.pdf)')
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(422, 'Could not extract document text') from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, 'Invalid document') from exc
        if len(content.strip()) < 20 or len(content) > 16000:
            raise HTTPException(422, 'Document must contain 20–16000 text characters; scanned PDFs need OCR before upload')
        db.scalar(select(Organization).where(Organization.id == user.organization_id).with_for_update())
        last = db.scalar(select(func.max(DocumentVersion.version)).where(DocumentVersion.organization_id == user.organization_id, DocumentVersion.document_key == document_key)) or 0
        doc = DocumentVersion(organization_id=user.organization_id, document_key=document_key, title=title, product=product, min_version=min_version, max_version=max_version, version=last + 1, content=content)
        db.add(doc)
        db.add(AuditEvent(organization_id=user.organization_id, actor_id=user.id, event_type='document_uploaded', payload={'document_key': document_key, 'version': last + 1}))
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, 'A document version was created concurrently. Retry upload.') from exc
        return document_dict(doc)

    @application.get('/api/documents/{document_id}')
    def get_document(document_id: str, user=Depends(current_user), db=Depends(session)):
        doc = db.scalar(select(DocumentVersion).where(DocumentVersion.id == document_id, DocumentVersion.organization_id == user.organization_id))
        if not doc:
            raise HTTPException(404, 'Document not found')
        data = document_dict(doc, content=True)
        data.pop('embedding', None)
        return data

    def report_data(user, db):
        tickets = db.scalars(select(Ticket).where(Ticket.organization_id == user.organization_id)).all()
        events = db.scalars(select(AuditEvent).where(AuditEvent.organization_id == user.organization_id).order_by(AuditEvent.created_at.desc())).all()
        analyzed = [e.payload for e in events if e.event_type == 'analysis_completed']
        costs = [a.get('cost_usd') for a in analyzed]
        count_by = lambda key: dict(Counter(getattr(t, key) for t in tickets))
        now = utcnow()
        subjects = {t.id: t.subject for t in tickets}
        data = {'total': len(tickets), 'open': sum(t.status != 'resolved' for t in tickets), 'overdue': sum(t.reviewed_at is None and t.due_at.replace(tzinfo=timezone.utc) < now for t in tickets), 'reviewed': sum(t.reviewed_at is not None for t in tickets), 'executed': sum(e.event_type == 'execution_succeeded' for e in events), 'api_errors': sum(a.get('api_errors', 0) for a in analyzed), 'avg_analysis_ms': round(sum(a.get('elapsed_ms', 0) for a in analyzed) / len(analyzed), 2) if analyzed else 0, 'cost_usd': round(sum(c for c in costs if c is not None), 6), 'cost_complete': all(c is not None for c in costs), 'by_category': count_by('category'), 'by_queue': count_by('queue'), 'by_language': count_by('language'), 'by_status': count_by('status'), 'decisions': dict(Counter(e.event_type for e in events if e.event_type in {'action_approved', 'action_rejected', 'action_edited', 'draft_accepted', 'draft_rejected'})), 'recent_activity': [{'event_type': e.event_type, 'created_at': iso(e.created_at), 'ticket_id': e.ticket_id, 'subject': subjects.get(e.ticket_id, 'Knowledge library')} for e in events[:12]], 'definitions': {'overdue': 'No completed human review by the due time. 24/7 UTC: P1 1h, P2 4h, P3 8h.', 'reviewed': 'Human review recorded; this is not a message sent to the customer.', 'executed': 'Confirmed engineering issue creations; escalation is not resolution.', 'avg_analysis_ms': 'Mean completed workflow time, including diagnostic calls, excluding human review.', 'cost_usd': 'Known analysis provider cost subtotal; excludes indexing and unmetered failed provider calls. Not a complete invoice.', 'cost_complete': 'Whether all completed analysis runs have configured pricing; no guarantee of billed-cost reconciliation.'}}
        return tickets, data

    @application.get('/api/dashboard')
    def dashboard(user=Depends(current_user), db=Depends(session)):
        return report_data(user, db)[1]

    @application.get('/api/reports/export.csv')
    def export_csv(user=Depends(current_user), db=Depends(session)):
        tickets, _ = report_data(user, db)
        output = io.StringIO(newline='')
        writer_csv = csv.writer(output)
        writer_csv.writerow(['report_generated_at_utc', 'organization', 'ticket_id', 'subject', 'language', 'category', 'queue', 'priority', 'status', 'created_at_utc', 'human_review_due_at_utc', 'human_review_completed_at_utc', 'resolved_at_utc', 'analysis_ms', 'known_analysis_cost_usd', 'sla_definition'])
        def safe(value):
            value = str(value) if value is not None else ''
            return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value
        now = iso(utcnow())
        for t in tickets:
            a = t.analysis or {}
            writer_csv.writerow([safe(v) for v in [now, user.organization_id, t.id, t.subject, t.language, t.category, t.queue, t.priority, t.status, iso(t.created_at), iso(t.due_at), iso(t.reviewed_at), iso(t.resolved_at), a.get('elapsed_ms'), a.get('cost_usd'), '24/7 UTC; first human review, not customer response; P1=1h P2=4h P3=8h']])
        return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="supportops-report.csv"'})

    if config.frontend_dist.is_dir():
        assets = config.frontend_dist / 'assets'
        if assets.is_dir():
            application.mount('/assets', StaticFiles(directory=assets), name='assets')

        @application.get('/', include_in_schema=False)
        def index():
            return FileResponse(config.frontend_dist / 'index.html')

    return application


app = create_app()
