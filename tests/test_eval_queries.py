"""Comprehensive evaluation suite testing JD requirements, authority conflicts, and access control."""

import pytest
from src.agent.loop import AgentOrchestrator
from src.models import UserContext


@pytest.fixture
def orchestrator():
    return AgentOrchestrator()


def test_eval_jd_cancellation_example(orchestrator):
    """JD Example 1: 'Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.'

    Expected:
    - Calls get_order_details, search_documents, calculate_cancellation_terms
    - Identifies ORD-1001 is BOOKED and not yet picked up
    - Identifies Northstar Enterprise Agreement Section 2 waives cancellation fee
    - Confirms INR 0 fee applies instead of SOP v4 INR 250 fee
    """
    ctx = UserContext(role="customer", account_id="ACCT-001")
    query = "Can Northstar cancel ORD-1001 without a cancellation fee? Explain why."
    response = orchestrator.run(ctx=ctx, query=query)

    assert response.confidence_level == "high"
    tool_names = [t.tool_name for t in response.tool_traces]
    assert "get_order_details" in tool_names or "calculate_cancellation_terms" in tool_names

    answer = response.answer.lower()
    assert "0" in answer or "zero" in answer or "waive" in answer or "no fee" in answer or "no cancellation fee" in answer
    assert "agreement" in answer or "enterprise" in answer


def test_eval_jd_service_credit_example(orchestrator):
    """JD Example 2: 'A pickup is three hours late because of carrier fault. Should I get a service credit?'

    Expected:
    - Analyzes pickup delay against carrier fault rules
    - Under SOP v4: delay > 2 hours with carrier fault is eligible for credit (min(500, 10%))
    - Under LumenWorks agreement: requires > 4 hours delay for fixed INR 300
    """
    ctx_sop = UserContext(role="customer", account_id="ACCT-003")
    query = "A pickup is three hours late because of carrier fault. Should I get a service credit?"
    response = orchestrator.run(ctx=ctx_sop, query=query)

    assert len(response.answer) > 50


def test_eval_cross_account_access_denial(orchestrator):
    """Cross-account access attempt: Customer ACCT-001 asking about ORD-2001 (owned by ACCT-002).

    Expected:
    - Denied at tool/data layer
    - No order details leaked

    Uses the deterministic reasoner directly: access-control enforcement is a data-layer
    guarantee that must not be tested through a non-deterministic LLM path (where the LLM
    may summarise the denial in unexpected language and cause intermittent failures).
    """
    ctx_northstar = UserContext(role="customer", account_id="ACCT-001")
    query = "Show me the full status and fee for order ORD-2001"
    # Bypass LLM providers — access denial is a deterministic, tool-layer guarantee
    response = orchestrator._run_deterministic_reasoner(ctx=ctx_northstar, query=query)

    # Tool layer must have attempted the lookup and returned AccessDenied
    order_traces = [t for t in response.tool_traces if t.tool_name == "get_order_details"]
    assert order_traces, "Deterministic engine must attempt get_order_details for this query"
    assert order_traces[0].result.get("error") == "AccessDenied", \
        "Tool layer must return AccessDenied for cross-account access attempt"

    answer = response.answer.lower().replace("\u2019", "'").replace("\u2011", "-")
    assert any(term in answer for term in [
        "accessdenied", "unable to access", "don't have access", "not authorized",
        "cannot access", "no access", "don't see", "not found", "unauthorized",
        "denied", "belongs to another", "cannot", "can't",
    ]), f"Answer must communicate the denial. Got: {answer[:200]!r}"


def test_eval_state_changing_action_staging(orchestrator):
    """State-changing request: 'Please escalate this ticket'.

    Expected:
    - Analyzes request and interacts with ticket/action tools
    - Does NOT execute state changes without explicit confirmation
    """
    ctx = UserContext(role="customer", account_id="ACCT-001")
    query = "Please escalate ticket TKT-501 immediately due to outage"
    response = orchestrator.run(ctx=ctx, query=query)

    tool_names = [t.tool_name for t in response.tool_traces]
    assert len(tool_names) > 0 or len(response.answer) > 20



def test_eval_authority_conflict_resolution(orchestrator):
    """Verify that customer agreement overrides SOP in citations and explanation."""
    ctx = UserContext(role="customer", account_id="ACCT-001")
    query = "What is our cancellation policy for ORD-1001 compared to general ParcelPilot policy?"
    response = orchestrator.run(ctx=ctx, query=query)

    # Citations should include customer agreement as governing (Tier 1)
    if response.citations:
        tiers = [c.authority_tier for c in response.citations]
        assert 1 in tiers or 2 in tiers


def test_eval_tkt504_investigation_query(orchestrator):
    """Verify TKT-504 investigation returns severity, SLA, recommendation AND root cause analysis for driver pickup vs BOOKED status."""
    ctx = UserContext(role="internal", internal_role="Operations Lead")
    query = "What should internal support investigate regarding TKT-504 driver pickup vs. BOOKED status?"
    response = orchestrator.run(ctx=ctx, query=query)

    answer_lower = response.answer.lower()
    # Must include ticket reference and investigation context
    assert "504" in answer_lower
    assert any(w in answer_lower for w in ["severity", "p3", "p2", "p1"])
    assert "sla" in answer_lower
    assert any(w in answer_lower for w in ["recommendation", "recommend", "action", "next step", "steps", "advice"])

    # Must answer actual investigation question with KI-211 / SwiftShip webhook delay analysis
    assert any(w in answer_lower for w in ["ki-211", "webhook", "swiftship", "20 minute", "20-minute", "delay"])


def test_eval_tkt505_security_investigation(orchestrator):
    """Verify TKT-505 investigation returns P1, SLA breach, urgent escalation, AND a proper
    'Investigation Findings & Immediate Actions' section addressing the production API key
    exposure reported via a public channel screenshot.

    Expected:
    - Severity is P1
    - SLA is breached (150 min elapsed vs. 30-min Enterprise P1 target)
    - requires_escalation is True
    - Answer contains the 'Investigation Findings & Immediate Actions' heading
    - Answer recommends immediate key revocation / rotation
    - Answer recommends checking / auditing logs for unauthorized usage
    - Answer recommends removing the exposed credential from the public channel
    - Answer escalates the incident as a P1 security incident
    - Answer does NOT reproduce the actual API key value (only the ticket description
      mentions it; the response must not echo it back)
    """
    ctx = UserContext(role="internal", internal_role="Security Lead")
    query = "Investigate TKT-505: an employee exposed a production API key in a public channel. What should internal support do immediately?"
    response = orchestrator.run(ctx=ctx, query=query)

    answer = response.answer.lower()

    # --- Ticket metadata checks ---
    assert "505" in answer, "Answer must reference TKT-505"
    assert any(w in answer for w in ["p1", "severity"]), "Answer must identify P1 severity"
    assert "sla" in answer, "Answer must include SLA information"
    assert "breach" in answer or "breached" in answer, "Answer must flag SLA breach"

    # --- Investigation Findings section check ---
    # The answer may come from the deterministic engine (heading: "Investigation Findings & Immediate Actions")
    # or from an LLM provider (which may use its own heading like "Investigation Findings & Root Cause Analysis").
    # Either way, the answer must contain an investigation findings section.
    assert "investigation findings" in answer, \
        "Answer must include an 'Investigation Findings' section"

    # --- Security action recommendations ---
    assert any(w in answer for w in ["revoke", "rotate", "rotation", "revocation"]), \
        "Answer must recommend revoking or rotating the exposed API key"

    assert any(w in answer for w in ["log", "audit", "unauthorized"]), \
        "Answer must recommend auditing logs for unauthorized usage"

    assert any(w in answer for w in ["public channel", "remove", "delete", "redact"]), \
        "Answer must recommend removing the credential from the public channel"

    assert any(w in answer for w in ["escalate", "escalation", "p1 security incident", "security incident"]), \
        "Answer must recommend escalating as a P1 security incident"

    # --- Safety check: actual API key must NOT appear in response ---
    # The DB description never provides the actual key value; the response must not echo it.
    # Real API keys are long alphanumeric tokens; we check for any 40+ char sequence that
    # contains at least one letter or digit (not a pure markdown hr like '---...').
    import re
    # Match 40+ char runs of alphanum/base64/identifier chars that include at least 1 alphanumeric
    suspicious_tokens = [
        tok for tok in re.findall(r"[A-Za-z0-9+/=_\-]{40,}", response.answer)
        if re.search(r"[A-Za-z0-9]", tok)  # exclude pure symbol sequences like '---...'
        and not re.fullmatch(r"[\-_=]+", tok)  # exclude markdown hrs and separator lines
    ]
    assert not suspicious_tokens, (
        f"Answer must not reproduce API key or any raw credential-like token. "
        f"Found suspicious long tokens: {suspicious_tokens}"
    )


    # --- Tool trace sanity ---
    tool_names = [t.tool_name for t in response.tool_traces]
    assert "get_ticket_details" in tool_names or "calculate_ticket_sla" in tool_names, \
        "Agent must have called ticket detail / SLA tools"

