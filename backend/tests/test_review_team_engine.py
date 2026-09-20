"""Exercise the bounded graph through mocked Responses API HTTP responses."""

import json
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app import intelligence, review_team
from app.observability import record_embedding_usage


TEXT = "RetailBridge 3.8 E-214 prevents payment at three stores."
GOOD_DRAFT = (
    "The runbook describes an authorization contract mismatch [current]. "
    "Prepare an engineering issue for staff review and exact-content approval. "
    "The incident remains unresolved."
)
BAD_CLAIM = "The incident is resolved."
EXTRACTION = {
    "product": "RetailBridge", "version": "3.8", "error_code": "E-214",
    "affected_stores": 3, "symptoms": ["Payment cannot complete"],
    "category": "Incident", "hypothesis": None,
}
DOCUMENT = {
    "id": "current", "document_key": "checkout", "title": "E-214 checkout authorization",
    "content": "RetailBridge 3.8 E-214 indicates an authorization contract mismatch. Confirm diagnostics before escalation.",
    "product": "RetailBridge", "version": 1, "min_version": "3.8", "max_version": "3.8",
    "status": "ready", "organization_id": "northstar",
}
DIAGNOSTICS = [
    {"tool": "get_service_status", "status": "ok", "data": {"status": "degraded", "synthetic": True}},
    {"tool": "get_recent_changes", "status": "ok", "data": {"changes": [], "synthetic": True}},
]


def verdict(value="approved", findings=None):
    return {"verdict": value, "summary": "Independent evidence review.", "findings": findings or []}


def finding(claim=BAD_CLAIM, source_ids=None, blocking=True):
    return {"code": "unsupported_claim", "message": "Resolution is not established by this evidence.",
            "blocking": blocking, "claim": claim, "source_ids": ["current"] if source_ids is None else source_ids}


@pytest.fixture
def settings():
    return SimpleNamespace(
        llm_provider="openai", openai_api_key="mock-key-never-sent", openai_model="mock-model",
        llm_timeout_seconds=0.1, max_output_tokens=1200, input_cost_per_million=1.0,
        output_cost_per_million=2.0, embedding_provider="demo", embedding_dimensions=256,
        embedding_cost_per_million=0.1, langfuse_enabled=False,
    )


@pytest.fixture
def provider(monkeypatch):
    """Keep extraction, schema validation, usage accounting and graph routing real."""
    state = SimpleNamespace(calls=[], initial=GOOD_DRAFT, revised=GOOD_DRAFT,
                            reviews=[verdict()], review_failure=None, revision_failure=False)

    def post(url, **kwargs):
        assert url == "https://api.openai.com/v1/responses"
        request = kwargs["json"]
        name = request["text"]["format"]["name"]
        payload = json.loads(request["input"][0]["content"])
        state.calls.append((name, payload))
        assert request["store"] is False and "tools" not in request
        assert request["text"]["format"]["strict"] is True
        assert request["text"]["format"]["schema"]["additionalProperties"] is False
        usage = {"input_tokens": 100, "output_tokens": 30}
        response_status = "completed"
        if name == "supportops_extraction":
            result = EXTRACTION
        elif name == "supportops_draft":
            result = {"draft": state.initial}
        elif name == "supportops_draft_revision":
            if state.revision_failure:
                raise httpx.ReadTimeout("private provider failure")
            result = {"draft": state.revised}
        elif name == "supportops_safety_review":
            if state.review_failure == "timeout":
                raise httpx.ReadTimeout("private provider failure")
            review_index = sum(call[0] == name for call in state.calls) - 1
            assert review_index < len(state.reviews), "Graph exceeded the planned number of reviews"
            result = state.reviews[review_index]
            if state.review_failure == "incomplete":
                response_status = "incomplete"
            if state.review_failure == "invalid_schema":
                result = {"verdict": "approve_and_execute", "findings": []}
        else:
            raise AssertionError(f"Unexpected generation: {name}")
        content = [{"type": "output_text", "text": json.dumps(result)}]
        if name == "supportops_safety_review" and state.review_failure == "refusal":
            content = [{"type": "refusal", "refusal": "Cannot review"}]
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "status": response_status, "output": [{"content": content}], "usage": usage,
        })

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(review_team, "diagnostics", lambda *args, **kwargs: DIAGNOSTICS)
    return state


def run_graph(settings, documents=None):
    graph = review_team.build_review_graph(settings, InMemorySaver())
    config = {"configurable": {"thread_id": "engine-test"}, "recursion_limit": 20}
    result = graph.invoke({
        "ticket_id": "ticket-test", "organization_id": "northstar", "text": TEXT,
        "language": "EN", "diagnostic_mode": "normal",
        "documents": [DOCUMENT] if documents is None else documents,
    }, config)
    assert result["__interrupt__"][0].value["ticket_id"] == "ticket-test"
    assert graph.get_state(config).next == ("human_review",)
    assert "decision" not in result
    return result["analysis"], graph, config


def test_approved_review_has_six_roles_and_waits_for_a_human(settings, provider):
    analysis, graph, config = run_graph(settings)
    team = analysis["review_team"]
    assert analysis["next_step"] == "escalate"
    assert analysis["draft"] == GOOD_DRAFT
    assert team["status"] == "passed" and team["human_review_required"] is True
    assert [row["role"] for row in team["roles"]] == [
        "triage", "knowledge", "diagnostics", "response", "safety", "coordinator",
    ]
    assert team["rounds"] == 1 and team["revisions"] == 0 and team["llm_calls"] == 3
    assert len(provider.calls) == 3
    resumed = graph.invoke(Command(resume={"decision": "accept", "reviewer_id": "operator"}), config)
    assert resumed["decision"]["reviewer_id"] == "operator"
    assert resumed["analysis"] == analysis and graph.get_state(config).next == ()
    assert len(provider.calls) == 3


def test_one_revision_preserves_findings_history_and_all_usage(settings, provider, monkeypatch):
    provider.initial = GOOD_DRAFT + " " + BAD_CLAIM
    provider.reviews = [verdict("revise", [finding()]), verdict()]
    real_retrieve = review_team.retrieve

    def metered_retrieve(*args):
        sources = real_retrieve(*args)
        record_embedding_usage({"total_tokens": 40}, settings)
        return sources

    monkeypatch.setattr(review_team, "retrieve", metered_retrieve)
    analysis, _, _ = run_graph(settings)
    team = analysis["review_team"]
    assert [name for name, _ in provider.calls] == [
        "supportops_extraction", "supportops_draft", "supportops_safety_review",
        "supportops_draft_revision", "supportops_safety_review",
    ]
    assert provider.calls[3][1]["review_findings"][0]["claim"] == BAD_CLAIM
    assert provider.calls[4][1]["draft"] == GOOD_DRAFT
    assert team["limits"] == {"max_revisions": 1, "max_llm_calls": 5}
    assert team["revisions"] == 1 and team["llm_calls"] == 5 and team["status"] == "passed"
    assert analysis["draft"] == GOOD_DRAFT and analysis["next_step"] == "escalate"
    assert [row["verdict"] for row in team["draft_history"]] == ["revise", "approved"]
    assert BAD_CLAIM in team["draft_history"][0]["draft"]
    assert team["disagreements"][0]["blocking"] is False
    assert team["disagreements"][0]["message"].startswith("Resolved by revision:")
    assert analysis["input_tokens"] == 500 and analysis["output_tokens"] == 150
    assert analysis["embedding_input_tokens"] == 40 and analysis["embedding_requests"] == 1
    assert analysis["cost_usd"] == pytest.approx(0.000804)
    assert sum(row["cost_usd"] for row in team["roles"]) == pytest.approx(analysis["cost_usd"])


@pytest.mark.parametrize("second_verdict", ["revise", "blocked"])
def test_second_rejection_stops_at_one_revision_and_uses_safe_template(settings, provider, second_verdict):
    provider.initial = provider.revised = GOOD_DRAFT + " " + BAD_CLAIM
    provider.reviews = [verdict("revise", [finding()]), verdict(second_verdict, [finding()])]
    analysis, _, _ = run_graph(settings)
    team = analysis["review_team"]
    assert len(provider.calls) == 5 and team["revisions"] == 1 and team["rounds"] == 2
    assert team["status"] == "needs_review" and analysis["next_step"] == "clarify"
    assert team["displayed_draft_origin"] == "local_safety_template"
    assert BAD_CLAIM not in analysis["draft"] and "Please confirm" in analysis["draft"]
    assert BAD_CLAIM in team["draft_history"][-1]["draft"]
    assert any(row["blocking"] for row in team["disagreements"])


@pytest.mark.parametrize("failure", ["timeout", "incomplete", "refusal", "invalid_schema"])
def test_reviewer_failure_requires_clarification_and_retains_available_usage(settings, provider, failure):
    provider.review_failure = failure
    analysis, _, _ = run_graph(settings)
    team = analysis["review_team"]
    assert team["reviewer_verdict"] == "unavailable" and team["revisions"] == 0
    assert len(provider.calls) == 3 and analysis["next_step"] == "clarify"
    assert team["status"] == "needs_review"
    assert team["displayed_draft_origin"] == "local_safety_template"
    assert any(row["code"] == "reviewer_unavailable" for row in team["disagreements"])
    assert next(row for row in team["roles"] if row["role"] == "safety")["status"] == "error"
    if failure == "timeout":
        assert analysis["cost_usd"] is None and analysis["input_tokens"] == 200
    else:
        assert analysis["cost_usd"] == pytest.approx(0.00048) and analysis["input_tokens"] == 300
    assert "private provider failure" not in json.dumps(analysis)


@pytest.mark.parametrize("invalid_finding", [
    finding(claim="This sentence does not appear in the candidate."),
    finding(claim=None, source_ids=["other-tenant-document"]),
    finding(claim=None, source_ids=["execute_github_issue"]),
])
def test_review_cannot_invent_a_quote_or_evidence_identifier(settings, provider, invalid_finding):
    provider.reviews = [verdict("approved", [invalid_finding])]
    analysis, _, _ = run_graph(settings)
    team = analysis["review_team"]
    assert team["reviewer_verdict"] == "blocked" and team["revisions"] == 0
    assert analysis["next_step"] == "clarify" and team["status"] == "needs_review"
    assert any(row["code"] == "invalid_review_evidence" for row in team["disagreements"])
    assert len(provider.calls) == 3


@pytest.mark.parametrize("draft,code", [
    (GOOD_DRAFT + " Additional evidence [invented-source].", "unknown_citation"),
    (GOOD_DRAFT.replace("[current]", ""), "missing_citation"),
])
def test_server_rejects_bad_citations_even_if_both_reviews_approve(settings, provider, draft, code):
    provider.initial = provider.revised = draft
    provider.reviews = [verdict(), verdict()]
    analysis, _, _ = run_graph(settings)
    team = analysis["review_team"]
    assert team["revisions"] == 1 and len(provider.calls) == 5
    assert team["reviewer_verdict"] == "revise" and analysis["next_step"] == "clarify"
    assert team["displayed_draft_origin"] == "local_safety_template"
    assert any(row["code"] == code and row["blocking"] for row in team["disagreements"])


def test_approved_verdict_with_blocking_finding_still_requires_revision(settings, provider):
    provider.initial = GOOD_DRAFT + " " + BAD_CLAIM
    provider.reviews = [verdict("approved", [finding()]), verdict()]
    analysis, _, _ = run_graph(settings)
    assert analysis["review_team"]["revisions"] == 1
    assert analysis["review_team"]["status"] == "passed"
    assert analysis["draft"] == GOOD_DRAFT


def test_revision_failure_keeps_original_history_and_unknown_cost(settings, provider):
    provider.initial = GOOD_DRAFT + " " + BAD_CLAIM
    provider.reviews = [verdict("revise", [finding()]), verdict()]
    provider.revision_failure = True
    analysis, _, _ = run_graph(settings)
    team = analysis["review_team"]
    assert team["revisions"] == 1 and len(provider.calls) == 5
    assert analysis["cost_usd"] is None and analysis["input_tokens"] == 400
    assert analysis["next_step"] == "clarify" and team["status"] == "needs_review"
    assert team["displayed_draft_origin"] == "local_safety_template"
    assert any(row["code"] == "draft_unavailable" for row in team["disagreements"])
    assert team["draft_history"][0]["draft"] == provider.initial


def test_conflicting_evidence_cannot_be_promoted_by_reviewer(settings, provider):
    provider.initial = "Instructions conflict; a specialist must verify the evidence [current]."
    analysis, _, _ = run_graph(settings, [{**DOCUMENT, "conflict_group": "upgrade-conflict"}])
    assert provider.calls[1][1]["next_step"] == "clarify"
    assert provider.calls[2][1]["next_step"] == "clarify"
    assert analysis["next_step"] == "clarify" and analysis["review_team"]["status"] == "needs_review"
    assert any(row["code"] == "conflicting_evidence" for row in analysis["review_team"]["disagreements"])


@pytest.mark.parametrize("failure", ["timeout", "error"])
def test_demo_can_reference_failed_requests_without_a_spurious_revision(settings, provider, monkeypatch, failure):
    settings.llm_provider = "demo"
    observed = [{**row, "status": failure, "data": None, "error": "upstream_" + failure}
                for row in DIAGNOSTICS]
    monkeypatch.setattr(review_team, "diagnostics", lambda *args, **kwargs: observed)
    analysis, _, _ = run_graph(settings)
    team = analysis["review_team"]
    assert analysis["next_step"] == "clarify" and team["status"] == "needs_review"
    assert team["reviewer_verdict"] == "approved" and team["revisions"] == 0 and team["rounds"] == 1
    assert team["displayed_draft_origin"] == "demo" and provider.calls == []
    assert "[get_service_status]" in analysis["draft"] and "[get_recent_changes]" in analysis["draft"]
    assert "upstream_" + failure in analysis["draft"]
    assert not any(fact["kind"] == "api" for fact in analysis["facts"])
    assert any(row["code"] == "diagnostic_uncertainty" and row["blocking"] for row in team["disagreements"])
    assert not any(row["code"] in {"unknown_citation", "invalid_review_evidence"} for row in team["disagreements"])


@pytest.mark.parametrize("failure", ["timeout", "error"])
def test_live_review_can_cite_failure_as_unknown_state_without_promoting_route(settings, provider, monkeypatch, failure):
    observed = [{**DIAGNOSTICS[0], "status": failure, "data": None, "error": "upstream_" + failure},
                DIAGNOSTICS[1]]
    monkeypatch.setattr(review_team, "diagnostics", lambda *args, **kwargs: observed)
    claim = "Service health remains unknown after the failed diagnostic request."
    provider.initial = claim + " [get_service_status] Please verify diagnostics before applying the runbook [current]."
    provider.reviews = [verdict("approved", [{
        "code": "diagnostic_uncertainty", "message": "The failed request supports unknown health only.",
        "blocking": False, "claim": claim, "source_ids": ["get_service_status"],
    }])]
    analysis, _, _ = run_graph(settings)
    team = analysis["review_team"]
    assert analysis["draft"] == provider.initial
    assert team["reviewer_verdict"] == "approved" and team["revisions"] == 0 and len(provider.calls) == 3
    assert analysis["next_step"] == "clarify" and team["status"] == "needs_review"
    assert provider.calls[1][1]["next_step"] == "clarify" and provider.calls[2][1]["next_step"] == "clarify"
    assert [fact["source_id"] for fact in analysis["facts"] if fact["kind"] == "api"] == ["get_recent_changes"]
    assert not any(row["code"] in {"unknown_citation", "invalid_review_evidence"} for row in team["disagreements"])
    assert any(row["role"] == "diagnostics" and row["blocking"] for row in team["disagreements"])


def test_triage_and_diagnostics_run_concurrently_before_response(settings, provider, monkeypatch):
    barrier = Barrier(2, timeout=5)
    real_extract = intelligence.extract

    def extract(*args):
        barrier.wait()
        return real_extract(*args)

    def diagnostics(*args, **kwargs):
        barrier.wait()
        return DIAGNOSTICS

    monkeypatch.setattr(intelligence, "extract", extract)
    monkeypatch.setattr(review_team, "diagnostics", diagnostics)
    analysis, _, _ = run_graph(settings)
    assert analysis["next_step"] == "escalate"
    assert provider.calls[1][1]["sources"][0]["id"] == "current"
    assert len(provider.calls[1][1]["diagnostics"]) == 2
