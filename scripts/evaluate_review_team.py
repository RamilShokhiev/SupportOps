"""Compare demo graphs on development data only, with no external API calls."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'backend'))

import httpx
from langgraph.checkpoint.memory import InMemorySaver

from app.config import Settings
from app.review_team import build_review_graph
from app.workflow import build_graph
from scripts.evaluate import documents, percentile


def load_development():
    path = ROOT / 'data' / 'dev_tickets.json'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = json.loads((ROOT / 'data' / 'dataset_manifest.json').read_text(encoding='utf-8'))
    if digest != manifest['sha256']['data/dev_tickets.json']:
        raise ValueError('The frozen development input changed')
    tickets = json.loads(path.read_text(encoding='utf-8'))
    if any(ticket['split'] != 'dev' or ticket['language'] != 'en' for ticket in tickets):
        raise ValueError('This comparison accepts English development tickets only')
    return tickets, digest


def diagnostic_response(request):
    if request.url.host != 'retailbridge.test':
        raise AssertionError('The development comparison cannot make external requests')
    mode = request.url.params.get('mode', 'normal')
    if mode == 'timeout':
        raise httpx.ReadTimeout('Synthetic development timeout', request=request)
    if mode == 'error':
        return httpx.Response(503, json={'detail': 'Synthetic development API failure'})
    version = request.url.params.get('version', '')
    checked = '2026-09-19T12:00:00+00:00'
    if request.url.path == '/service-status':
        service = request.url.params['service']
        return httpx.Response(200, json={
            'service': service, 'version': version, 'synthetic': True, 'checked_at': checked,
            'status': 'degraded' if service == 'checkout' and version == '3.8' else 'operational',
        })
    if request.url.path == '/recent-changes':
        return httpx.Response(200, json={
            'product': 'RetailBridge', 'synthetic': True,
            'changes': [{'id': 'CHG-380', 'product': 'RetailBridge', 'version': '3.8',
                         'description': 'Checkout authorization contract updated; investigate E-214 reports.',
                         'occurred_at': checked}] if version == '3.8' else [],
        })
    raise AssertionError('Unexpected diagnostic route')


def markdown(report):
    lines = [
        '# Review-team development comparison', '',
        f"Generated: {report['generated_at']}. Actual run on {report['n']} synthetic English development tickets "
        f"from {report['scenario_groups']} scenario groups. Providers: deterministic demo rules, templates and hash embeddings.", '',
        'Both variants run the complete LangGraph path through the human-review interrupt, including '
        'the real diagnostic adapter with an in-memory HTTP transport and separate in-memory checkpoints. '
        'The comparison performs no human decision, issue creation, live model request or external network call. '
        'The frozen held-out test file is neither loaded nor modified.', '',
        '| Workflow | Completed | Next-step matches | Category matches | p50 ms | p95 ms | Runtime errors |',
        '|---|---:|---:|---:|---:|---:|---:|',
    ]
    for mode, metric in report['metrics'].items():
        lines.append(f"| {mode} | {metric['completed']}/{report['n']} | {metric['next_step_matches']}/{report['n']} | "
                     f"{metric['category_matches']}/{report['n']} | {metric['latency_ms']['p50']} | "
                     f"{metric['latency_ms']['p95']} | {metric['runtime_errors']} |")
    lines += [
        '', f"Route agreement: {report['route_agreement']}/{report['paired_completed']} completed pairs. "
        f"Changed routes: {report['changed_routes']}. Team review disagreements: {report['team_disagreements']}; "
        f"draft revisions: {report['team_revisions']}.", '',
        'These numbers do not demonstrate live-model improvement. They measure a small, correlated development '
        'regression sample using the existing routing policy. Additional review roles and recorded disagreements '
        'improve inspectability; they are not proof of better support answers. A fresh held-out set and authorized '
        'live comparison are required for a quality claim.', '',
        'Timings include graph execution, mock HTTP diagnostics and in-memory checkpoint work, excluding graph '
        'construction, document indexing, database/API/UI overhead, human review and external actions. '
        'Variant order alternates per ticket. Values come from one local run and are not production latency '
        'or statistically significant performance measurements. Demo external model cost is zero; local '
        'compute and infrastructure costs are not measured.', '',
        'Reproduce from the repository:', '', '```powershell',
        r'.\.venv\Scripts\python.exe .\scripts\evaluate_review_team.py', '```', '',
        f"Python: `{report['python']}`. Development SHA-256: `{report['development_sha256']}`.", '',
        'Per-ticket JSON is generated by this script and is not stored in git. '
        'The existing held-out baseline reports remain in `docs/EVALUATION.md`.', '',
    ]
    return '\n'.join(lines)


def main():
    tickets, digest = load_development()
    settings = Settings(
        _env_file=None, mode='demo', llm_provider='demo', embedding_provider='demo',
        issue_provider='demo', langfuse_enabled=False, openai_api_key='',
        retailbridge_url='http://retailbridge.test',
    )
    corpus = documents(settings)
    graphs = {
        'standard': build_graph(settings, InMemorySaver()),
        'multi_agent_review': build_review_graph(settings, InMemorySaver()),
    }
    original_client = httpx.Client

    def fixture_client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(diagnostic_response)
        return original_client(*args, **kwargs)

    records = []
    with patch('app.integrations.httpx.Client', fixture_client):
        for index, ticket in enumerate(tickets):
            scoped = [doc for doc in corpus if doc['organization'] in ('both', ticket['organization'])]
            order = list(graphs) if index % 2 == 0 else list(reversed(graphs))
            for mode in order:
                graph = graphs[mode]
                run_config = {'configurable': {'thread_id': f'dev:{mode}:{ticket["id"]}'}, 'recursion_limit': 20}
                started = perf_counter()
                record = {'ticket_id': ticket['id'], 'group_id': ticket['group_id'], 'workflow_mode': mode,
                          'expected_category': ticket['expected']['category'],
                          'expected_next_step': ticket['expected']['next_step']}
                try:
                    result = graph.invoke({
                        'ticket_id': ticket['id'], 'organization_id': ticket['organization'],
                        'text': ticket['text'], 'language': ticket['language'],
                        'diagnostic_mode': ticket['diagnostic_mode'], 'documents': scoped,
                    }, run_config)
                    elapsed = round((perf_counter() - started) * 1000, 4)
                    snapshot = graph.get_state(run_config)
                    if not snapshot.interrupts:
                        raise AssertionError('The workflow failed to stop for human review')
                    analysis = result['analysis']
                    team = analysis.get('review_team') or {}
                    record.update({
                        'category': analysis['category'], 'next_step': analysis['next_step'],
                        'source_keys': [source['document_key'] for source in analysis['sources']],
                        'elapsed_ms': elapsed, 'runtime_error': None,
                        'review_status': team.get('status'), 'revisions': team.get('revisions', 0),
                        'disagreements': team.get('disagreements', []), 'warnings': analysis['warnings'],
                        'human_review_interrupt': True,
                    })
                except Exception as exc:
                    record.update({'runtime_error': type(exc).__name__, 'error_detail': str(exc),
                                   'elapsed_ms': round((perf_counter() - started) * 1000, 4)})
                records.append(record)

    metrics = {}
    for mode in graphs:
        rows = [row for row in records if row['workflow_mode'] == mode]
        completed = [row for row in rows if row['runtime_error'] is None]
        latencies = [row['elapsed_ms'] for row in completed]
        metrics[mode] = {
            'completed': len(completed), 'runtime_errors': len(rows) - len(completed),
            'next_step_matches': sum(row['next_step'] == row['expected_next_step'] for row in completed),
            'category_matches': sum(row['category'] == row['expected_category'] for row in completed),
            'latency_ms': {'p50': percentile(latencies, 0.5), 'p95': percentile(latencies, 0.95)},
        }
    pairs = {ticket['id']: [row for row in records if row['ticket_id'] == ticket['id'] and not row['runtime_error']] for ticket in tickets}
    paired = [rows for rows in pairs.values() if len(rows) == 2]
    agreement = sum(rows[0]['next_step'] == rows[1]['next_step'] for rows in paired)
    team_rows = [row for row in records if row['workflow_mode'] == 'multi_agent_review' and not row['runtime_error']]
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
        'mode': 'demo', 'split': 'dev', 'language': 'en', 'n': len(tickets),
        'scenario_groups': len({ticket['group_id'] for ticket in tickets}), 'development_sha256': digest,
        'metrics': metrics, 'paired_completed': len(paired), 'route_agreement': agreement,
        'changed_routes': len(paired) - agreement,
        'team_disagreements': sum(len(row['disagreements']) for row in team_rows),
        'team_revisions': sum(row['revisions'] for row in team_rows),
        'disagreement_codes': dict(Counter(item['code'] for row in team_rows for item in row['disagreements'])),
        'code_sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in (
            'backend/app/review_team.py', 'backend/app/workflow.py', 'backend/app/intelligence.py',
            'backend/app/retrieval.py', 'scripts/evaluate_review_team.py')},
        'records': records,
    }
    output = ROOT / 'data' / 'evaluation' / 'dev_review_team.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (ROOT / 'docs' / 'REVIEW_TEAM_EVALUATION.md').write_text(markdown(report), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('n', 'metrics', 'route_agreement', 'changed_routes', 'team_disagreements', 'team_revisions')}, indent=2))
    return 1 if any(metric['runtime_errors'] for metric in metrics.values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
