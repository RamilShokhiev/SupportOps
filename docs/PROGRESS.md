# Progress

Last reviewed: 2026-09-19. This file is the continuation point after a break. Do not restart the MVP.

## Done (working in-repo)

- Modular FastAPI backend + React SPA: tickets, import JSON, analyze, clarify, review, approve/edit/reject/execute/reconcile, knowledge upload (md/pdf), dashboard, CSV export.
- Two demo orgs (`northstar`, `contoso`), roles admin/agent/viewer, cookie sessions, org-scoped queries.
- LangGraph: diagnostics → analysis → human interrupt; SQLite or Postgres checkpointer; `POST /tickets/{id}/resume`.
- Intelligence adapters: explicit `demo` (deterministic EN/RU/TR rules) and `openai` (JSON schema, no tools). Server-side `next_step`.
- Hybrid retrieval (lexical + cosine RRF), product/version applicability, conflict groups, tenant-visible docs. No reranker (deferred).
- RetailBridge demo API (`retailbridge/main.py`) with normal/timeout/error. GitHub Issues adapter + in-app `DemoIssue` fallback. Execution claim committed before outbound POST; reconcile by marker, no second create.
- Authored corpus: 10 runbooks, 30 EN dev tickets, 150 held-out EN/RU/TR. Frozen hashes in `data/dataset_manifest.json`.
- `scripts/evaluate.py`: classification, Recall@5, next-step, mechanical source/fact checks, llm_only/rag/workflow; optional `--triage-only` against an external Keras artifact.
- Unit tests: `backend/tests/test_intelligence.py`. Optional Langfuse (scalars only, no ticket text).
- Fixed repository-relative configuration: `.env`, corpus and built UI now resolve inside SupportOps. PostgreSQL is the default; SQLite remains an explicit test/development option.
- Pinned Python direct dependencies and transitive constraints; npm lockfile with verified `npm ci`, typecheck and production build. Added `.env.example`, English README and `docs/DEMO.md` recording walkthrough.
- Docker Compose: PostgreSQL 17 + pgvector, one-shot migration/bootstrap service, built UI/API, synthetic RetailBridge, indexing worker, health checks and persistent database volume. Successfully built and started on Docker Desktop.
- Alembic initial migration with vector extension and SQLite compatibility; repeatable bootstrap seeds/indexes the synthetic corpus and initializes LangGraph checkpoints. Existing unversioned databases are not automatically stamped or destroyed.
- `backend/tests/test_scenarios.py`: 24 HTTP cases cover main branches, missing fields, conflicts, diagnostic failures, roles/tenants, revoked access, exact approval/hash binding, overlapping/repeated execute, uncertain results and durable crash recovery.
- Fixed workflow resume when a crash precedes the first checkpoint. Recovery after the diagnostic checkpoint keeps the saved diagnostic results.
- Fixed current-draft review after clarification: `review_pending` is separate from the first-review SLA timestamp; resolution requires the current review to finish.
- Fixed a PostgreSQL-only issue execution failure: the full UUID/hash marker is 131 characters, beyond the original `VARCHAR(100)` limit. Migration `0002` expands the field to 160; PostgreSQL tests cover normal creation and lost-response reconciliation after restart without a duplicate write.
- `backend/tests/test_bootstrap.py`: configuration paths, encoded database credentials, migration drift/roundtrip, idempotent bootstrap and PostgreSQL vector/checkpoint restart checks.
- Four Playwright Chromium tests use the real HTTP backend and synthetic diagnostics with temporary SQLite state. UI now respects current review status, lifecycle and role restrictions and opens synthetic issue links.
- Actual held-out demo evaluation and retained predictions/errors in `data/evaluation/`; `docs/EVALUATION.md` distinguishes rule proxies from LLMs and analysis timing from full workflow timing.
- Reviewed the existing English Triage code/weights read-only and evaluated them on 50 frozen EN tickets; `docs/TRIAGE_REVIEW.md` records fresh metrics, hashes and preprocessing limits.
- GitHub Actions configuration: backend + disposable PostgreSQL, UI typecheck/build/Chromium E2E, demo evaluation artifacts and Compose smoke jobs. The workflow has not yet been run on GitHub.

## Added: SupportOps Review Team (2026-09-19)

- Implemented the owner-approved opt-in `multi_agent_review` mode in the existing application. `standard` remains the default. Six roles share evidence: triage and diagnostics start in parallel, retrieval follows extraction, response waits for evidence, independent safety review may request one revision, and the coordinator applies conservative server routing before a durable human interrupt.
- Added separate structured OpenAI reviewer/revision calls using the existing adapter. Demo roles remain rules/templates; the UI names the provider and displays any local safety fallback. No paid requests or live quality claims.
- Added per-role reports, disagreements, draft history, usage/cost aggregation, one-revision/five-generation-attempt limits and a recursion cap of 20. Literal reviewer evidence references and citation IDs receive server validation. Diagnostic failures may be referenced as uncertainty but cannot establish API facts or authorize escalation.
- Migration `0003` stores the selected graph on each workflow run; existing runs remain standard. Review, crash recovery and clarification preserve the stored mode across application restarts and default changes.
- Added optional human feedback on draft/action decisions, durable audit/checkpoint storage, a six-role UI panel, saved feedback in the activity log, organization-scoped dashboard review counts and CSV columns. Feedback is collected for deliberate quality work; there is no automatic training or permissions change.
- Added mocked-provider graph tests, team API scenarios, migration/SQLite/PostgreSQL recovery checks, and two browser scenarios. CI now also runs the development comparison and team Compose smoke.
- Added `docs/REVIEW_TEAM.md` and a separate actual 30-EN-ticket development comparison (`docs/REVIEW_TEAM_EVALUATION.md`). Both graphs complete 30/30, agree on all routes, match expected routes 24/30 and categories 30/30. Team records three evidence concerns and zero revisions on this sample. This does not demonstrate improved accuracy. Existing held-out corpus and baseline outputs remain unchanged.
- The baseline repository was published at `https://github.com/RamilShokhiev/SupportOps` (`dfd79a4`). The Review Team slice is included in the follow-up commit on `main`.

## Next (remaining validation, not a rebuild)

1. Inspect the GitHub Actions run for this Review Team commit. No remote CI result is claimed until that run completes.
2. If explicitly authorized and configured, evaluate the live LLM/embedding adapter, create/reconcile an issue in a dedicated GitHub test repository, and verify Langfuse delivery. None is represented as already tested live.
3. Record the optional narrated 2–3 minute video using `docs/DEMO.md`; the walkthrough is ready, but no video has been recorded.
4. Further quality work must use development examples and a new held-out set. Preserve the frozen corpus and current failures; do not tune on this test set.

## Run

```powershell
# From E:\Projects\SupportOps. No provider keys are required.
Copy-Item .env.example .env  # Only if .env does not already exist.
docker compose up -d --build --wait --wait-timeout 180
# Open http://localhost:8000. Stop without removing stored data:
docker compose down
```

Demo password: `demo-supportops`. Accounts: `support@northstar.demo` (admin), `viewer@northstar.demo`, `support@contoso.demo`.

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe .\scripts\evaluate.py --split test
.\.venv\Scripts\python.exe .\scripts\evaluate_review_team.py  # Development only; does not overwrite the baseline.
.\.venv\Scripts\python.exe .\scripts\smoke.py  # Running all-demo stack; creates a synthetic ticket/issue.
.\.venv\Scripts\python.exe .\scripts\smoke.py --workflow-mode multi_agent_review
cd frontend
npm ci
npm run typecheck
npm run build
npx playwright install chromium
npm run test:e2e
```

The PostgreSQL tests are skipped unless `SUPPORTOPS_TEST_DATABASE_URL` points at a dedicated test database with extension permissions. Development startup, migrations, fallback SQLite and provider configuration are described in `README.md`.

`--live` and GitHub execute need explicit authorization, keys and a dedicated test repository. Do not commit secrets.

## Baseline verified on 2026-09-19 (before Review Team)

- Python 3.13.15: **56 passed** in the final run, including PostgreSQL migration/vector/checkpoint and normal/lost-response issue execution tests against a dedicated `supportops_test` database; no skipped tests. One upstream Starlette/AnyIO deprecation warning.
- `pip check`: no broken requirements. Container dependency installation and `pip check` also succeeded on Linux Python 3.13.
- npm clean install, TypeScript check and Vite production build: passed.
- Playwright Chromium: **4 passed** using isolated SQLite and real synthetic HTTP diagnostics.
- `docker compose config --quiet` and fresh/repeated `docker compose up -d --build --wait`: passed; migrations completed and API, RetailBridge and PostgreSQL became healthy; worker started. The running-stack HTTP smoke passed cited analysis, exact approval, one synthetic issue despite replay, CSV and tenant isolation.
- Running application schema is `0002`; `alembic check` reports no drift. The built UI returns HTTP 200 at `http://localhost:8000`. The local stack is left running for review.
- Demo evaluation: 150 tickets (50 EN/RU/TR), workflow/API-fixture next-step 90.0%, Recall@5 99.1%, category macro-F1 0.9149. EN/RU/TR next-step: 92% / 88% / 90%. Timings are analysis-only and facts are checked mechanically, not human-adjudicated.
- Existing CNN Triage: 50 EN tickets, category macro-F1 0.3235, 20/50 correct, no retraining or source-repository changes.

## Review Team verification on 2026-09-19

- Backend full suite: 96 passed, including four PostgreSQL cases on the dedicated `supportops_test` database. Four additional regression cases were then added for cited failed diagnostics; all 21 mocked-provider engine cases pass. One upstream Starlette/AnyIO deprecation warning remains.
- TypeScript and Vite production build passed. Playwright Chromium: six passed, including both new Review Team scenarios. Detail and dashboard screenshots inspected.
- Docker Compose rebuilt successfully with existing data preserved. Running schema is `0003`; `alembic check` reports no drift. API, PostgreSQL and RetailBridge are healthy; worker is running.
- Both standard and team running-stack smoke checks passed against actual PostgreSQL and synthetic HTTP diagnostics: six recorded roles in team mode, cited draft, exact approval with feedback, one synthetic issue per smoke despite replay, CSV and tenant isolation.
- Local application is left running at `http://localhost:8000`. Select **Review team** before analyzing a new ticket. Existing analyzed tickets retain their saved workflow mode.
- Development timing in the saved run (in-memory checkpoints/mock HTTP, 30 tickets): standard p50/p95 11.00/16.15 ms; team 16.56/19.90 ms. These are local graph timings and do not represent production or paid-model latency.

## Known limitations

- Demo embeddings are feature hashes, not a trained multilingual model. Demo LLM is rules/templates.
- Live model quality, external GitHub writes and Langfuse delivery are unverified; no API keys or paid calls were used.
- Retrieval scores the scoped small corpus in Python. The worker is intended as one process. Docker base image tags can receive patch updates; application package versions are pinned.
- Cost reporting is an analysis subtotal, not a complete provider invoice. There is no global dollar quota. The current evaluation is synthetic, correlated across translations and has no native-speaker/human factuality adjudication.
- Bootstrap upgrades fresh/versioned databases. A legacy unversioned `create_all` database needs backup and schema review before adoption (README).
- Knowledge `data-export` is Northstar-only by design. Upgrade 3.9 A/B conflict must clarify, never auto-resolve.
- Escalation success is not ticket resolution.
