"""Exercise the running synthetic stack, including real HTTP diagnostics and storage."""
import argparse
from uuid import uuid4

import httpx


def check(response, status=200):
    if response.status_code != status:
        raise RuntimeError(f'{response.request.method} {response.request.url.path}: expected {status}, got {response.status_code}')
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--password', default='demo-supportops')
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=30, follow_redirects=False) as client:
        health = check(client.get('/api/health'))
        if any(health.get(key) != 'demo' for key in ('mode', 'llm_provider', 'embedding_provider', 'issue_provider')):
            raise SystemExit('Smoke checks require every provider and the application mode to be demo.')
        if not health.get('synthetic_diagnostics'):
            raise SystemExit('Smoke checks require synthetic diagnostics.')
        check(client.post('/api/auth/login', json={'email': 'support@northstar.demo', 'password': args.password}))
        ticket = check(client.post('/api/tickets', json={
            'subject': f'Synthetic stack smoke {uuid4().hex[:8]}',
            'text': 'After upgrading RetailBridge to 3.8, three stores cannot complete checkout. Error E-214.',
            'language': 'en',
        }), 201)
        ticket_path = f'/api/tickets/{ticket["id"]}'
        analyzed = check(client.post(ticket_path + '/analyze'))
        assert analyzed['status'] == 'awaiting_approval'
        assert analyzed['analysis']['sources']
        assert all(item['status'] == 'ok' for item in analyzed['analysis']['diagnostics'])
        action = analyzed['action']
        action_path = f'/api/actions/{action["id"]}'
        version = {'version': action['version']}
        check(client.post(action_path + '/execute', json=version), 409)
        check(client.post(action_path + '/approve', json=version))
        executed = check(client.post(action_path + '/execute', json=version))
        repeated = check(client.post(action_path + '/execute', json=version))
        assert executed['status'] == 'escalated'
        assert executed['action']['execution']['external_id'] == repeated['action']['execution']['external_id']
        assert sum(event['event_type'] == 'execution_succeeded' for event in repeated['events']) == 1
        issue = client.get(executed['action']['execution']['url'])
        assert issue.status_code == 200 and issue.text.startswith('SYNTHETIC ISSUE #')
        assert check(client.get('/api/dashboard'))['executed'] >= 1
        report = client.get('/api/reports/export.csv')
        assert report.status_code == 200 and ticket['id'] in report.text
        # A new authenticated session must not gain access to the first tenant's records.
        check(client.post('/api/auth/logout'))
        check(client.post('/api/auth/login', json={'email': 'support@contoso.demo', 'password': args.password}))
        check(client.get(ticket_path), 404)
        check(client.get(executed['action']['execution']['url']), 404)
        print(f'Synthetic {health["database"]} smoke passed: diagnostic HTTP, cited proposal, approval, one issue, CSV and tenant isolation.')


if __name__ == '__main__':
    main()
