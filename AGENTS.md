# SupportOps — agent map

Read this file, then `SupportOps_AI_project_plan.md` and `docs/PROGRESS.md`. Do not reconstruct the architecture by walking the tree unless you are changing that module.

## Codex contract

You are implementing the **remaining MVP gaps** in this repository (`e:\Projects\SupportOps`). Owner: Ramil. Explain substantial decisions in short Russian. Code, identifiers, comments that ship, and README stay English.

- Do **not** create `supportops-ai` or another folder. This repo is SupportOps AI.
- Do **not** discard existing work. Keep uncommitted changes. Continue from `docs/PROGRESS.md`.
- The spec is `SupportOps_AI_project_plan.md`. If a requirement is already implemented, leave it and move to the next gap.
- No client search, sales, or cloud spend unless Ramil asks.
- Demo adapters must stay clearly labelled. Never report demo-rules output as a live LLM or live GitHub result.
- After each meaningful slice: update `docs/PROGRESS.md` (done, next, run commands, limits).

## Spec vs this repo

| Spec item | Status |
|---|---|
| Ticket queue, form, JSON import | Done (`main.py`, `App.tsx`) |
| PostgreSQL + pgvector as default local store | Done: Compose default, Alembic migration/bootstrap, vector/checkpoint integration test |
| Extract fields; empty unknowns; category ≠ hypothesis ≠ facts | Done (`intelligence.py`) |
| RAG md/pdf, versions, hybrid search, org+version filter, citations | Done; no reranker (correct until a measured baseline) |
| RetailBridge demo API + timeout/error | Done (`retailbridge/`, `integrations.py`) |
| LangGraph answer/clarify/escalate + durable resume | Done (`workflow.py`, `/resume`) |
| Action card, versioned approval, GitHub adapter, reconcile | Done (`services.py`) |
| EN/RU/TR eval + optional EN Triage compare | Done: actual demo and EN Triage reports/artifacts; live LLM not evaluated |
| Auth, roles, two orgs | Done (`seed.py`) |
| Dashboard + CSV from code | Done |
| Tracing / Langfuse | Partial: opt-in scalars in `observability.py` |
| Docker Compose, CI, README, `.env.example`, pinned deps | Done locally; CI workflow awaits a remote GitHub run |
| Scenario tests (isolation, unapproved execute, replay, crash) | Done: API scenarios, migrations/Postgres and four Chromium E2E tests |

## Layout

```text
SupportOps/
├── AGENTS.md
├── SupportOps_AI_project_plan.md
├── docs/PROGRESS.md
├── backend/app/           FastAPI (behavior source of truth)
│   ├── main.py            HTTP, auth, upload, dashboard
│   ├── config.py          Settings; repository-relative data/UI paths
│   ├── models.py          Schema
│   ├── services.py        Analyze / approve / execute / reconcile
│   ├── workflow.py        diagnostics → analysis → human_review interrupt
│   ├── intelligence.py    extract, retrieve, next_step, draft (demo|openai)
│   ├── retrieval.py       applicability + lexical/cosine RRF
│   ├── integrations.py    RetailBridge GET + issue create
│   ├── worker.py          embedding indexer
│   └── seed.py            demo orgs/users/docs/tickets
├── backend/tests/test_intelligence.py
├── frontend/src/App.tsx   SPA
├── retailbridge/main.py   :8001 synthetic product API
├── data/                  frozen corpus — do not fit test labels
└── scripts/               build_dataset.py, evaluate.py
```

## Runtime

| Process | Port | Notes |
|---|---|---|
| Backend | 8000 | `create_app()` after `python -m backend.app.bootstrap`; Compose automates startup |
| Vite | 5173 | proxies `/api` → 8000 |
| RetailBridge | 8001 | JSON must include `synthetic: true` |
| Worker | — | `pending` docs → 256-d embeddings |

Demo password `demo-supportops`: `support@northstar.demo` admin, `viewer@northstar.demo` viewer, `support@contoso.demo` other tenant.

**Paths:** `config.py` now resolves `PROJECT_ROOT` to this repository. Data, built UI and `.env` use that root. Compose uses `POSTGRES_HOST` and separate credentials to construct an encoded database URL; native runs can use `DATABASE_URL` directly.

## Ticket lifecycle

```text
new/failed → analyze → LangGraph (diagnostics, analysis, interrupt)
  clarify  → awaiting_clarification
  answer   → ready (human draft accept/reject)
  escalate → awaiting_approval → approve exact version → execute once
             success → escalated (not resolved)
             uncertain → needs_review → reconcile (no second POST)
resolve only after human review on ready|escalated
```

Approval binds `version` + `content_hash`. Edit bumps version. SLA `due_at` is first human review (P1 1h / P2 4h / P3 8h UTC). Incident + ≥3 stores → P1.

## Intelligence invariants

The model has **no tools**. `next_step` is `_next_step()` on the server:

- missing product/version, no applicable source, `conflict_group`, failed diagnostics, or Incident/Problem without diagnostics → `clarify`
- degraded/down service **or** category Problem → `escalate`
- else `answer`

Identifiers not literally in the ticket are cleared. Excerpts are copied, not paraphrased as evidence. Recency does not break a document conflict (upgrade-v39 A vs B). Failed API ≠ healthy. Demo embeddings are glossary hashes, not a multilingual model.

Corpus traps: E-214 3.7 vs 3.8 differ; `data-export` is Northstar-only; SupportOps never grants roles or executes printer/stock writes.

## Eval freeze

`data/dataset_manifest.json`. Do not tune on `test_tickets.json`. Optional Triage: `--triage-only --triage-root` to the existing Keras project; ASCII-only EN category head; do not reuse old metrics.

## Where to edit

| Change | Files |
|---|---|
| Packaging (Compose, CI, README, pins) | root configuration; keep app behavior |
| Config paths / boot | `config.py`, `main.py` |
| API / auth | `main.py`, `schemas.py` |
| Execute / GitHub | `services.py`, `integrations.py` |
| Routing / drafts | `intelligence.py` |
| RAG | `retrieval.py`, `worker.py` |
| UI | `frontend/src/App.tsx` |
| Corpus | `scripts/build_dataset.py` then regenerate hashes |
