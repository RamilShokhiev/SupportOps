"""Reproducible, honestly labelled synthetic evaluation; no paid requests by default."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
CLASSES = ("Incident", "Request", "Problem", "Change")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 4)


def classification_metrics(records):
    per_class = {}
    for category in CLASSES:
        tp = sum(r["expected"]["category"] == category and r["prediction"]["category"] == category for r in records)
        fp = sum(r["expected"]["category"] != category and r["prediction"]["category"] == category for r in records)
        fn = sum(r["expected"]["category"] == category and r["prediction"]["category"] != category for r in records)
        per_class[category] = {"tp": tp, "fp": fp, "fn": fn, "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0}
    return {"macro_f1": statistics.mean(c["f1"] for c in per_class.values()), "per_class": per_class}


def check_split(split):
    manifest = load_json(ROOT / "data" / "dataset_manifest.json")
    for filename, expected_hash in manifest["sha256"].items():
        actual = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
        if actual != expected_hash:
            raise ValueError(f"Frozen dataset hash differs: {filename}")
    dev, test = [load_json(ROOT / "data" / name) for name in ("dev_tickets.json", "test_tickets.json")]
    if {x["group_id"] for x in dev} & {x["group_id"] for x in test}:
        raise ValueError("Scenario groups leak across splits")
    if len({x["text"].casefold().strip() for x in dev + test}) != len(dev + test):
        raise ValueError("Exact duplicate text detected across or within splits")
    return (dev if split == "dev" else test), manifest


def make_diagnostics(text, settings, mode):
    """Exercise the real read adapter with an in-memory HTTP transport.

    API routing, allowlist, retry and error parsing are production code. Responses
    are deterministic fixtures; this does not test real network timing/availability.
    Expected labels are not read or passed to the adapter.
    """
    import httpx
    from unittest.mock import patch
    from app.integrations import diagnostics

    checked = "2026-09-13T12:00:00+00:00"
    original_client = httpx.Client

    def handler(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("Synthetic evaluation timeout", request=request)
        if mode == "error":
            return httpx.Response(503, json={"detail": "Synthetic evaluation API failure"})
        version = request.url.params.get("version", "")
        if request.url.path == "/service-status":
            service = request.url.params["service"]
            payload = {"service": service, "status": "degraded" if service == "checkout" and version == "3.8" else "operational", "version": version, "checked_at": checked, "synthetic": True}
        elif request.url.path == "/recent-changes":
            payload = {"product": "RetailBridge", "changes": [{"id": "CHG-380", "product": "RetailBridge", "version": "3.8", "description": "Checkout authorization contract updated; investigate E-214 reports.", "occurred_at": checked}] if version == "3.8" else [], "synthetic": True}
        else:
            return httpx.Response(404)
        return httpx.Response(200, json=payload)

    def client(**kwargs):
        return original_client(**kwargs, transport=httpx.MockTransport(handler))

    with patch("app.integrations.httpx.Client", client):
        return diagnostics(text, settings, mode)


def documents(settings):
    from app.retrieval import embed_texts
    directory = ROOT / "data" / "knowledge"
    docs = [{**row, "id": row["document_key"], "status": "ready", "content": (directory / row["filename"]).read_text(encoding="utf-8")} for row in load_json(directory / "manifest.json")]
    # Indexing occurs once and is outside request latency; indexing API token cost
    # is not exposed by embed_texts and therefore cannot be claimed as measured.
    embedded = embed_texts([row["title"] + "\n" + row["content"] for row in docs], settings)
    for row, vector in zip(docs, embedded):
        row["embedding"] = vector
    return docs


def validate_evidence(result, scoped_docs, observed, allowed):
    """Mechanical checks, deliberately not a semantic entailment judge."""
    from app.retrieval import applicable
    by_key = {d["document_key"]: d for d in scoped_docs}
    source_valid, fact_valid, messages = [], [], []
    for source in result.get("sources", []):
        doc = by_key.get(source.get("document_key"))
        okay = bool(doc and applicable(doc, result.get("fields", {})) and source.get("excerpt") and source["excerpt"] in doc["content"])
        source_valid.append(okay)
        if not okay:
            messages.append("invalid_source_provenance")
    sources_by_id = {str(s["id"]): s for s in result.get("sources", [])}
    successful = {d["tool"]: d for d in observed if d.get("status") == "ok"}
    for fact in result.get("facts", []):
        if fact.get("kind") == "document":
            source = sources_by_id.get(str(fact.get("source_id")))
            okay = bool(source and fact.get("excerpt") == source.get("excerpt") and fact.get("text") == source.get("excerpt"))
        elif fact.get("kind") == "api":
            evidence = successful.get(fact.get("tool"))
            okay = bool(evidence and fact.get("tool") in allowed["allowed_tools"] and fact.get("data") == evidence.get("data") and fact.get("checked_at") == evidence.get("checked_at"))
        else:
            okay = False
        fact_valid.append(okay)
        if not okay:
            messages.append("fact_evidence_mismatch")
    return {"source_valid_n": sum(source_valid), "source_n": len(source_valid), "fact_valid_n": sum(fact_valid), "fact_n": len(fact_valid), "errors": sorted(set(messages))}


def summarize(records, classification_only=False):
    result = {"n": len(records), "classification": classification_metrics(records), "latency_ms": {"p50": percentile([r["prediction"]["elapsed_ms"] for r in records], .50), "p95": percentile([r["prediction"]["elapsed_ms"] for r in records], .95)}}
    if classification_only:
        return result
    relevant = [r for r in records if r["expected"]["source_keys"]]
    relevant_count = sum(len(r["expected"]["source_keys"]) for r in relevant)
    retrieved_relevant = sum(len(set(r["expected"]["source_keys"]) & set(r["prediction"]["source_keys"][:5])) for r in relevant)
    source_count = sum(r["evidence"]["source_n"] for r in records)
    fact_count = sum(r["evidence"]["fact_n"] for r in records)
    costs = [r["prediction"]["cost_usd"] for r in records if r["prediction"]["cost_usd"] is not None]
    result.update({
        "recall_at_5": {"macro": statistics.mean(len(set(r["expected"]["source_keys"]) & set(r["prediction"]["source_keys"][:5])) / len(set(r["expected"]["source_keys"])) for r in relevant) if relevant else None, "micro": retrieved_relevant / relevant_count if relevant_count else None, "eligible_tickets": len(relevant), "excluded_no_relevant_source": len(records) - len(relevant), "relevant_document_labels": relevant_count, "relevant_retrieved": retrieved_relevant},
        "next_step_accuracy": sum(r["expected"]["next_step"] == r["prediction"]["next_step"] for r in records) / len(records) if records else None,
        "mechanical_source_validity": {"valid": sum(r["evidence"]["source_valid_n"] for r in records), "total": source_count, "rate": sum(r["evidence"]["source_valid_n"] for r in records) / source_count if source_count else None},
        "mechanical_fact_attribution": {"valid": sum(r["evidence"]["fact_valid_n"] for r in records), "total": fact_count, "rate": sum(r["evidence"]["fact_valid_n"] for r in records) / fact_count if fact_count else None},
        "semantic_factual_support": "not measured: no independent human or entailment adjudication",
        "generation_cost_usd": {"known_ticket_count": len(costs), "unknown_ticket_count": len(records) - len(costs), "known_sum": sum(costs), "p50_known": percentile(costs, .5), "p95_known": percentile(costs, .95)},
        "failed_ticket_count": sum(bool(r["errors"]) for r in records),
    })
    return result


def percentage(value):
    return "n/a" if value is None else f"{100 * value:.1f}%"


def markdown_report(report):
    mode = report["configuration"]["mode"]
    rows = ["# Synthetic SupportOps evaluation", "", f"Generated: {report['generated_at']}. Split: **{report['split']}**, dataset: `{report['dataset_version']}`. Mode: **{mode}**.", "", "This is an actual run over fictional RetailBridge examples. In demo mode every language model and vector is a deterministic local proxy. These results do **not** measure an LLM or production multilingual quality. The workflow variant calls the real read adapter with in-memory HTTP fixtures, followed by the analysis function. It does not run the LangGraph checkpoint/resume or approval/execution lifecycle; those require separate scenario tests.", "", "| Variant | Language | n | Category macro-F1 | Recall@5 macro | Next step | p50 ms | p95 ms | Failures |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for variant, language_metrics in report["metrics"].items():
        for language, metrics in language_metrics.items():
            rows.append(f"| {report['variant_labels'][variant]} | {language} | {metrics['n']} | {metrics['classification']['macro_f1']:.4f} | {percentage(metrics['recall_at_5']['macro'])} | {percentage(metrics['next_step_accuracy'])} | {metrics['latency_ms']['p50']} | {metrics['latency_ms']['p95']} | {metrics['failed_ticket_count']} |")
    rows += ["", "## Metric definitions and denominators", "", "Classification macro-F1 is the unweighted mean over the four fixed categories Incident, Request, Problem and Change. Recall@5 is averaged only over tickets with at least one labelled relevant document; JSON includes both macro and micro recall and all denominators. Tickets intentionally lacking an applicable source are excluded from recall and retained in next-step accuracy. Both conflicting upgrade documents are relevant. Next-step labels are identical across variants, exposing capability gaps in the no-source and no-diagnostics variants.", "", "| Variant (all languages) | Relevant tickets | Relevant document labels | Valid source references | Mechanically attributed facts | Generation cost |", "|---|---:|---:|---|---|---|"]
    for variant, grouped in report["metrics"].items():
        m = grouped["all"]
        source, fact, cost = m["mechanical_source_validity"], m["mechanical_fact_attribution"], m["generation_cost_usd"]
        cost_text = f"${cost['known_sum']:.6f} known; {cost['unknown_ticket_count']} unknown"
        rows.append(f"| {report['variant_labels'][variant]} | {m['recall_at_5']['eligible_tickets']} | {m['recall_at_5']['relevant_document_labels']} | {source['valid']}/{source['total']} | {fact['valid']}/{fact['total']} | {cost_text} |")
    rows += ["", "Source checks verify tenant visibility, product/version applicability and exact excerpt provenance. Fact checks compare document excerpts and successful API payloads with their recorded evidence. **These mechanical checks do not establish semantic correctness of draft claims.** Human-adjudicated factual support and real support-time savings were not measured. Zero demo cost means no model API calls; CPU, electricity and infrastructure are excluded. Live generation cost is unknown when rates are unset. Embedding indexing/query costs are not exposed by the adapter, so live total cost is explicitly unknown.", "", "Latency measures synchronous analysis only, excluding startup, document indexing, diagnostic HTTP fixture calls, database/UI operations and human review. It is not end-to-end workflow latency. p50/p95 use linear interpolation. In live mode these values include model and retrieval requests. No reranker is enabled. Failures count tickets with at least one checked label/provenance mismatch, not runtime exceptions; one ticket can contribute to several error counts below.", "", "## Reproducibility and limitations", "", "```powershell", r".\.venv\Scripts\python.exe .\scripts\evaluate.py --split test", "```", "", f"Configuration: `{json.dumps(report['configuration'], sort_keys=True)}`.", "", f"Frozen test/input hashes are recorded in `data/dataset_manifest.json`; this run used `{report['dataset_sha256']}`. Complete predictions and failures are in `data/evaluation/{report['split']}_{mode}.json` and `data/evaluation/{report['split']}_{mode}_failures.json`.", "", "The 150 test rows are 50 translation groups, not 150 independent real tickets. Development has 10 other scenario groups with three English paraphrases each. Exact text and group IDs are mechanically disjoint; semantic boundaries were manually authored and not independently certified. Shared vocabulary and hand-written translated templates make this a closed-domain regression exercise. No native-speaker review, random customer sampling, bootstrap intervals or human adjudication was performed. Test failures are retained; do not change this frozen set or tune on its outcomes. Future improvements require development examples and a fresh held-out set for quality claims.", "", "## Failures from this run", ""]
    counts = {}
    for record in report["records"]:
        for error in record["errors"]:
            counts[error] = counts.get(error, 0) + 1
    rows.append("; ".join(f"{key}: {value}" for key, value in sorted(counts.items())) or "No checked errors in this finite synthetic run.")
    rows += ["", "The no-source demo-only variant is a deterministic proxy, not a real LLM-only baseline. Real OpenAI LLM-only/RAG/workflow comparison requires an explicit `--live` run with keys and incurs API charges. The optional existing English Triage model is evaluated separately; see `docs/TRIAGE_REVIEW.md`. No old Triage metric is reused here.", ""]
    return "\n".join(rows)


def run_analysis(args):
    sys.path.insert(0, str(ROOT / "backend"))
    from app.config import Settings
    from app.intelligence import analyze

    tickets, dataset = check_split(args.split)
    settings = Settings(mode="live" if args.live else "demo", llm_provider="openai" if args.live else "demo", embedding_provider="openai" if args.live else "demo", issue_provider="demo", langfuse_enabled=False)
    if args.live and not settings.openai_api_key:
        raise SystemExit("--live requires OPENAI_API_KEY; no paid API call was made.")
    docs = documents(settings)
    variants = args.variants.split(",")
    if any(v not in ("llm_only", "rag", "workflow") for v in variants):
        raise SystemExit("--variants must contain llm_only, rag and/or workflow")
    records = []
    for variant in variants:
        for ticket in tickets:
            # Authorization is session-owned and never inferred from incoming text.
            scoped = [d for d in docs if d["organization"] in ("both", ticket["organization"])]
            observed = make_diagnostics(ticket["text"], settings, ticket["diagnostic_mode"]) if variant == "workflow" else []
            result = analyze(ticket["text"], ticket["language"], scoped, observed, settings, variant=variant)
            evidence = validate_evidence(result, scoped, observed, ticket["expected"])
            keys = [source["document_key"] for source in result["sources"]]
            errors = list(evidence["errors"])
            if result["category"] != ticket["expected"]["category"]:
                errors.append("classification")
            if result["next_step"] != ticket["expected"]["next_step"]:
                errors.append("next_step")
            if set(ticket["expected"]["source_keys"]) - set(keys[:5]):
                errors.append("retrieval_missing_relevant")
            if not ticket["expected"]["source_keys"] and keys:
                errors.append("retrieval_when_no_source_expected")
            record = {"ticket_id": ticket["id"], "group_id": ticket["group_id"], "language": ticket["language"], "organization": ticket["organization"], "text": ticket["text"], "diagnostic_mode": ticket["diagnostic_mode"], "variant": variant, "expected": ticket["expected"], "prediction": {**result, "source_keys": keys}, "evidence": evidence, "errors": sorted(set(errors))}
            records.append(record)
        print(f"Evaluated {variant}: {len(tickets)} {args.split} examples", flush=True)
    labels = {"llm_only": "LLM-only" if args.live else "demo-only proxy", "rag": "RAG" if args.live else "demo RAG proxy", "workflow": "workflow + API fixtures" if args.live else "demo workflow + API fixtures"}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "split": args.split, "dataset_version": dataset["dataset_version"], "dataset_sha256": dataset["sha256"][f"data/{args.split}_tickets.json"], "configuration": {"mode": "live" if args.live else "demo", "python": platform.python_version(), "llm_provider": settings.llm_provider, "llm_model": settings.openai_model if args.live else "deterministic-demo", "embedding_provider": settings.embedding_provider, "embedding_model": settings.embedding_model if args.live else "feature-hash-glossary", "embedding_dimensions": settings.embedding_dimensions, "diagnostics": "production read adapter with in-memory HTTP fixtures", "retrieval": "lexical plus cosine reciprocal-rank fusion; no reranker", "live_total_cost": "unknown (embedding usage unavailable)" if args.live else "zero external model API cost"}, "variant_labels": labels, "metrics": {variant: {language: summarize([r for r in records if r["variant"] == variant and (language == "all" or r["language"] == language)]) for language in ["all"] + sorted({t["language"] for t in tickets})} for variant in variants}, "records": records}
    out = ROOT / "data" / "evaluation"
    mode = report["configuration"]["mode"]
    write_json(out / f"{args.split}_{mode}.json", report)
    write_json(out / f"{args.split}_{mode}_failures.json", [r for r in records if r["errors"]])
    markdown = markdown_report(report)
    (out / f"{args.split}_{mode}.md").write_text(markdown, encoding="utf-8")
    # Keep the canonical report pointing at the actual held-out run, never dev.
    if args.split == "test":
        (ROOT / "docs" / "EVALUATION.md").write_text(markdown, encoding="utf-8")
    return report


def run_triage(args):
    """Load only the explicit user-owned Keras artifact; never import old app code."""
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    import numpy as np
    import tensorflow as tf
    import keras

    if not args.triage_root:
        raise SystemExit("--triage-only requires --triage-root pointing to the existing Triage project")
    root = Path(args.triage_root).resolve()
    artifact = root / "incident_triage_artifacts" / "best_triage_model.keras"
    tickets, dataset = check_split(args.split)
    tickets = [ticket for ticket in tickets if ticket["language"] == "en"]

    def custom_standardize(input_text):
        lowercase = tf.strings.lower(input_text)
        lowercase = tf.strings.regex_replace(lowercase, r"http\S+|www\.\S+", " ")
        lowercase = tf.strings.regex_replace(lowercase, r"<br />", " ")
        lowercase = tf.strings.regex_replace(lowercase, r"[^a-z0-9\s]", " ")
        lowercase = tf.strings.regex_replace(lowercase, r"\s+", " ")
        return tf.strings.strip(lowercase)

    started = perf_counter()
    model = tf.keras.models.load_model(artifact, custom_objects={"custom_standardize": custom_standardize}, compile=False)
    load_ms = (perf_counter() - started) * 1000
    with (artifact.parent / "root_cause_classes.csv").open(encoding="utf-8", newline="") as handle:
        classes = [row["class"] for row in csv.DictReader(handle)]
    if set(classes) != set(CLASSES):
        raise ValueError("Triage class vocabulary differs from SupportOps categories")
    records = []
    # Direct eager inference avoids Dataset/thread-pool setup and keeps one-ticket latency visible.
    for ticket in tickets:
        started = perf_counter()
        raw = model(tf.constant([[ticket["text"]]]), training=False)
        latency = (perf_counter() - started) * 1000
        if isinstance(raw, dict):
            scores = np.asarray(raw["root_cause_family"])[0]
        else:
            index = list(model.output_names).index("root_cause_family")
            scores = np.asarray(raw[index])[0]
        if len(scores) != len(classes):
            raise ValueError("Triage output dimensions do not match class file")
        category = classes[int(np.argmax(scores))]
        records.append({"ticket_id": ticket["id"], "group_id": ticket["group_id"], "language": "en", "text": ticket["text"], "expected": ticket["expected"], "prediction": {"category": category, "elapsed_ms": round(latency, 4), "scores": {label: float(score) for label, score in zip(classes, scores)}}, "errors": [] if category == ticket["expected"]["category"] else ["classification"]})
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "split": args.split, "dataset_version": dataset["dataset_version"], "dataset_sha256": dataset["sha256"][f"data/{args.split}_tickets.json"], "model": model.name, "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "python": platform.python_version(), "tensorflow": tf.__version__, "keras": keras.__version__, "load_ms": load_ms, "input_format": "single complete ticket text; subject is not supplied in this dataset", "language": "en", "metrics": summarize(records, classification_only=True), "limitations": ["Existing model used without retraining or threshold tuning", "Category output historically called root_cause_family but mapped to Incident/Request/Problem/Change", "Urgency head not evaluated: no independently labelled urgency targets", "Not comparable to retrieval, next-step or fact-support tasks", "ASCII-only preprocessing makes this unsuitable for RU/TR", "One-ticket CPU inference; first call is included in latency"], "records": records}
    path = ROOT / "data" / "evaluation" / f"triage_{args.split}.json"
    write_json(path, report)
    write_json(path.with_name(f"triage_{args.split}_failures.json"), [r for r in records if r["errors"]])
    print(json.dumps({"n": len(records), "macro_f1": report["metrics"]["classification"]["macro_f1"], "latency_ms": report["metrics"]["latency_ms"], "output": str(path)}))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--variants", default="llm_only,rag,workflow")
    parser.add_argument("--live", action="store_true", help="Explicitly enable PAID OpenAI generation and embedding API requests")
    parser.add_argument("--triage-only", action="store_true", help="Evaluate only the existing EN Keras category model")
    parser.add_argument("--triage-root", help="Existing read-only Triage project containing incident_triage_artifacts")
    args = parser.parse_args()
    if args.live and args.triage_only:
        parser.error("--live and --triage-only cannot be combined")
    run_triage(args) if args.triage_only else run_analysis(args)


if __name__ == "__main__":
    main()
