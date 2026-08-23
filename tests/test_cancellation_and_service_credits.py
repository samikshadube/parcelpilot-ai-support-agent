"""Test suite for order cancellation rules, agreement overrides, and service credit calculations."""

import pytest
from src.models import UserContext
from src.tools.query_structured_data import (
    calculate_cancellation_terms,
    calculate_service_credit,
)


def test_northstar_cancellation_override():
    """Northstar ORD-1001 was requested 120 mins after booking before pickup.

    Under SOP v4, >30 mins would incur INR 250 fee.
    Under Northstar Agreement Section 2, cancellation fee is waived before pickup.
    """
    ctx_northstar = UserContext(role="customer", account_id="ACCT-001")
    res = calculate_cancellation_terms(ctx_northstar, "ORD-1001")

    assert res["order_id"] == "ORD-1001"
    assert res["can_cancel"] is True
    assert res["governing_fee_inr"] == 0.0
    assert res["agreement_override_applied"] is True
    assert "Enterprise Agreement" in res["governing_policy"]


def test_standard_cancellation_fee_applied():
    """LumenWorks ORD-2001 requested 75 mins after booking.

    LumenWorks agreement contains no cancellation waiver, so SOP v4 INR 250 fee applies.
    """
    ctx_lumenworks = UserContext(role="customer", account_id="ACCT-002")
    res = calculate_cancellation_terms(ctx_lumenworks, "ORD-2001")

    assert res["order_id"] == "ORD-2001"
    assert res["can_cancel"] is True
    assert res["governing_fee_inr"] == 250.0
    assert res["agreement_override_applied"] is False


def test_free_cancellation_within_window():
    """Beacon Retail ORD-3001 requested 15 mins after booking (within 30-min window).

    Under SOP v4, free cancellation applies.
    """
    ctx_beacon = UserContext(role="customer", account_id="ACCT-003")
    res = calculate_cancellation_terms(ctx_beacon, "ORD-3001")

    assert res["order_id"] == "ORD-3001"
    assert res["can_cancel"] is True
    assert res["governing_fee_inr"] == 0.0


def test_picked_up_order_cannot_cancel():
    """ORD-1002 is already PICKED_UP. Standard cancellation is not permitted."""
    ctx_internal = UserContext(role="internal")
    res = calculate_cancellation_terms(ctx_internal, "ORD-1002")

    assert res["can_cancel"] is False
    assert "PICKED_UP" in res["order_status"]


def test_lumenworks_service_credit_override():
    """LumenWorks ORD-2002 missed pickup: scheduled window ended 06:30, snapshot is 11:00 (4.5h delay).

    Carrier fault is True.
    Under LumenWorks Agreement Section 3: delay > 4 hours grants fixed INR 300 service credit.
    """
    ctx_lumenworks = UserContext(role="customer", account_id="ACCT-002")
    res = calculate_service_credit(ctx_lumenworks, "ORD-2002")

    assert res["order_id"] == "ORD-2002"
    assert res["is_eligible"] is True
    assert res["credit_amount_inr"] == 300.0
    assert res["carrier_fault"] is True
    assert "Service Agreement" in res["governing_policy"]
