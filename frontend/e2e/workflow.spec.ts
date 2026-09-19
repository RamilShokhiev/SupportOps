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
