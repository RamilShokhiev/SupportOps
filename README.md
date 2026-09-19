# SupportOps AI

A local portfolio application for support teams working with **RetailBridge, a fictional retail product**. Every included ticket, runbook, diagnostic response and demo issue is synthetic.

Create a ticket, inspect extracted fields and applicable sources, check the product's diagnostic API, then review an answer, clarification request or engineering proposal. The backend enforces roles and organization isolation. An external write requires approval of the exact proposal version and content hash. Escalation and problem resolution are separate events.

## Start the local stack

Requirements: Docker Desktop with Linux containers and Docker Compose 2.24 or newer. Ports 8000, 8001 and 5432 must be available. No API keys are required.

```powershell
cd E:\Projects\SupportOps
Copy-Item .env.example .env  # First run only; preserve an existing .env.
docker compose up -d --build --wait --wait-timeout 180
```

Open [SupportOps](http://localhost:8000). The backend serves the built React UI. API documentation is at [localhost:8000/docs](http://localhost:8000/docs); synthetic RetailBridge documentation is at [localhost:8001/docs](http://localhost:8001/docs).

| Demo account | Organization | Role |
|---|---|---|
| `support@northstar.demo` | Northstar Retail | Administrator |
| `viewer@northstar.demo` | Northstar Retail | Viewer |
| `support@contoso.demo` | Contoso Markets | Agent |

Password: `demo-supportops`. These credentials and localhost bindings are for local synthetic data. `DEMO_PASSWORD` sets the password when accounts are first seeded; changing it does not update existing accounts.

Compose starts PostgreSQL 17 with pgvector, applies Alembic migrations, seeds two organizations and the knowledge library, initializes durable LangGraph checkpoints, starts RetailBridge and the API, and then starts the indexing worker. Startup dependencies and health checks enforce that order. PostgreSQL data and checkpoints live in the named `supportops_postgres_data` volume.

```powershell
docker compose ps
docker compose logs --tail 100 api worker migrate
docker compose down  # Stops the stack and retains its data volume.
```

The database password in `.env.example` is a local demo default. If changing `POSTGRES_PASSWORD`, also update the host-side `DATABASE_URL`. Compose passes separate connection fields (`POSTGRES_HOST=db`, user, password, port and database); the app builds an encoded URL and overrides the host-side URL. Use URL-encoded credentials when writing a URL manually. An existing PostgreSQL volume retains its original password.

## Architecture and trust boundaries

```mermaid
flowchart LR
    UI[React review workspace] --> API[FastAPI: session, role and tenant checks]
    API --> DB[(PostgreSQL + pgvector)]
    API --> Graph[LangGraph: diagnostics, analysis, human interrupt]
    Graph --> RB[RetailBridge synthetic read API]
    Graph --> Intelligence[Demo or OpenAI adapter: no tools]
    Graph --> Checkpoints[(Durable checkpoints)]
    Worker[Embedding worker] --> DB
    API --> Approval[Version + hash approval and execution log]
    Approval --> Issues[Demo issue store or permitted GitHub repository]
```

Backend routing, permissions, queues, priorities and SLA rules are authoritative. Retrieved text and the model cannot grant privileges. Hybrid retrieval filters organization, product and version before lexical/cosine reciprocal-rank fusion. The small corpus is scored in Python; PostgreSQL stores its 256-dimensional vectors. There is no reranker or large-corpus vector index.

Unknown product/version fields stay empty. Category, unconfirmed hypothesis and copied evidence are separate. A failed diagnostic read means unknown service health. Conflicting 3.9 upgrade runbooks require clarification. Error E-214 has different guidance for RetailBridge 3.7 and 3.8.

Proposals are editable before execution, and edits invalidate approval. The operation claim is committed before the outbound POST. A lost response produces a review/reconciliation path, never an automatic second create. GitHub reconciliation scans up to 500 recent issues for the operation marker; a missing result remains unresolved. This is bounded recovery, not a guarantee of external exactly-once delivery.

## Develop without containers for the application

Use Python 3.13 and Node.js 22. The direct dependencies and their transitive constraints are in `backend/requirements.txt`, `backend/requirements-dev.txt` and `backend/requirements.lock`; npm uses `frontend/package-lock.json`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
docker compose up -d db
.\.venv\Scripts\python.exe -m backend.app.bootstrap
```

Run each service in its own terminal from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn retailbridge.main:app --host 127.0.0.1 --port 8001
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m backend.app.worker
```

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

The development UI at [localhost:5173](http://localhost:5173) proxies `/api` to port 8000. On Linux/macOS replace `.venv\Scripts\python.exe` with `.venv/bin/python`. To serve the UI directly from FastAPI, run `npm run build` and restart the backend.

For an explicit SQLite fallback, set `DATABASE_URL=sqlite:///./supportops.db` in `.env` before bootstrapping. SQLite checkpoints default to the repository's `checkpoints.db`; `CHECKPOINT_SQLITE_PATH` overrides this. SQLite is useful for tests and lightweight local work; Compose remains the PostgreSQL default. Paths to `.env`, `data/` and `frontend/dist/` resolve from the repository, not its parent.

## Schema changes and existing data

`python -m backend.app.bootstrap` runs `alembic upgrade head`, idempotent demo seeding, demo indexing and checkpoint setup. The normal API entrypoint expects this initialization to have completed. `create_app(initialize=True)` remains an isolated-test convenience, not the deployment path.

```powershell
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "Describe schema change"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Review generated revisions before applying them. Alembic owns application tables; LangGraph's checkpointer owns its checkpoint migrations. Bootstrap does not drop tables or automatically stamp an unversioned legacy database. Back up an older database created with `create_all`, compare its schema with revision `0001`, and only stamp `0001` after confirming equivalence; then upgrade to head. Revision `0002` widens the synthetic issue marker to store its UUID and full content hash on PostgreSQL. Do not run a destructive downgrade on data you want to keep.

## Configuration and optional adapters

The default `MODE`, `LLM_PROVIDER`, `EMBEDDING_PROVIDER` and `ISSUE_PROVIDER` are all `demo`. The UI and `/api/health` expose provider modes. Demo language processing uses deterministic templates/rules; demo embeddings use glossary feature hashes. Neither is a live multilingual model. RetailBridge remains a synthetic diagnostic service even when a model or GitHub adapter is enabled.

| Setting | Purpose |
|---|---|
| `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL` | Enable the existing real structured-output adapter |
| `EMBEDDING_PROVIDER=openai`, `EMBEDDING_MODEL` | Real embeddings; keep `EMBEDDING_DIMENSIONS=256` for this schema |
| `INPUT_COST_PER_MILLION`, `OUTPUT_COST_PER_MILLION`, `EMBEDDING_COST_PER_MILLION` | Explicit prices for cost estimates; unset live prices mean unknown cost |
| `ISSUE_PROVIDER=github`, `GITHUB_TOKEN` | Enable real issue creation after human approval |
| `GITHUB_REPOSITORIES={"northstar":"owner/dedicated-test-repo"}` | Server-owned organization-to-test-repository allowlist |
| `DEMO_ISSUE_MODE=timeout_after` | Simulate a lost response after a synthetic issue is stored |
| `LANGFUSE_ENABLED=true`, project keys and `LANGFUSE_BASE_URL` | Opt-in scalar tracing; ticket/document text is excluded |
| `API_TIMEOUT_SECONDS`, `LLM_TIMEOUT_SECONDS`, `MAX_OUTPUT_TOKENS` | Bound individual requests and generated output |

Enable paid adapters deliberately and use a dedicated authorized GitHub test repository. Changing the embedding provider does not rewrite existing vectors: upload new document revisions to enqueue indexing with the selected provider. Documents indexed by another model are excluded from analysis. The worker is a single-process deployment and recovers interrupted indexing jobs on startup.

Read-only diagnostic calls retry at most twice; graph recursion is capped. These bounds limit individual operations, but there is no account-wide dollar quota. Dashboard costs are known analysis subtotals, excluding indexing and unmetered failed calls; they are not a provider invoice. SLA is first human review on a 24/7 UTC schedule (P1: 1 hour, P2: 4 hours, P3: 8 hours), not time to a customer response.

## Verification and evaluation

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/evaluate.py --split test
.\.venv\Scripts\python.exe scripts/smoke.py  # Requires the running all-demo stack; creates one synthetic ticket/issue.
cd frontend
npm ci
npm run typecheck
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright launches its own temporary SQLite database and real synthetic HTTP diagnostics, serves the built UI, and uses port 18765. Set `SUPPORTOPS_E2E_PORT` or `SUPPORTOPS_PYTHON` if needed. It does not reuse a running development server. API scenarios cover authorization and tenant isolation, approval invalidation, duplicate/concurrent execution, diagnostic errors, conflicts and crash recovery. Migration tests check fresh boot, repeat boot and upgrade/downgrade on a disposable database.

The optional PostgreSQL test requires `SUPPORTOPS_TEST_DATABASE_URL` pointing at a **dedicated test database** with extension-creation privileges. It checks pgvector storage, migration drift and a checkpoint resumed after application restart. CI supplies that database and additionally runs browser and Compose startup jobs. The Triage baseline is optional and not installed with runtime dependencies.

To include the PostgreSQL checks locally with the default demo password:

```powershell
docker compose exec -T db createdb -U supportops supportops_test  # Once, if this test database does not exist.
$env:SUPPORTOPS_TEST_DATABASE_URL = 'postgresql+psycopg://supportops:supportops-local@127.0.0.1:5432/supportops_test'
.\.venv\Scripts\python.exe -m pytest -q
```

These checks also execute and reconcile synthetic issues across an app restart on PostgreSQL, enforcing the real column constraints. The test database is separate from the demo workspace and can be reused.

Actual synthetic held-out results (150 tickets, 50 per language; correlated translation groups): workflow/API fixture next-step accuracy **90.0%**, macro Recall@5 **99.1%**, category macro-F1 **0.9149**. This is a deterministic demo evaluation, not a live LLM benchmark. Existing English CNN Triage category macro-F1 is **0.3235** on a fresh 50-ticket evaluation. Frozen failures are retained and the test set was not used for tuning.

- [Evaluation methods, per-language results and limits](docs/EVALUATION.md)
- [Existing Triage code/model review and fresh comparison](docs/TRIAGE_REVIEW.md)
- [Three-minute demo walkthrough](docs/DEMO.md)
- [Current completion status, checks and remaining limits](docs/PROGRESS.md)

No live OpenAI, GitHub write or Langfuse delivery result is claimed. Live accuracy, native-speaker review and an independently annotated factuality assessment remain unmeasured.

## Technical references

The implementation uses [Alembic versioned migrations](https://alembic.sqlalchemy.org/en/latest/tutorial.html), the [SQLAlchemy psycopg dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg), [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), and [pgvector's PostgreSQL extension](https://github.com/pgvector/pgvector). The local checks above validate this repository's pinned combination.
