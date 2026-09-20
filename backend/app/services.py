import time
from datetime import timedelta

from fastapi import HTTPException
from langgraph.types import Command
from sqlalchemy import select, update

from .integrations import IssueError, create_issue, reconcile_issue, repository_for
from .models import Approval, AuditEvent, DocumentVersion, Execution, ProposedAction, Ticket, WorkflowRun, utcnow
from .security import action_hash


def audit(db, ticket, user, event_type, payload=None):
    db.add(AuditEvent(organization_id=ticket.organization_id, ticket_id=ticket.id, actor_id=user.id if user else None, event_type=event_type, payload=payload or {}))


def iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        from datetime import timezone
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def document_dict(doc, content=False):
    data = {key: getattr(doc, key) for key in ['id', 'document_key', 'title', 'version', 'product', 'min_version', 'max_version', 'status', 'conflict_group', 'error']}
    data['created_at'] = iso(doc.created_at)
    if content:
        data['content'] = doc.content
        data['embedding'] = list(doc.embedding) if doc.embedding is not None else None
        if data['embedding'] is not None:
            data['embedding'] = [float(n) for n in data['embedding']]
    return data


def ticket_dict(db, ticket, detail=True):
    data = {key: getattr(ticket, key) for key in ['id', 'subject', 'text', 'language', 'diagnostic_mode', 'category', 'queue', 'priority', 'status', 'analysis']}
    data.update({key: iso(getattr(ticket, key)) for key in ['created_at', 'due_at', 'reviewed_at', 'resolved_at']})
    run = latest_run(db, ticket)
    data['review_pending'] = bool(run and run.status == 'waiting')
    data['workflow_mode'] = run.workflow_mode if run else None
    action = db.scalar(select(ProposedAction).where(ProposedAction.ticket_id == ticket.id, ProposedAction.organization_id == ticket.organization_id))
    data['action'] = None
    if action:
        data['action'] = {key: getattr(action, key) for key in ['id', 'title', 'body', 'repository', 'version', 'status', 'approved_version']}
        execution = db.scalar(select(Execution).where(Execution.action_id == action.id))
        data['action']['execution'] = {key: getattr(execution, key) for key in ['id', 'status', 'external_id', 'url', 'error', 'mode']} if execution else None
    events = db.scalars(select(AuditEvent).where(AuditEvent.ticket_id == ticket.id, AuditEvent.organization_id == ticket.organization_id).order_by(AuditEvent.created_at)).all() if detail else []
    data['events'] = [{'id': e.id, 'event_type': e.event_type, 'created_at': iso(e.created_at), 'payload': e.payload} for e in events]
    return data


def get_ticket(db, ticket_id, user):
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.organization_id == user.organization_id))
    if ticket is None:
        raise HTTPException(404, 'Ticket not found')
    return ticket


def get_action(db, action_id, user):
    action = db.scalar(select(ProposedAction).where(ProposedAction.id == action_id, ProposedAction.organization_id == user.organization_id).with_for_update())
    if action is None:
        raise HTTPException(404, 'Action not found')
    return action


def latest_run(db, ticket):
    return db.scalar(select(WorkflowRun).where(WorkflowRun.ticket_id == ticket.id, WorkflowRun.organization_id == ticket.organization_id).order_by(WorkflowRun.created_at.desc()).limit(1))


def graph_config(run):
    return {'configurable': {'thread_id': f'{run.organization_id}:{run.id}'},
            'recursion_limit': 20 if run.workflow_mode == 'multi_agent_review' else 8}


def graph_for_run(app, run):
    if run.workflow_mode == 'multi_agent_review':
        return app.state.review_graph
    if run.workflow_mode == 'standard':
        return app.state.graph
    raise ValueError('The saved workflow mode is unsupported')


def workflow_input(db, ticket, settings):
    documents = db.scalars(select(DocumentVersion).where(DocumentVersion.organization_id == ticket.organization_id, DocumentVersion.status == 'ready')).all()
    latest = {}
    expected_model = 'demo-hash-256' if settings.embedding_provider == 'demo' else settings.embedding_model
    for doc in documents:
        if doc.embedding_model != expected_model:
            continue
        if doc.document_key not in latest or doc.version > latest[doc.document_key].version:
            latest[doc.document_key] = doc
    return {'ticket_id': ticket.id, 'organization_id': ticket.organization_id, 'text': ticket.text, 'language': ticket.language, 'diagnostic_mode': ticket.diagnostic_mode, 'documents': [document_dict(d, True) for d in latest.values()]}


def complete_review(db, ticket, user, app, decision, reason=''):
    run = latest_run(db, ticket)
    if run and run.status == 'waiting':
        graph_for_run(app, run).invoke(Command(resume={'decision': decision, 'reviewer_id': user.id, 'reason': reason.strip()}), graph_config(run))
        run.status = 'completed'
        run.completed_at = utcnow()
    if ticket.reviewed_at is None:
        ticket.reviewed_at = utcnow()
    audit(db, ticket, user, 'review_completed', {'decision': decision, 'reason': reason.strip(),
          'run_id': run.id if run else None, 'workflow_mode': run.workflow_mode if run else None})


def analyze_ticket(db, ticket, user, app, resume=False, workflow_mode=None):
    existing_action = db.scalar(select(ProposedAction).where(ProposedAction.ticket_id == ticket.id))
    if existing_action:
        raise HTTPException(409, 'Review the existing proposal before requesting another analysis')
    run = latest_run(db, ticket)
    if resume:
        if not run or run.status not in {'running', 'failed'}:
            raise HTTPException(409, 'No interrupted workflow to resume')
        if ticket.status == 'analyzing' and (utcnow() - run.created_at.replace(tzinfo=utcnow().tzinfo)).total_seconds() < 120:
            raise HTTPException(409, 'Workflow is still running; retry recovery after two minutes')
    else:
        selected_mode = workflow_mode or (run.workflow_mode if run else app.state.settings.default_workflow_mode)
        if selected_mode not in {'standard', 'multi_agent_review'}:
            raise HTTPException(422, 'Unsupported workflow mode')
        claimed = db.execute(update(Ticket).where(Ticket.id == ticket.id, Ticket.organization_id == user.organization_id, Ticket.status.in_(['new', 'failed'])).values(status='analyzing'))
        if claimed.rowcount != 1:
            raise HTTPException(409, 'Ticket is already analyzed or being processed')
        run = WorkflowRun(ticket_id=ticket.id, organization_id=user.organization_id, workflow_mode=selected_mode)
        db.add(run)
    ticket.status = 'analyzing'
    run.status = 'running'
    db.flush()
    audit(db, ticket, user, 'analysis_started', {'run_id': run.id, 'resumed': resume, 'workflow_mode': run.workflow_mode})
    db.commit()
    started = time.perf_counter()
    try:
        graph = graph_for_run(app, run)
        if resume:
            state = graph.get_state(graph_config(run))
            if state.created_at is None:
                # The durable run claim can survive a crash before LangGraph saves its input.
                result = graph.invoke(workflow_input(db, ticket, app.state.settings), graph_config(run))
            elif state.values.get('analysis') and state.interrupts:
                result = state.values
            else:
                result = graph.invoke(None, graph_config(run))
        else:
            result = graph.invoke(workflow_input(db, ticket, app.state.settings), graph_config(run))
        analysis = dict(result['analysis'])
        analysis['workflow_mode'] = run.workflow_mode
        analysis['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 2)
        db.refresh(ticket)
        ticket.analysis = analysis
        ticket.category = analysis['category']
        ticket.queue = {'Incident': 'Technical Support', 'Request': 'Service Desk', 'Problem': 'Engineering', 'Change': 'Change Advisory'}.get(ticket.category, 'Intake')
        stores = analysis['fields'].get('affected_stores') or 0
        ticket.priority = 'P1' if ticket.category == 'Incident' and stores >= 3 else 'P2' if ticket.category in {'Incident', 'Problem'} else 'P3'
        ticket.due_at = ticket.created_at + timedelta(hours={'P1': 1, 'P2': 4, 'P3': 8}[ticket.priority])
        step = analysis['next_step']
        ticket.status = {'clarify': 'awaiting_clarification', 'answer': 'ready', 'escalate': 'awaiting_approval'}[step]
        run.status = 'waiting'
        run.error = None
        if step == 'escalate':
            try:
                repository = repository_for(user.organization_id, app.state.settings)
            except IssueError:
                repository = 'unconfigured/test-repository'
            fields = analysis['fields']
            title = f"[RetailBridge] {fields.get('error_code') or ticket.category}: {ticket.subject}"[:200]
            facts = '\n'.join(f"- {fact.get('text', '')} [source: {fact.get('source', '')}]" for fact in analysis.get('facts', []))
            sources = '\n'.join(f"- {source['title']} (document {source['id']}, revision {source.get('version', 1)})" for source in analysis.get('sources', []))
            body = f"## Synthetic support case\n\n{ticket.text}\n\n## Confirmed observations\n{facts}\n\n## Sources\n{sources}\n\n## Draft response\n{analysis['draft']}\n\n## Hypothesis (unconfirmed)\n{analysis.get('hypothesis') or 'No causal hypothesis established.'}\n\nEscalation does not imply problem resolution."
            action = ProposedAction(ticket_id=ticket.id, organization_id=user.organization_id, title=title, body=body, repository=repository, content_hash=action_hash(title, body, repository))
            db.add(action)
        team = analysis.get('review_team')
        team_summary = None if not team else {
            'status': team['status'], 'revisions': team['revisions'],
            'disagreements': len(team['disagreements']),
            'by_role': {role: sum(finding['role'] == role for finding in team['disagreements'])
                        for role in {finding['role'] for finding in team['disagreements']}},
        }
        audit(db, ticket, user, 'analysis_completed', {'run_id': run.id, 'workflow_mode': run.workflow_mode, 'review_team': team_summary, 'elapsed_ms': analysis['elapsed_ms'], 'next_step': step, 'cost_usd': analysis.get('cost_usd'), 'model': analysis.get('model'), 'input_tokens': analysis.get('input_tokens', 0), 'output_tokens': analysis.get('output_tokens', 0), 'api_errors': sum(d.get('status') != 'ok' for d in analysis.get('diagnostics', []))})
        db.commit()
    except Exception as exc:
        db.rollback()
        ticket = db.get(Ticket, ticket.id)
        run = db.get(WorkflowRun, run.id)
        ticket.status = 'failed'
        run.status = 'failed'
        run.error = f'Workflow failed ({type(exc).__name__}); safe to resume this workflow.'
        audit(db, ticket, user, 'analysis_failed', {'run_id': run.id, 'error_type': type(exc).__name__})
        db.commit()
        raise HTTPException(503, run.error) from exc
    return ticket_dict(db, ticket)


def execute_action(db, action, ticket, user, version, app):
    if action.version != version:
        raise HTTPException(409, 'Stale action version')
    existing = db.scalar(select(Execution).where(Execution.action_id == action.id))
    if existing:
        return ticket_dict(db, ticket)
    expected_hash = action_hash(action.title, action.body, action.repository)
    approved = db.scalar(select(Approval).where(Approval.action_id == action.id, Approval.version == version, Approval.decision == 'approved', Approval.content_hash == expected_hash))
    if action.status != 'approved' or action.approved_version != version or action.content_hash != expected_hash or not approved:
        raise HTTPException(409, 'This exact action must be approved before execution')
    try:
        if repository_for(user.organization_id, app.state.settings) != action.repository:
            raise IssueError('Repository configuration differs from the approved action')
    except IssueError as exc:
        raise HTTPException(409, str(exc)) from exc
    claimed = db.execute(update(ProposedAction).where(ProposedAction.id == action.id, ProposedAction.status == 'approved', ProposedAction.version == version, ProposedAction.content_hash == expected_hash).values(status='executing'))
    if claimed.rowcount != 1:
        raise HTTPException(409, 'Action was changed or is already executing')
    execution = Execution(action_id=action.id, version=version, content_hash=expected_hash, mode=app.state.settings.issue_provider)
    db.add(execution)
    db.flush()
    audit(db, ticket, user, 'execution_started', {'execution_id': execution.id, 'version': version})
    db.commit()  # Durable claim precedes any outbound POST.
    try:
        result = create_issue(action, execution, app.state.settings, app.state.session_factory)
        apply_success(db, ticket, action, execution, user, result)
    except IssueError as exc:
        execution.status = action.status = 'needs_review' if exc.uncertain else 'failed'
        execution.error = str(exc)
        ticket.status = 'needs_review' if exc.uncertain else 'failed'
        audit(db, ticket, user, 'execution_uncertain' if exc.uncertain else 'execution_failed', {'execution_id': execution.id, 'error': str(exc)})
    except Exception:
        execution.status = action.status = ticket.status = 'needs_review'
        execution.error = 'Execution was interrupted; verify the external result.'
        audit(db, ticket, user, 'execution_uncertain', {'execution_id': execution.id})
    db.commit()
    return ticket_dict(db, ticket)


def apply_success(db, ticket, action, execution, user, result):
    execution.status = action.status = 'succeeded'
    execution.external_id = result['external_id']
    execution.url = result['url']
    execution.completed_at = utcnow()
    execution.error = None
    ticket.status = 'escalated'
    audit(db, ticket, user, 'execution_succeeded', {'execution_id': execution.id, 'external_id': result['external_id'], 'mode': execution.mode})


def reconcile(db, action, ticket, user, app):
    execution = db.scalar(select(Execution).where(Execution.action_id == action.id))
    if not execution:
        raise HTTPException(409, 'No execution to reconcile')
    if execution.status == 'succeeded':
        return ticket_dict(db, ticket)
    try:
        result = reconcile_issue(action, execution, app.state.settings, app.state.session_factory)
        if result:
            apply_success(db, ticket, action, execution, user, result)
        else:
            execution.status = action.status = ticket.status = 'needs_review'
            execution.error = 'No matching issue confirmed. Manual verification required; no repeat POST was sent.'
    except IssueError as exc:
        execution.status = action.status = ticket.status = 'needs_review'
        execution.error = str(exc)
    audit(db, ticket, user, 'execution_reconciled', {'execution_id': execution.id, 'status': execution.status})
    db.commit()
    return ticket_dict(db, ticket)
