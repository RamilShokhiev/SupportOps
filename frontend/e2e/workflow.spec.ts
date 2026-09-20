import { expect, test, type Page } from '@playwright/test';

async function signIn(page: Page, email = 'support@northstar.demo') {
  await page.goto('/');
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill('demo-supportops');
  await page.getByRole('button', { name: 'Sign in to workspace' }).click();
  await expect(page.getByRole('heading', { name: 'Ticket inbox' })).toBeVisible();
}

async function createTicket(page: Page, subject: string, message: string, diagnostic = 'normal') {
  await page.getByRole('button', { name: 'New ticket', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'New support ticket' });
  await dialog.getByLabel('Subject', { exact: true }).fill(subject);
  await dialog.getByLabel('Customer message').fill(message);
  await dialog.getByLabel('Diagnostic scenario').selectOption(diagnostic);
  const response = page.waitForResponse(r => r.url().endsWith('/api/tickets') && r.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Create ticket', exact: true }).click();
  const ticket = await (await response).json();
  await expect(page.getByRole('heading', { name: subject, exact: true })).toBeVisible();
  return ticket as { id: string };
}

async function analyze(page: Page) {
  await page.getByRole('button', { name: 'Analyze ticket', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: 'Extracted details' })).toBeVisible();
}

test('review an exact version, create one synthetic issue, and resolve separately', async ({ page }) => {
  await signIn(page);
  const ticket = await createTicket(page, 'E2E checkout escalation',
    'After upgrading RetailBridge to 3.8, three stores cannot complete checkout. Error E-214.');
  await expect(page.getByRole('button', { name: 'Mark resolved' })).toBeDisabled();
  await analyze(page);
  await expect(page.getByRole('heading', { name: 'Escalate to engineering' })).toBeVisible();
  await expect(page.getByText('Demo mode: deterministic', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Execute approved action' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Accept draft' })).toBeDisabled();
  await page.getByRole('button', { name: 'Approve v1', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Execute approved action' })).toBeEnabled();
  await page.getByLabel('Issue title', { exact: true }).fill('[RetailBridge] E-214: reviewed engineering report');
  await expect(page.getByRole('button', { name: 'Execute approved action' })).toBeDisabled();
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve v2', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Execute approved action' })).toBeDisabled();
  await page.getByRole('button', { name: 'Approve v2', exact: true }).click();
  await page.getByRole('button', { name: 'Execute approved action' }).click();
  await expect(page.getByText('Engineering issue created', { exact: true })).toBeVisible();
  await expect(page.getByText('Provider mode: demo')).toBeVisible();
  await expect(page.locator('.detail-kicker')).toContainText('Escalated');
  const issueLink = page.getByRole('link', { name: 'Open issue' });
  await expect(issueLink).toHaveAttribute('href', /^\/api\/demo-issues\/\d+$/);
  const issue = await page.request.get((await issueLink.getAttribute('href'))!);
  expect(await issue.text()).toContain('SYNTHETIC ISSUE #');
  expect(await issue.text()).toContain('reviewed engineering report');
  await page.reload();
  await page.getByRole('button', { name: 'E2E checkout escalation', exact: true }).click();
  await expect(page.getByText('Engineering issue created', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Execute approved action' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Mark resolved' }).click();
  await expect(page.locator('.detail-kicker')).toContainText('Resolved');
  const result = await (await page.request.get(`/api/tickets/${ticket.id}`)).json();
  expect(result.events.filter((e: { event_type: string }) => e.event_type === 'execution_succeeded')).toHaveLength(1);
});

test('missing details can be supplied before a grounded answer is reviewed', async ({ page }) => {
  await signIn(page);
  await createTicket(page, 'E2E missing product version', 'Our registers cannot complete checkout. Please help.');
  await analyze(page);
  await expect(page.getByRole('heading', { name: 'Ask for missing details' })).toBeVisible();
  await expect(page.getByText('Still needed:', { exact: false })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Engineering action' })).toHaveCount(0);
  await page.getByLabel('Customer clarification').fill('RetailBridge 3.7 shows E-214 at one store.');
  await page.getByRole('button', { name: 'Save clarification' }).click();
  await expect(page.getByRole('heading', { name: 'Review the response' })).toBeVisible();
  await expect(page.locator('.sources-list')).toContainText('3.7 only');
  await expect(page.getByRole('button', { name: 'Mark resolved' })).toBeDisabled();
  await page.getByRole('button', { name: 'Accept draft' }).click();
  await expect(page.getByRole('button', { name: 'Mark resolved' })).toBeEnabled();
  await page.getByRole('button', { name: 'Mark resolved' }).click();
  await expect(page.locator('.detail-kicker')).toContainText('Resolved');
});

test('diagnostic failure requests clarification and never permits execution', async ({ page }) => {
  await signIn(page);
  await createTicket(page, 'E2E unavailable diagnostics',
    'RetailBridge 3.8 checkout is blocked with E-214 at three stores.', 'error');
  await analyze(page);
  await expect(page.getByRole('heading', { name: 'Ask for missing details' })).toBeVisible();
  await expect(page.locator('.diagnostics-list .badge.error')).toHaveCount(2);
  await expect(page.getByRole('heading', { name: 'Engineering action' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Execute approved action' })).toHaveCount(0);
});

test('viewer cannot mutate and a second organization cannot see private tickets or knowledge', async ({ page }) => {
  await signIn(page);
  const ticket = await createTicket(page, 'Northstar private E2E case',
    'RetailBridge 3.8 E-214 blocks checkout at three stores.');
  await page.getByRole('button', { name: 'Sign out' }).click();
  await signIn(page, 'viewer@northstar.demo');
  await expect(page.getByRole('button', { name: 'New ticket', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Import', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Northstar private E2E case', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Analyze ticket', exact: true }).first()).toBeDisabled();
  expect((await page.request.post(`/api/tickets/${ticket.id}/analyze`, { data: {} })).status()).toBe(403);
  await page.getByRole('button', { name: 'Knowledge base', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Add document' })).toBeDisabled();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await signIn(page, 'support@contoso.demo');
  await expect(page.getByRole('button', { name: 'Northstar private E2E case', exact: true })).toHaveCount(0);
  expect((await page.request.get(`/api/tickets/${ticket.id}`)).status()).toBe(404);
  await page.getByRole('button', { name: 'Knowledge base', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Add document' })).toBeDisabled();
  await expect(page.locator('.document-grid')).toBeVisible();
  await expect(page.locator('.document-grid')).not.toContainText('data-export');
});

test('review team reports six roles and keeps human action approval and feedback after reload', async ({ page }, testInfo) => {
  await signIn(page);
  const subject = 'E2E review team checkout';
  const ticket = await createTicket(page, subject,
    'After upgrading RetailBridge to 3.8, three stores cannot complete checkout. Error E-214.');
  await expect(page.getByLabel('Analysis mode', { exact: true })).toHaveValue('standard');
  await page.getByLabel('Analysis mode', { exact: true }).selectOption('multi_agent_review');
  const analysisRequest = page.waitForRequest(r => r.url().endsWith(`/tickets/${ticket.id}/analyze`));
  await analyze(page);
  expect((await analysisRequest).postDataJSON()).toEqual({ workflow_mode: 'multi_agent_review' });
  const team = page.getByRole('region', { name: 'Review team results' });
  await expect(team.getByText('Demo team: rules-based simulation. No live model calls.', { exact: true })).toBeVisible();
  await expect(team.getByRole('article')).toHaveCount(6);
  for (const role of ['Triage', 'Knowledge', 'Diagnostics', 'Response', 'Safety', 'Coordinator']) {
    await expect(team.getByRole('article', { name: `${role} review`, exact: true })).toBeVisible();
  }
  await expect(page.getByLabel('Analysis mode', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Analysis mode: Review team', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Execute approved action' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Mark resolved' })).toBeDisabled();
  await team.getByText('Draft review history', { exact: true }).click();
  await expect(team.getByRole('heading', { name: 'Initial draft' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('review-team.png'), fullPage: true });
  await page.getByLabel('Action review feedback (optional)').fill('Version and diagnostic evidence checked by the support lead.');
  await expect(page.getByLabel('Action review feedback (optional)')).toHaveAttribute('maxlength', '1000');
  await page.getByRole('button', { name: 'Approve v1', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Execute approved action' })).toBeEnabled();
  await page.reload();
  await page.getByRole('button', { name: subject, exact: true }).click();
  await expect(page.getByRole('region', { name: 'Review team results' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Execute approved action' })).toBeEnabled();
  await page.getByRole('button', { name: /Activity log/ }).click();
  await expect(page.locator('.human-review-note')).toContainText('Version and diagnostic evidence checked by the support lead.');
  const saved = await (await page.request.get(`/api/tickets/${ticket.id}`)).json();
  expect(saved.workflow_mode).toBe('multi_agent_review');
  expect(saved.review_pending).toBe(false);
  expect(saved.action.status).toBe('approved');
  expect(saved.action.execution).toBeNull();
  expect(saved.events.some((event: { event_type: string; payload: { reason?: string; workflow_mode?: string } }) =>
    event.event_type === 'review_completed' && event.payload.workflow_mode === 'multi_agent_review' &&
    event.payload.reason === 'Version and diagnostic evidence checked by the support lead.')).toBe(true);
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  const overview = page.getByRole('region', { name: 'Review team overview' });
  await expect(overview.getByRole('heading', { name: 'Review team outcomes' })).toBeVisible();
  await expect(overview).toContainText('Counts include repeated runs; they do not measure answer quality.');
  await expect(overview).toContainText('Action Approved');
  const dashboard = await (await page.request.get('/api/dashboard')).json();
  expect(dashboard.review_team.tickets).toBeGreaterThanOrEqual(1);
  expect(dashboard.review_team.human_decisions.action_approved).toBeGreaterThanOrEqual(1);
  await page.screenshot({ path: testInfo.outputPath('review-team-overview.png'), fullPage: true });
});

test('review team keeps its mode through clarification and records draft rejection and acceptance', async ({ page }) => {
  await signIn(page);
  const ticket = await createTicket(page, 'E2E review team missing details',
    'Our registers cannot complete checkout. Please help.');
  await page.getByLabel('Analysis mode', { exact: true }).selectOption('multi_agent_review');
  await analyze(page);
  await expect(page.getByRole('heading', { name: 'Ask for missing details' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Review team results' })).toContainText('Needs human review');
  await page.getByLabel('Review feedback (optional)', { exact: true }).fill('Ask for the installed product version first.');
  await page.getByRole('button', { name: 'Reject draft' }).click();
  await expect(page.getByRole('button', { name: 'Accept draft' })).toBeDisabled();
  await page.getByLabel('Customer clarification').fill('RetailBridge 3.7 shows E-214 at one store.');
  await page.getByRole('button', { name: 'Save clarification' }).click();
  await expect(page.getByRole('heading', { name: 'Review the response' })).toBeVisible();
  await expect(page.getByText('Analysis mode: Review team', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Review team results' }).getByRole('article')).toHaveCount(6);
  await expect(page.getByRole('button', { name: 'Mark resolved' })).toBeDisabled();
  await page.getByLabel('Review feedback (optional)', { exact: true }).fill('The revised context and 3.7 source now support this answer.');
  await page.getByRole('button', { name: 'Accept draft' }).click();
  await expect(page.getByRole('button', { name: 'Mark resolved' })).toBeEnabled();
  const saved = await (await page.request.get(`/api/tickets/${ticket.id}`)).json();
  expect(saved.workflow_mode).toBe('multi_agent_review');
  expect(saved.analysis.review_team.roles).toHaveLength(6);
  const reviews = saved.events.filter((event: { event_type: string }) => event.event_type === 'review_completed');
  expect(reviews.map((event: { payload: { reason: string } }) => event.payload.reason)).toContain('Ask for the installed product version first.');
  expect(reviews.map((event: { payload: { reason: string } }) => event.payload.reason)).toContain('The revised context and 3.7 source now support this answer.');
});
