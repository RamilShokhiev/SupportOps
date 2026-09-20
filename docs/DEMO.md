# Three-minute synthetic demo walkthrough

Start the stack using the README. Sign in as `support@northstar.demo` with `demo-supportops`. Keep the provider badge visible. All examples describe fictional RetailBridge behavior.

## 0:00–1:15 — Evidence, approval and execution

1. Open **New ticket**. Subject: `Checkout blocked after upgrade`. Message: `After upgrading RetailBridge to 3.8, three stores cannot complete checkout. Error E-214.`
2. Select normal diagnostics and create the ticket. Click **Analyze ticket**.
3. Show product/version/error fields, the incident category, the unconfirmed hypothesis, the 3.8 source excerpt and the degraded synthetic checkout diagnostic. The ticket is P1 and awaits approval.
4. Show that **Execute approved action** is unavailable before approval. Approve version 1, edit the issue title and save. The proposal becomes version 2 and requires a new approval.
5. Approve version 2 and execute. Open the clearly labelled synthetic issue. Reload to show its stored result. The ticket is escalated; only a later human **Mark resolved** records resolution.

## 1:15–2:00 — Missing details and failed diagnostics

1. Create `Our registers cannot complete checkout. Please help.` and analyze it. Product and version remain unknown. The system requests clarification and proposes no engineering write.
2. Add `RetailBridge 3.7 shows E-214 at one store.` as the customer clarification. Show that the applicable source is the 3.7 runbook, then review the draft.
3. Optionally create the original 3.8 ticket with diagnostic scenario **error**. Failed reads request clarification; they never become a healthy status. Alternatively use `RetailBridge 3.9 upgrade plan` to show the two conflicting instructions.

## 2:00–3:00 — Recover a lost creation response

Prepare this segment by setting `DEMO_ISSUE_MODE=timeout_after` in `.env` and recreating the API:

```powershell
docker compose up -d --force-recreate api
```

1. Create a fresh 3.8 E-214 ticket, analyze and approve its exact version.
2. Execute. The synthetic issue is stored, but its response is deliberately lost. Show the **needs review** result.
3. Restart the API with `docker compose restart api`. Sign in again if necessary and reopen the ticket.
4. Click **Reconcile**. It finds the existing issue by its persisted operation marker and records success. No second create is sent.
5. Open the dashboard and CSV export. Explain that SLA measures first human review, and escalation is separate from resolution.

Restore `DEMO_ISSUE_MODE=normal` and recreate the API afterward. These are local synthetic writes; this sequence does not create a GitHub issue.

## Optional supporting scenes

- Sign in as `viewer@northstar.demo` to show read-only controls. Use `support@contoso.demo` to show a different queue and knowledge scope.
- Upload a small Markdown runbook as the Northstar administrator. Wait for the worker to mark the new revision ready.
- Create another 3.8 E-214 ticket, choose **Review team**, analyze, and show the six role reports. Human approval is still required before execute. Do not present demo-rules team output as a live multi-model result.
- Show `docs/EVALUATION.md` with separate EN/RU/TR scores and the retained errors. Do not call demo-rules scores LLM scores.

This file is a recording script. A narrated video has not been recorded.
