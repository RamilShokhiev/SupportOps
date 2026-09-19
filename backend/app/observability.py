"""Local timing and opt-in Langfuse tracing without ticket text or credentials."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Iterator

logger = logging.getLogger(__name__)
_SAFE_METADATA = {"variant", "language", "provider", "prompt_version", "elapsed_ms", "category", "next_step", "source_count", "warning_count", "model", "input_tokens", "output_tokens", "cost_usd"}
_EMBEDDING_LEDGER: ContextVar[list | None] = ContextVar("supportops_embedding_usage", default=None)


@contextmanager
def embedding_usage_scope() -> Iterator[list]:
    """Collect indexing or query charges without changing the vector interface."""
    ledger: list[dict] = []
    token = _EMBEDDING_LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _EMBEDDING_LEDGER.reset(token)


def record_embedding_usage(usage: dict | None, settings: Any) -> None:
    ledger = _EMBEDDING_LEDGER.get()
    if ledger is None:
        return
    count = usage.get("total_tokens") if usage else None
    rate = getattr(settings, "embedding_cost_per_million", None)
    ledger.append({"input_tokens": int(count) if count is not None else None,
                   "cost_usd": round(int(count) * float(rate) / 1_000_000, 8) if count is not None and rate is not None else None})


def summarize_embeddings(ledger: list[dict]) -> dict:
    counts = [row["input_tokens"] for row in ledger]
    costs = [row["cost_usd"] for row in ledger]
    return {"embedding_requests": len(ledger),
            "embedding_input_tokens": sum(counts) if all(count is not None for count in counts) else None,
            "embedding_cost_usd": round(sum(costs), 8) if all(cost is not None for cost in costs) else None}


def _safe_metadata(data: dict) -> dict:
    return {key: value for key, value in data.items() if key in _SAFE_METADATA and isinstance(value, (str, bool, float, int))}


@contextmanager
def trace_operation(settings: Any, name: str, metadata: dict | None = None) -> Iterator[dict]:
    """Timing always works; disabled/unavailable telemetry cannot break workflow.

Remote reporting requires an explicit switch, endpoint and both project keys.
Only allowlisted scalar operational metadata is sent, never ticket bodies,
document excerpts, diagnostics, organization/user identifiers, or secrets.
    """
    started = perf_counter()
    local = dict(metadata or {})
    observation = None
    context = None
    if (getattr(settings, "langfuse_enabled", False)
            and getattr(settings, "langfuse_public_key", None)
            and getattr(settings, "langfuse_secret_key", None)
            and getattr(settings, "langfuse_base_url", None)):
        try:
            from langfuse import Langfuse
            client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                base_url=settings.langfuse_base_url,
            )
            context = client.start_as_current_observation(name=name, as_type="span", metadata=_safe_metadata(local))
            observation = context.__enter__()
            local["trace_id"] = getattr(observation, "trace_id", None)
        except Exception as exc:  # Telemetry is deliberately optional.
            logger.warning("Optional Langfuse tracing unavailable: %s", type(exc).__name__)
            context = None
    try:
        yield local
    finally:
        local["elapsed_ms"] = round((perf_counter() - started) * 1000, 2)
        if observation is not None:
            try:
                observation.update(metadata=_safe_metadata(local))
            except Exception as exc:
                logger.warning("Optional trace update failed: %s", type(exc).__name__)
        if context is not None:
            try:
                context.__exit__(None, None, None)
            except Exception as exc:
                logger.warning("Optional trace close failed: %s", type(exc).__name__)


def usage_metadata(usage: dict, settings: Any, model: str) -> dict:
    inputs = int(usage.get("input_tokens", 0) or 0)
    outputs = int(usage.get("output_tokens", 0) or 0)
    input_rate = getattr(settings, "input_cost_per_million", None)
    output_rate = getattr(settings, "output_cost_per_million", None)
    if input_rate is None or output_rate is None:
        cost = None
        warnings = ["Generation cost is unknown: configure input_cost_per_million and output_cost_per_million."]
    else:
        cost = round((inputs * float(input_rate) + outputs * float(output_rate)) / 1_000_000, 8)
        warnings = []
    return {"model": model, "input_tokens": inputs, "output_tokens": outputs, "cost_usd": cost, "warnings": warnings}
