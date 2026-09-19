import re
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import select

from .models import DemoIssue, utcnow


def diagnostics(text, settings, mode='normal'):
    # Arguments are derived within the allowlist; incoming instructions cannot choose URLs/tools.
    version = re.search(r'\b(\d+\.\d+(?:\.\d+)?)\b', text)
    if 'retailbridge' not in text.lower() or not version:
        return []
    service = 'checkout'
    for code, candidate in [('E-409', 'inventory'), ('E-401', 'connector'), ('P-102', 'printing'), ('R-503', 'reporting')]:
        if code.lower() in text.lower():
            service = candidate
    jobs = [('get_service_status', '/service-status', {'service': service, 'version': version[1], 'mode': mode}), ('get_recent_changes', '/recent-changes', {'product': 'RetailBridge', 'version': version[1], 'mode': mode})]

    def read(job):
        tool, path, params = job
        result = {'tool': tool, 'status': 'error', 'checked_at': utcnow().isoformat(), 'data': None, 'error': None}
        for attempt in range(2):
            try:
                with httpx.Client(timeout=settings.api_timeout_seconds, follow_redirects=False) as client:
                    response = client.get(settings.retailbridge_url + path, params=params)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get('synthetic') is not True:
                    raise ValueError('Invalid synthetic API schema')
                if tool == 'get_service_status' and data.get('status') not in {'operational', 'degraded', 'down'}:
                    raise ValueError('Invalid service status')
                if tool == 'get_recent_changes' and not isinstance(data.get('changes'), list):
                    raise ValueError('Invalid changes')
                result.update(status='ok', data=data, error=None, attempts=attempt + 1)
                return result
            except httpx.TimeoutException:
                result.update(status='timeout', error='Diagnostic API timed out; service health is unknown.')
            except (httpx.HTTPError, ValueError):
                result.update(status='error', error='Diagnostic API unavailable or invalid; service health is unknown.')
        result['attempts'] = 2
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(read, jobs))


class IssueError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def marker_for(execution):
    return f'<!-- supportops-execution:{execution.id}:{execution.content_hash} -->'


def repository_for(org_id, settings):
    if settings.issue_provider == 'demo':
        return f'demo/{org_id}-engineering'
    repository = settings.github_repositories.get(org_id, '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise IssueError('No permitted GitHub test repository configured for this organization.')
    if not settings.github_token:
        raise IssueError('GitHub token is not configured.')
    if settings.github_api_url != 'https://api.github.com':
        raise IssueError('Only the configured GitHub API origin is supported.')
    return repository


def github_client(settings):
    return httpx.Client(base_url=settings.github_api_url, timeout=settings.api_timeout_seconds, follow_redirects=False, headers={'Authorization': f'Bearer {settings.github_token}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': settings.github_api_version})


def create_issue(action, execution, settings, session_factory):
    repo = repository_for(action.organization_id, settings)
    if repo != action.repository:
        raise IssueError('Repository changed after approval; execution blocked.')
    marker = marker_for(execution)
    if settings.issue_provider == 'demo':
        if settings.demo_issue_mode == 'timeout_before':
            raise IssueError('Synthetic timeout before confirmed creation.', uncertain=True)
        if settings.demo_issue_mode == 'error':
            raise IssueError('Synthetic issue API rejected the request.')
        with session_factory() as db:
            issue = DemoIssue(marker=marker, organization_id=action.organization_id, title=action.title, body=action.body + '\n\n' + marker)
            db.add(issue)
            db.commit()
            result = {'external_id': str(issue.id), 'url': f'/api/demo-issues/{issue.id}'}
        if settings.demo_issue_mode == 'timeout_after':
            raise IssueError('Synthetic response lost after issue creation.', uncertain=True)
        return result
    try:
        with github_client(settings) as client:
            response = client.post(f'/repos/{repo}/issues', json={'title': action.title, 'body': action.body + '\n\n' + marker})
        if response.status_code != 201:
            raise IssueError(f'GitHub returned HTTP {response.status_code}.', uncertain=response.status_code >= 500 or response.status_code < 400)
        value = response.json()
        if not isinstance(value.get('number'), int) or not str(value.get('html_url', '')).startswith(f'https://github.com/{repo}/issues/'):
            raise IssueError('GitHub returned an incomplete creation response.', uncertain=True)
        return {'external_id': str(value['number']), 'url': value['html_url']}
    except (httpx.HTTPError, ValueError) as exc:
        raise IssueError('Creation outcome unknown. Reconcile before any further action.', uncertain=True) from exc


def reconcile_issue(action, execution, settings, session_factory):
    repo = repository_for(action.organization_id, settings)
    if repo != action.repository or execution.mode != settings.issue_provider:
        raise IssueError('Integration configuration changed; manual verification required.', uncertain=True)
    marker = marker_for(execution)
    if execution.mode == 'demo':
        with session_factory() as db:
            item = db.scalar(select(DemoIssue).where(DemoIssue.marker == marker, DemoIssue.organization_id == action.organization_id))
            return {'external_id': str(item.id), 'url': f'/api/demo-issues/{item.id}'} if item else None
    try:
        with github_client(settings) as client:
            # Bounded reconciliation scans recent issues, without relying on eventually indexed search.
            for page in range(1, 6):
                response = client.get(f'/repos/{repo}/issues', params={'state': 'all', 'sort': 'created', 'direction': 'desc', 'per_page': 100, 'page': page})
                response.raise_for_status()
                issues = response.json()
                if not isinstance(issues, list):
                    raise ValueError('Invalid issues list')
                matches = [i for i in issues if not i.get('pull_request') and marker in (i.get('body') or '')]
                if matches:
                    item = matches[0]
                    return {'external_id': str(item['number']), 'url': item['html_url']}
                if len(issues) < 100:
                    break
        return None  # Absence is never proof that the POST failed.
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise IssueError('Could not reconcile external result; manual verification required.', uncertain=True) from exc
