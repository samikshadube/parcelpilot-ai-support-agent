"""Test suite for access control and data privacy scoping."""

import pytest
from src.db import init_db
from src.ingest.data_loader import ingest_structured_data
from src.ingest.doc_loader import ingest_documents
from src.models import UserContext
from src.tools.action_tools import propose_action
from src.tools.query_structured_data import (
    get_account_details,
    get_order_details,
    get_ticket_details,
    list_orders,
    list_tickets,
)
from src.tools.search_documents import search_documents


@pytest.fixture(scope="session", autouse=True)
def setup_test_data():
    """Ensure DB and documents are ingested for tests."""
    init_db()
    ingest_structured_data()
    ingest_documents()


def test_customer_document_search_isolation():
    """Customer ACCT-001 can view general docs and Northstar agreement, but NOT LumenWorks agreement."""
    ctx_northstar = UserContext(role="customer", account_id="ACCT-001")
    ctx_lumenworks = UserContext(role="customer", account_id="ACCT-002")
    ctx_internal = UserContext(role="internal", internal_role="support_lead")

    # Search for agreement terms
    res_northstar = search_documents(ctx_northstar, "service agreement cancellation fee support terms")
    files_northstar = [r["source_file"] for r in res_northstar["results"]]

    # ACCT-001 should see its own agreement or general docs, but never LumenWorks agreement
    assert "06_LumenWorks_Service_Agreement.pdf" not in files_northstar
    assert any("05_Northstar" in f or "01_Support" in f or "03_Cancellation" in f for f in files_northstar)

    # ACCT-002 should see LumenWorks agreement, but never Northstar agreement
    res_lumenworks = search_documents(ctx_lumenworks, "service agreement cancellation fee support terms")
    files_lumenworks = [r["source_file"] for r in res_lumenworks["results"]]
    assert "05_Northstar_Logistics_Enterprise_Agreement.pdf" not in files_lumenworks

    # Internal user can view any customer agreement
    res_internal = search_documents(ctx_internal, "service agreement cancellation fee support terms", n_results=10)
    files_internal = [r["source_file"] for r in res_internal["results"]]
    assert any("05_Northstar" in f for f in files_internal)
    assert any("06_LumenWorks" in f for f in files_internal)


def test_customer_order_scoping():
    """Customer ACCT-001 can access ORD-1001, but is denied ORD-2001."""
    ctx_customer = UserContext(role="customer", account_id="ACCT-001")

    # Happy path: Own order
    own_order = get_order_details(ctx_customer, "ORD-1001")
    assert "error" not in own_order
    assert own_order["order_id"] == "ORD-1001"
    assert own_order["account_id"] == "ACCT-001"

    # Unauthorized access: Other customer's order
    other_order = get_order_details(ctx_customer, "ORD-2001")
    assert other_order.get("error") == "AccessDenied"
    # Should not leak any order details
    assert "shipment_fee_inr" not in other_order


def test_customer_ticket_scoping():
    """Customer ACCT-001 can access TKT-501, but is denied TKT-502."""
    ctx_customer = UserContext(role="customer", account_id="ACCT-001")

    # Happy path
    own_ticket = get_ticket_details(ctx_customer, "TKT-501")
    assert "error" not in own_ticket
    assert own_ticket["ticket_id"] == "TKT-501"
    assert own_ticket["account_id"] == "ACCT-001"

    # Out of scope
    other_ticket = get_ticket_details(ctx_customer, "TKT-502")
    assert other_ticket.get("error") == "AccessDenied"
    assert "subject" not in other_ticket


def test_customer_list_isolation():
    """list_orders and list_tickets strictly filter to caller's account_id."""
    ctx_customer = UserContext(role="customer", account_id="ACCT-001")

    orders = list_orders(ctx_customer)
    for o in orders["orders"]:
        assert o["account_id"] == "ACCT-001"

    tickets = list_tickets(ctx_customer)
    for t in tickets["tickets"]:
        assert t["account_id"] == "ACCT-001"


def test_customer_action_proposal_scoping():
    """Customer cannot propose actions on another account's entities."""
    ctx_customer = UserContext(role="customer", account_id="ACCT-001")

    # Trying to escalate another customer's ticket (TKT-502 belongs to ACCT-002)
    prop = propose_action(
        ctx=ctx_customer,
        action_type="create_escalation",
        target_entity_type="ticket",
        target_entity_id="TKT-502",
        description="Unauthorized escalation attempt",
    )
    assert prop.get("error") == "AccessDenied"
