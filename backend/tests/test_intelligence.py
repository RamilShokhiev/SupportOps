import json
from types import SimpleNamespace

import httpx
import pytest

from app.intelligence import LLMError, analyze, extract
from app.observability import embedding_usage_scope, summarize_embeddings, trace_operation
from app.retrieval import EmbeddingError, applicable, embed_texts, retrieve


@pytest.fixture
def settings():
    return SimpleNamespace(llm_provider="demo", embedding_provider="demo", embedding_dimensions=256,
                           openai_api_key=None, openai_model="test-model", llm_timeout_seconds=0.1,
                           max_output_tokens=1200, langfuse_enabled=False)


def document(identifier="current", **overrides):
    return {"id": identifier, "document_key": identifier, "title": "E-214 checkout authorization",
            "content": "RetailBridge 3.8 E-214 indicates an authorization contract mismatch. Confirm diagnostics before escalation.",
            "product": "RetailBridge", "version": 1, "min_version": "3.8", "max_version": "3.8",
            "status": "ready", **overrides}


def diagnostics(status="ok", service_status="degraded"):
    return [{"tool": "get_service_status", "status": status, "checked_at": "2026-09-13T09:00:00Z",
             "data": {"service": "checkout", "status": service_status, "version": "3.8", "synthetic": True} if status == "ok" else None,
             "error": None if status == "ok" else "upstream_timeout"},
            {"tool": "get_recent_changes", "status": status, "checked_at": "2026-09-13T09:00:00Z",
             "data": {"changes": [{"version": "3.8", "description": "authorization contract changed"}]} if status == "ok" else None,
             "error": None if status == "ok" else "upstream_timeout"}]


@pytest.mark.parametrize("language,text", [
    ("EN", "After upgrading RetailBridge to 3.8, three stores cannot complete payment, E-214."),
    ("RU", "После обновления RetailBridge до 3.8 кассы трёх магазинов не завершают оплату. Код E-214."),
    ("TR", "RetailBridge 3.8 güncellemesinden sonra üç mağazada ödeme tamamlanmıyor. E-214."),
])
def test_known_case_has_grounded_fields_and_actual_evidence(settings, language, text):
    doc = document()
    result = analyze(text, language, [doc], diagnostics(), settings)
    assert result["fields"]["affected_stores"] == 3
    assert result["fields"]["product"] == "RetailBridge"
    assert result["fields"]["version"] == "3.8"
    assert result["category"] == "Incident"
    assert result["next_step"] == "escalate"
    assert result["cost_usd"] == 0 and result["input_tokens"] == 0
    assert result["elapsed_ms"] >= 0
    assert result["facts"][0]["excerpt"] in doc["content"]
    assert result["facts"][0]["source_id"] == doc["id"]
    assert result["facts"][1]["data"] == diagnostics()[0]["data"]
    assert "unconfirmed" in result["hypothesis"]


def test_missing_product_and_version_are_not_filled_from_documents(settings):
    result = analyze("Payment failed at three stores. E-214", "EN", [document()], [], settings)
    assert result["fields"]["product"] is None
    assert result["fields"]["version"] is None
    assert result["missing_fields"] == ["product", "version"]
    assert result["sources"] == []
    assert result["next_step"] == "clarify"


def test_version_before_sentence_punctuation_is_preserved(settings):
    assert extract("Payment E-214 in RetailBridge 3.8.", "EN", settings)["version"] == "3.8"
    assert extract("Receipt P-102 in RetailBridge 3.8.1.", "EN", settings)["version"] == "3.8.1"


def test_version_comparison_is_numeric_and_latest_revision_wins(settings):
    fields = {"product": "RetailBridge", "version": "3.10", "error_code": "E-214"}
    stale = document("stale", min_version="3.8", max_version="3.9")
    revision1 = document("revision1", document_key="runbook", min_version="3.10", max_version="3.12")
    revision2 = document("revision2", document_key="runbook", version=2, min_version="3.10", max_version="3.12")
    results = retrieve("RetailBridge 3.10 E-214", [stale, revision1, revision2], fields, settings)
    assert [source["id"] for source in results] == ["revision2"]
    assert not applicable(document(max_version="invalid"), fields)


def test_exact_error_code_never_matches_a_prefix(settings):
    fields = {"product": "RetailBridge", "version": "3.8", "error_code": "E-214"}
    other = document("wrong", title="E-2140 code", content="E-2140 checkout error.")
    assert retrieve("RetailBridge 3.8 E-214", [other], fields, settings) == []


def test_unknown_code_and_unrelated_query_have_no_fake_top_hit(settings):
    assert analyze("RetailBridge 3.8 E-999 unexpected failure", "EN", [document()], diagnostics(), settings)["next_step"] == "clarify"
    fields = {"product": "RetailBridge", "version": "3.8", "error_code": None}
    assert retrieve("RetailBridge 3.8 wallpaper aquarium", [document()], fields, settings) == []


def test_pending_and_other_product_documents_are_excluded(settings):
    fields = {"product": "RetailBridge", "version": "3.8", "error_code": "E-214"}
    docs = [document("pending", status="pending"), document("different", product="OtherProduct")]
    assert retrieve("RetailBridge 3.8 E-214", docs, fields, settings) == []


def test_marked_conflict_requires_review_even_if_sibling_outside_top_k(settings):
    result = analyze("RetailBridge 3.8 E-214", "EN", [document(conflict_group="policy-conflict")], diagnostics(), settings)
    assert result["next_step"] == "clarify"
    assert any("conflict" in warning for warning in result["warnings"])


@pytest.mark.parametrize("status", ["timeout", "error"])
def test_failed_diagnostics_never_create_healthy_facts(settings, status):
    result = analyze("RetailBridge 3.8 E-214", "EN", [document()], diagnostics(status), settings)
    assert result["next_step"] == "clarify"
    assert not any(fact["kind"] == "api" for fact in result["facts"])
    assert "operational" not in result["draft"]


def test_comparison_variants_remove_unavailable_evidence(settings):
    llm = analyze("RetailBridge 3.8 E-214", "EN", [document()], diagnostics(), settings, variant="llm_only")
    rag = analyze("RetailBridge 3.8 E-214", "EN", [document()], diagnostics(), settings, variant="rag")
    assert llm["sources"] == [] and llm["diagnostics"] == [] and llm["facts"] == []
    assert rag["sources"] and rag["diagnostics"] == []
    assert not any(fact["kind"] == "api" for fact in rag["facts"])


@pytest.mark.parametrize("text,expected", [
    ("Please grant a store analyst role in RetailBridge 3.8", "Request"),
    ("Plan a RetailBridge 3.9 upgrade next month", "Change"),
    ("RetailBridge 3.8 has intermittent E-409 failures, investigate root cause", "Problem"),
    ("RetailBridge 3.8 failed after upgrade: E-214", "Incident"),
])
def test_category_and_hypothesis_are_distinct(settings, text, expected):
    result = extract(text, "EN", settings)
    assert result["category"] == expected
    assert result["hypothesis"] is None


def test_embeddings_are_reproducible_and_demo_never_calls_provider(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("demo mode attempted a network call")
    monkeypatch.setattr(httpx, "post", forbidden)
    first = embed_texts(["Payment error E-214", "Ошибка оплаты E-214"], settings)
    assert first == embed_texts(["Payment error E-214", "Ошибка оплаты E-214"], settings)
    assert len(first[0]) == 256
    assert sum(x * x for x in first[0]) == pytest.approx(1.0)
    analyze("RetailBridge 3.8 E-214", "EN", [document()], diagnostics(), settings)


def test_live_embeddings_validate_provider_shape_and_order(settings, monkeypatch):
    settings.embedding_provider = "openai"
    settings.openai_api_key = "test-secret-do-not-log"
    settings.embedding_dimensions = 8
    def provider(url, **kwargs):
        assert url == "https://api.openai.com/v1/embeddings"
        assert kwargs["json"]["dimensions"] == 8
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [2.0] * 8}, {"index": 0, "embedding": [1.0] * 8}]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", provider)
    assert embed_texts(["first", "second"], settings) == [[1.0] * 8, [2.0] * 8]


def test_live_missing_key_does_not_silently_claim_demo_success(settings):
    settings.llm_provider = "openai"
    with pytest.raises(LLMError, match="requires OPENAI_API_KEY"):
        extract("RetailBridge 3.8 E-214", "EN", settings)
    result = analyze("RetailBridge 3.8 E-214", "EN", [document()], diagnostics(), settings)
    assert result["next_step"] == "clarify"
    assert result["cost_usd"] is None
    assert any("local safety template" in warning for warning in result["warnings"])


def test_live_response_schema_usage_and_literal_identifier_grounding(settings, monkeypatch):
    settings.llm_provider = "openai"
    settings.openai_api_key = "test-secret-do-not-log"
    settings.input_cost_per_million = 1.0
    settings.output_cost_per_million = 2.0
    payload = {"product": "InventedProduct", "version": "3.8", "error_code": "E-214", "affected_stores": None,
               "symptoms": ["reported E-214"], "category": "Incident", "hypothesis": None}
    def provider(url, **kwargs):
        assert url == "https://api.openai.com/v1/responses"
        assert kwargs["json"]["store"] is False
        assert kwargs["json"]["text"]["format"]["strict"] is True
        assert kwargs["json"]["text"]["format"]["schema"]["additionalProperties"] is False
        return httpx.Response(200, json={"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps(payload)}]}],
                                         "usage": {"input_tokens": 100, "output_tokens": 30}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", provider)
    result = extract("RetailBridge 3.8 E-214", "EN", settings)
    assert result["product"] is None
    assert result["cost_usd"] == pytest.approx(0.00016)
    assert result["input_tokens"] == 100


def test_partial_live_response_retains_billed_usage(settings, monkeypatch):
    settings.llm_provider = "openai"
    settings.openai_api_key = "test-secret-do-not-log"
    settings.input_cost_per_million = 1.0
    settings.output_cost_per_million = 2.0
    def provider(url, **kwargs):
        return httpx.Response(200, json={"status": "incomplete", "output": [], "usage": {"input_tokens": 100, "output_tokens": 200}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", provider)
    with pytest.raises(LLMError) as error:
        extract("RetailBridge 3.8 E-214", "EN", settings)
    assert error.value.usage["output_tokens"] == 200


def test_provider_failure_does_not_expose_response_body_or_secret(settings, monkeypatch):
    settings.embedding_provider = "openai"
    settings.openai_api_key = "test-secret-do-not-log"
    def provider(url, **kwargs):
        return httpx.Response(500, text="secret upstream content test-secret-do-not-log", request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", provider)
    with pytest.raises(EmbeddingError) as error:
        embed_texts(["payment"], settings)
    assert "test-secret" not in str(error.value)


def test_embedding_accounting_includes_failed_requests_as_unknown(settings, monkeypatch):
    settings.embedding_provider = "openai"
    settings.openai_api_key = "test-secret-do-not-log"
    settings.embedding_cost_per_million = 0.1
    def provider(url, **kwargs):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 256}], "usage": {"total_tokens": 100}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", provider)
    with embedding_usage_scope() as ledger:
        embed_texts(["query"], settings)
    assert summarize_embeddings(ledger) == {"embedding_requests": 1, "embedding_input_tokens": 100, "embedding_cost_usd": 0.00001}
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("secret upstream message")
    monkeypatch.setattr(httpx, "post", timeout)
    with embedding_usage_scope() as failed_ledger:
        with pytest.raises(EmbeddingError):
            embed_texts(["query"], settings)
    assert summarize_embeddings(failed_ledger)["embedding_cost_usd"] is None


def test_telemetry_is_opt_in_and_does_not_swallow_workflow_errors(settings):
    with pytest.raises(ValueError, match="workflow failed"):
        with trace_operation(settings, "test", {"ticket_text": "private"}) as trace:
            raise ValueError("workflow failed")
    assert trace["elapsed_ms"] >= 0
