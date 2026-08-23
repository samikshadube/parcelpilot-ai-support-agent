"""Test suite for SLA calculations, breach detection, and two-phase confirmed action execution."""

import pytest
from src.models import ActionStatus, UserContext
from src.tools.action_tools import execute_action, propose_action
from src.tools.query_structured_data import (
    calculate_ticket_sla,
    get_ticket_details,
)


def test_northstar_p1_sla_breach():
    """TKT-501 (P1 outage, created 10:30, snapshot 11:00 = 30 min elapsed).

    Under Northstar Agreement Section 1: P1 response target is 15 minutes.
    SLA is breached (30 > 15).
    """
    ctx_internal = UserContext(role="internal")
    sla = calculate_ticket_sla(ctx_internal, "TKT-501", severity="P1")

    assert sla["ticket_id"] == "TKT-501"
    assert sla["severity"] == "P1"
    assert sla["elapsed_minutes"] == 30
    assert sla["sla_target_minutes"] == 15
    assert sla["is_breached"] is True
    assert sla["requires_escalation"] is True


def test_axis_labs_p1_security_leak_sla():
    """TKT-505 (P1 security credential leak, created 08:30, snapshot 11:00 = 150 min elapsed).

    Under Support Policy v3 Enterprise: P1 target is 30 minutes.
    SLA is heavily breached.
    """
    ctx_internal = UserContext(role="internal")
    sla = calculate_ticket_sla(ctx_internal, "TKT-505", severity="P1")

    assert sla["ticket_id"] == "TKT-505"
    assert sla["severity"] == "P1"
    assert sla["elapsed_minutes"] == 150
    assert sla["sla_target_minutes"] == 30
    assert sla["is_breached"] is True


def test_two_phase_action_lifecycle():
    """Verify confirm-before-action two-phase lifecycle: propose -> verify pending -> execute."""
    ctx_internal = UserContext(role="internal", internal_role="support_lead")

    # Step 1: Propose Action (stages, does NOT execute)
    prop = propose_action(
        ctx=ctx_internal,
        action_type="create_escalation",
        target_entity_type="ticket",
        target_entity_id="TKT-503",
        description="Escalate configuration query to billing team lead.",
    )
    assert prop["status"] == "staged_pending_confirmation"
    action_id = prop["action_id"]

    # Verify ticket was NOT updated before execution
    t_before = get_ticket_details(ctx_internal, "TKT-503")
    assert t_before["status"] == "open"

    # Step 2: Execute Action after user confirmation
    exec_res = execute_action(ctx=ctx_internal, action_id=action_id)
    assert exec_res["status"] == "executed"
    assert "escalated" in exec_res["execution_result"]

    # Verify ticket state is now modified in database
    t_after = get_ticket_details(ctx_internal, "TKT-503")
    assert t_after["status"] == "escalated"

    # Step 3: Prevent duplicate execution
    dup_exec = execute_action(ctx=ctx_internal, action_id=action_id)
    assert dup_exec.get("error") == "InvalidState"


def test_action_execution_revalidates_scope():
    """Executing an action re-validates that the caller has access at execution time."""
    ctx_internal = UserContext(role="internal")
    ctx_customer = UserContext(role="customer", account_id="ACCT-001")

    # Action staged on ACCT-002 ticket (TKT-502)
    prop = propose_action(
        ctx=ctx_internal,
        action_type="update_ticket",
        target_entity_type="ticket",
        target_entity_id="TKT-502",
        description="Internal update",
        parameters={"status": "in_progress"},
    )
    action_id = prop["action_id"]

    # ACCT-001 customer tries to execute the action staged on ACCT-002's ticket
    denied_exec = execute_action(ctx=ctx_customer, action_id=action_id)
    assert denied_exec.get("error") == "AccessDenied"


def test_tkt505_deterministic_investigation_content():
    """Regression test: deterministic engine for TKT-505 must answer the *actual* security
    investigation question — not just return P1/SLA/escalation metadata.

    Calls _run_deterministic_reasoner directly so NO LLM provider is involved.
    This pins the exact content the deterministic branch must produce for an API key
    exposure ticket so it cannot silently regress.

    TKT-505 facts (Axis Labs, Enterprise plan):
    - Subject: "Possible API key exposure"
    - Description: screenshot with production API key posted in a public channel
    - Created: 2026-08-16 08:30 | Snapshot: 11:00 → 150 min elapsed
    - Enterprise P1 SLA target: 30 min → BREACHED by 120 min

    Required deterministic response:
    1. Identifies the security risk (production API key exposed publicly)
    2. Explains why it is P1
    3. Recommends immediate key revocation / rotation
    4. Recommends auditing logs for unauthorized usage
    5. Recommends removing the exposed credential from the public channel
    6. Recommends escalating as a P1 security incident
    7. Includes a "confirmation / remediation" step
    8. Does NOT reproduce the actual API key value
    9. Still contains SLA breach and recommendation fields
    """
    from src.agent.loop import AgentOrchestrator

    orchestrator = AgentOrchestrator()
    ctx = UserContext(role="internal", internal_role="Security Lead")
    query = "Investigate TKT-505: what is the security risk, why is it P1, and what immediate actions should internal support take?"

    # Call the deterministic reasoner DIRECTLY — no LLM provider involved
    response = orchestrator._run_deterministic_reasoner(ctx=ctx, query=query)
    answer = response.answer.lower()

    # ── Ticket metadata ──────────────────────────────────────────────────────
    assert "505" in answer, "Must reference TKT-505"
    assert "p1" in answer, "Must identify P1 severity"
    assert "sla" in answer, "Must include SLA information"
    assert "breach" in answer or "breached" in answer, "Must flag SLA as breached"

    # ── Security Risk explanation ─────────────────────────────────────────────
    assert "security risk" in answer or "security incident" in answer, \
        "Must explain the security risk (P1 security incident)"
    assert any(w in answer for w in ["api key", "credential", "exposure"]), \
        "Must identify the nature of the risk: API key / credential exposure"
    assert any(w in answer for w in ["public", "public channel", "screenshot"]), \
        "Must note the exposure vector (public channel / screenshot)"
    assert "compromised" in answer or "unauthorized" in answer or "fully compromised" in answer, \
        "Must state the key should be treated as compromised"

    # ── Investigation Findings & Immediate Actions section ────────────────────
    assert "investigation findings" in answer, \
        "Must include an Investigation Findings section"
    assert "immediate actions" in answer, \
        "Deterministic engine must use the 'Immediate Actions' heading for P1 security tickets"

    # ── Immediate Action 1: Revoke / Rotate ───────────────────────────────────
    assert any(w in answer for w in ["revoke", "rotate", "rotation", "revocation"]), \
        "Must recommend revoking or rotating the exposed API key"

    # ── Immediate Action 2: Audit logs for unauthorized usage ─────────────────
    assert any(w in answer for w in ["audit", "logs", "log", "unauthorized", "access logs"]), \
        "Must recommend auditing logs for unauthorized usage"

    # ── Immediate Action 3: Remove credential from public channel ─────────────
    assert any(w in answer for w in ["remove", "delete", "redact"]), \
        "Must recommend removing / redacting the exposed credential"
    assert any(w in answer for w in ["public channel", "public location", "channel"]), \
        "Must reference removing it from the public channel"

    # ── Immediate Action 4: Escalate as P1 security incident ─────────────────
    assert any(w in answer for w in ["escalate", "escalation"]), \
        "Must recommend escalating the incident"
    assert any(w in answer for w in ["p1 security", "security incident", "security team", "ciso"]), \
        "Must frame escalation as a P1 security incident"

    # ── Immediate Action 5: Confirm remediation ───────────────────────────────
    assert any(w in answer for w in ["remediation", "remediate", "confirm", "verified", "verify"]), \
        "Must include a remediation confirmation step"

    # ── Safety: raw API key must NOT appear in response ───────────────────────
    import re
    suspicious = [
        tok for tok in re.findall(r"[A-Za-z0-9+/=_\-]{40,}", response.answer)
        if re.search(r"[A-Za-z0-9]", tok) and not re.fullmatch(r"[\-_=]+", tok)
    ]
    assert not suspicious, (
        f"Deterministic response must NOT reproduce any raw API key value. "
        f"Suspicious long tokens found: {suspicious}"
    )

    # ── Tool traces ───────────────────────────────────────────────────────────
    tool_names = [t.tool_name for t in response.tool_traces]
    assert "get_ticket_details" in tool_names, "Must have called get_ticket_details"
    assert "calculate_ticket_sla" in tool_names, "Must have called calculate_ticket_sla"

