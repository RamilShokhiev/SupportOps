# SupportOps AI — technical specification

Portfolio MVP for a synthetic RetailBridge support workflow. Client search, sales, and commercial pilots are out of scope.

**This file is the product spec.** The repository already contains a substantial implementation. Do not create a sibling project. Extend remaining gaps in `docs/PROGRESS.md`. Architecture and file map: `AGENTS.md`.

## Product

SupportOps processes tickets for the fictional product **RetailBridge** (retail POS software and integrations). All documents and tickets are synthetic and must stay labelled as such.

End-to-end path: an agent enters a ticket → the system extracts fields and classifies → retrieves applicable instructions → checks facts via API → prepares an answer, clarification, or engineering issue → a human reviews the action → the app executes only the approved operation and stores the result.

Canonical example (fictional): after upgrading RetailBridge to 3.8, checkout at three stores cannot complete payment; error E-214. The meaning of E-214 is defined only in our knowledge documents and differs by product version.

## Required capabilities

1. Ticket queue, create form, JSON import, durable state in PostgreSQL.
2. Structured extraction of product, version, symptoms, error code, and scale. Unknown fields stay empty. Category, causal hypothesis, and confirmed facts are separate.
3. RAG: Markdown then text PDFs; document versions; embeddings; lexical and semantic search; organization and product-version filters; verifiable source citations. Add a reranker only after a useful baseline comparison.
4. Separate RetailBridge demo API: `get_service_status`, `get_recent_changes`, with normal responses, timeouts, and errors.
5. LangGraph workflow with answer / clarify / escalate branches, durable checkpoints, resume after restart.
6. Action review card: edit, approve, reject. GitHub Issues adapter for engineering tasks. Persist external id and execution outcome.
7. EN / RU / TR with separate quality reporting. Compare the existing EN Triage ML model only if its code and weights are available. Review its limits before use; do not copy old metrics onto this dataset.
8. Auth, roles, two demo organizations with data isolation.
9. Dashboard: categories, queues, first-review SLA, stage duration, human decisions, errors, cost. Numbers computed in code. CSV export.
10. Tracing, optional Langfuse, quality evaluation, Docker Compose, CI, README, and a demo walkthrough.

## Technical baseline

Python, FastAPI, Pydantic, PostgreSQL + pgvector, React, LangGraph, one LLM provider behind an adapter, multilingual embeddings. Pin dependency versions after compatibility checks against official docs.

Local run first. Cloud deploy and paid resources need an explicit owner request. If API keys are absent, keep real adapters plus an explicit **demo** mode. Never present stub results as live model or live GitHub results.

## Evaluation corpus

Start with 10 runbooks and 30 EN development tickets. Held-out set: 150 tickets, 50 each EN/RU/TR. Keep translations and near-paraphrases of one scenario in the same split. Held-out answers must not enter the retrieval corpus.

Measure classification quality, Recall@5, next-step correctness, source-backed facts, latency, and cost. Compare LLM-only, RAG, and workflow+API. Publish only numbers from actual runs with sample size and configuration.

Required automated checks: happy path, missing fields, conflicting documents, API failure, org isolation, unapproved action, repeat execution, crash recovery.

## Authorization and execution

Backend owns permissions, queues, SLAs, and whether an action may run. The LLM and retrieved documents cannot grant extra rights.

Approval is bound to an exact action version (content hash). Edits require a new approval. Re-check authorization immediately before execute.

Handle retries and concurrent requests via an operations log. If an outbound write has an uncertain outcome, reconcile; if that is impossible, status is needs-review. Do not assume the external API supports idempotency keys.

Cap workflow steps, timeouts, retries, and cost. An unreachable API means **unknown** health, not a healthy service.

## Owner notes

Ramil: Python, ML, FastAPI, PostgreSQL. Substantial decisions in Russian and short; code, entity names, and README in English.
