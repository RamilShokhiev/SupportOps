# Synthetic SupportOps evaluation

Generated: 2026-09-19T11:28:08.108745+00:00. Split: **test**, dataset: `synthetic-v1`. Mode: **demo**.

This is an actual run over fictional RetailBridge examples. In demo mode every language model and vector is a deterministic local proxy. These results do **not** measure an LLM or production multilingual quality. The workflow variant calls the real read adapter with in-memory HTTP fixtures, followed by the analysis function. It does not run the LangGraph checkpoint/resume or approval/execution lifecycle; those require separate scenario tests.

| Variant | Language | n | Category macro-F1 | Recall@5 macro | Next step | p50 ms | p95 ms | Failures |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| demo-only proxy | all | 150 | 0.9149 | 0.0% | 46.0% | 0.06 | 0.13 | 108 |
| demo-only proxy | en | 50 | 0.9732 | 0.0% | 46.0% | 0.06 | 0.1455 | 36 |
| demo-only proxy | ru | 50 | 0.8246 | 0.0% | 46.0% | 0.06 | 0.1155 | 36 |
| demo-only proxy | tr | 50 | 0.9419 | 0.0% | 46.0% | 0.06 | 0.1155 | 36 |
| demo RAG proxy | all | 150 | 0.9149 | 99.1% | 76.0% | 0.985 | 3.5755 | 38 |
| demo RAG proxy | en | 50 | 0.9732 | 100.0% | 78.0% | 1.0 | 3.582 | 12 |
| demo RAG proxy | ru | 50 | 0.8246 | 97.2% | 74.0% | 0.975 | 3.531 | 14 |
| demo RAG proxy | tr | 50 | 0.9419 | 100.0% | 76.0% | 0.98 | 3.601 | 12 |
| demo workflow + API fixtures | all | 150 | 0.9149 | 99.1% | 90.0% | 1.195 | 4.9795 | 18 |
| demo workflow + API fixtures | en | 50 | 0.9732 | 100.0% | 92.0% | 1.165 | 4.867 | 5 |
| demo workflow + API fixtures | ru | 50 | 0.8246 | 97.2% | 88.0% | 1.165 | 4.818 | 8 |
| demo workflow + API fixtures | tr | 50 | 0.9419 | 100.0% | 90.0% | 1.26 | 5.352 | 5 |

## Metric definitions and denominators

Classification macro-F1 is the unweighted mean over the four fixed categories Incident, Request, Problem and Change. Recall@5 is averaged only over tickets with at least one labelled relevant document; JSON includes both macro and micro recall and all denominators. Tickets intentionally lacking an applicable source are excluded from recall and retained in next-step accuracy. Both conflicting upgrade documents are relevant. Next-step labels are identical across variants, exposing capability gaps in the no-source and no-diagnostics variants.

| Variant (all languages) | Relevant tickets | Relevant document labels | Valid source references | Mechanically attributed facts | Generation cost |
|---|---:|---:|---|---|---|
| demo-only proxy | 108 | 120 | 0/0 | 0/0 | $0.000000 known; 0 unknown |
| demo RAG proxy | 108 | 120 | 194/194 | 194/194 | $0.000000 known; 0 unknown |
| demo workflow + API fixtures | 108 | 120 | 194/194 | 410/410 | $0.000000 known; 0 unknown |

Source checks verify tenant visibility, product/version applicability and exact excerpt provenance. Fact checks compare document excerpts and successful API payloads with their recorded evidence. **These mechanical checks do not establish semantic correctness of draft claims.** Human-adjudicated factual support and real support-time savings were not measured. Zero demo cost means no model API calls; CPU, electricity and infrastructure are excluded. Live generation cost is unknown when rates are unset. Embedding indexing/query costs are not exposed by the adapter, so live total cost is explicitly unknown.

Latency measures synchronous analysis only, excluding startup, document indexing, diagnostic HTTP fixture calls, database/UI operations and human review. It is not end-to-end workflow latency. p50/p95 use linear interpolation. In live mode these values include model and retrieval requests. No reranker is enabled. Failures count tickets with at least one checked label/provenance mismatch, not runtime exceptions; one ticket can contribute to several error counts below.

## Reproducibility and limitations

```powershell
.\.venv\Scripts\python.exe .\scripts\evaluate.py --split test
```

Configuration: `{"diagnostics": "production read adapter with in-memory HTTP fixtures", "embedding_dimensions": 256, "embedding_model": "feature-hash-glossary", "embedding_provider": "demo", "live_total_cost": "zero external model API cost", "llm_model": "deterministic-demo", "llm_provider": "demo", "mode": "demo", "python": "3.13.15", "retrieval": "lexical plus cosine reciprocal-rank fusion; no reranker"}`.

Frozen test/input hashes are recorded in `data/dataset_manifest.json`; this run used `01da5457aff0b6198c226226a8b063fd803a5435e5459bc2dabaab6f268fd60b`. Full per-ticket JSON is generated locally by `scripts/evaluate.py` and is not stored in git.

The 150 test rows are 50 translation groups, not 150 independent real tickets. Development has 10 other scenario groups with three English paraphrases each. Exact text and group IDs are mechanically disjoint; semantic boundaries were manually authored and not independently certified. Shared vocabulary and hand-written translated templates make this a closed-domain regression exercise. No native-speaker review, random customer sampling, bootstrap intervals or human adjudication was performed. Test failures are retained; do not change this frozen set or tune on its outcomes. Future improvements require development examples and a fresh held-out set for quality claims.

## Failures from this run

classification: 27; next_step: 132; retrieval_missing_relevant: 110; retrieval_when_no_source_expected: 2

The no-source demo-only variant is a deterministic proxy, not a real LLM-only baseline. Real OpenAI LLM-only/RAG/workflow comparison requires an explicit `--live` run with keys and incurs API charges. The optional existing English Triage model is evaluated separately; see `docs/TRIAGE_REVIEW.md`. No old Triage metric is reused here.
