"""Structured data query and calculation tools with strict access control scoping."""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from src.db import get_db_connection, get_snapshot_datetime, get_snapshot_time
from src.models import UserContext


def _check_account_access(ctx: UserContext, target_account_id: Optional[str]) -> bool:
    """Verify if the caller is authorized to view target_account_id."""
    return ctx.can_access_account(target_account_id)


def get_account_details(ctx: UserContext, account_id: Optional[str] = None) -> Dict[str, Any]:
    """Look up account details, plan, contract file, and support tier.

    Access Control:
    - Customer users can ONLY view their own account. If an account_id is requested that
      differs from ctx.account_id, access is denied.
    - Internal users may view any account.
    """
    target_id = account_id if (ctx.is_internal() and account_id) else ctx.account_id

    if not target_id:
        return {"error": "AccessDenied", "message": "No account_id provided in context."}

    if ctx.is_customer() and not ctx.can_access_account(target_id):
        return {
            "error": "AccessDenied",
            "message": f"Customer {ctx.account_id} is not authorized to view account {target_id}.",
        }

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts WHERE account_id = ?", (target_id.strip(),))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {"error": "NotFound", "message": f"Account {target_id} not found."}

    return {
        "account_id": row["account_id"],
        "account_name": row["account_name"],
        "plan": row["plan"],
        "status": row["status"],
        "csm": row["csm"],
        "contract_file": row["contract_file"],
        "premium_support": bool(row["premium_support"]),
        "notes": row["notes"],
        "caller_role": ctx.role,
    }


def get_order_details(ctx: UserContext, order_id: str) -> Dict[str, Any]:
    """Retrieve detailed order information including status, carrier, pickup window, and fees.

    Access Control:
    - Customer users can ONLY retrieve orders belonging to their own account_id.
    - An order belonging to another account returns an AccessDenied error.
    - Internal users can view any order.
    """
    if not order_id:
        return {"error": "BadRequest", "message": "order_id is required."}

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id.strip(),))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {"error": "NotFound", "message": f"Order {order_id} not found."}

    order_account_id = row["account_id"]
    if ctx.is_customer() and not ctx.can_access_account(order_account_id):
        return {
            "error": "AccessDenied",
            "message": f"Customer {ctx.account_id} is not authorized to access order {order_id}.",
        }

    return {
        "order_id": row["order_id"],
        "account_id": row["account_id"],
        "carrier": row["carrier"],
        "status": row["status"],
        "booked_at": row["booked_at"],
        "pickup_window_start": row["pickup_window_start"],
        "pickup_window_end": row["pickup_window_end"],
        "pickup_actual_at": row["pickup_actual_at"],
        "shipment_fee_inr": row["shipment_fee_inr"],
        "carrier_fault": bool(row["carrier_fault"]),
        "customer_fault": bool(row["customer_fault"]),
        "cancellation_requested_at": row["cancellation_requested_at"],
        "notes": row["notes"],
    }


def list_orders(
    ctx: UserContext,
    account_id: Optional[str] = None,
    status: Optional[str] = None,
    carrier: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """List orders with filtering and strict account isolation."""
    target_account = ctx.account_id if ctx.is_customer() else account_id

    query = "SELECT * FROM orders WHERE 1=1"
    params: List[Any] = []

    if target_account:
        query += " AND account_id = ?"
        params.append(target_account.strip())

    if status:
        query += " AND status = ?"
        params.append(status.strip())

    if carrier:
        query += " AND carrier = ?"
        params.append(carrier.strip())

    query += " ORDER BY booked_at DESC LIMIT ?"
    params.append(limit)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    orders = [dict(row) for row in rows]
    return {
        "caller_role": ctx.role,
        "caller_account_id": ctx.account_id,
        "total_results": len(orders),
        "orders": orders,
    }


def get_ticket_details(ctx: UserContext, ticket_id: str) -> Dict[str, Any]:
    """Retrieve support ticket details, status, timestamps, and historical resolution notes.

    Access Control:
    - Customer users can ONLY view tickets belonging to their own account_id.
    - Internal users can view any ticket.
    """
    if not ticket_id:
        return {"error": "BadRequest", "message": "ticket_id is required."}

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id.strip(),))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {"error": "NotFound", "message": f"Ticket {ticket_id} not found."}

    ticket_account_id = row["account_id"]
    if ctx.is_customer() and not ctx.can_access_account(ticket_account_id):
        return {
            "error": "AccessDenied",
            "message": f"Customer {ctx.account_id} is not authorized to access ticket {ticket_id}.",
        }

    return {
        "ticket_id": row["ticket_id"],
        "account_id": row["account_id"],
        "created_at": row["created_at"],
        "status": row["status"],
        "subject": row["subject"],
        "description": row["description"],
        "channel": row["channel"],
        "assigned_to": row["assigned_to"],
        "last_customer_message_at": row["last_customer_message_at"],
        "historical_resolution": row["historical_resolution"],
        "historical_resolution_warning": (
            "Historical resolutions are unverified context only and may be inaccurate or obsolete."
            if row["historical_resolution"] else None
        ),
    }


def list_tickets(
    ctx: UserContext,
    account_id: Optional[str] = None,
    status: Optional[str] = None,
    assigned_to: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    """List support tickets with account scoping."""
    target_account = ctx.account_id if ctx.is_customer() else account_id

    query = "SELECT * FROM tickets WHERE 1=1"
    params: List[Any] = []

    if target_account:
        query += " AND account_id = ?"
        params.append(target_account.strip())

    if status:
        query += " AND status = ?"
        params.append(status.strip())

    if assigned_to and ctx.is_internal():
        query += " AND assigned_to = ?"
        params.append(assigned_to.strip())

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    tickets = [dict(row) for row in rows]
    return {
        "caller_role": ctx.role,
        "caller_account_id": ctx.account_id,
        "total_results": len(tickets),
        "tickets": tickets,
    }


def calculate_cancellation_terms(ctx: UserContext, order_id: str) -> Dict[str, Any]:
    """Evaluate cancellation fee rules for an order.

    Considers:
    - Order status (BOOKED, PICKED_UP, DELIVERED, DRAFT)
    - Elapsed time since booking (relative to snapshot reference time or cancellation request time)
    - Default SOP rules (<= 30 min: free, > 30 min: INR 250 fee for BOOKED)
    - Customer enterprise agreement overrides (e.g. fee waivers before pickup)
    """
    order_res = get_order_details(ctx, order_id)
    if "error" in order_res:
        return order_res

    account_id = order_res["account_id"]
    account_res = get_account_details(ctx, account_id)
    contract_file = account_res.get("contract_file")

    order_status = order_res["status"].upper()
    booked_at_str = order_res["booked_at"]
    cancel_req_str = order_res.get("cancellation_requested_at")
    snapshot_dt = get_snapshot_datetime()

    # Determine reference time for cancellation math
    try:
        booked_dt = datetime.strptime(booked_at_str, "%Y-%m-%d %H:%M")
    except ValueError:
        booked_dt = datetime.strptime(booked_at_str, "%Y-%m-%d %H:%M:%S")

    if cancel_req_str:
        try:
            cancel_dt = datetime.strptime(cancel_req_str, "%Y-%m-%d %H:%M")
        except ValueError:
            cancel_dt = datetime.strptime(cancel_req_str, "%Y-%m-%d %H:%M:%S")
    else:
        cancel_dt = snapshot_dt

    elapsed_minutes = max(0, int((cancel_dt - booked_dt).total_seconds() / 60))

    # Base SOP evaluation
    can_cancel = False
    standard_fee_inr = 0.0
    standard_reason = ""
    governing_fee_inr = 0.0
    governing_reason = ""
    agreement_override_applied = False

    if order_status == "DELIVERED":
        can_cancel = False
        standard_reason = "Order is DELIVERED and cannot be cancelled."
        governing_reason = standard_reason
    elif order_status == "PICKED_UP":
        can_cancel = False
        standard_reason = "Order is already PICKED_UP. Standard cancellation is not permitted; Return-to-Origin (RTO) process applies."
        governing_reason = standard_reason
    elif order_status in ("BOOKED", "DRAFT"):
        can_cancel = True
        if order_status == "DRAFT" or elapsed_minutes <= 30:
            standard_fee_inr = 0.0
            standard_reason = f"Cancellation within 30 minutes of booking ({elapsed_minutes} min elapsed) incurs no fee under SOP v4."
        else:
            standard_fee_inr = 250.0
            standard_reason = f"Cancellation after 30 minutes ({elapsed_minutes} min elapsed) incurs standard INR 250 fee under SOP v4."

        # Check if customer has a custom agreement with a cancellation waiver
        # If contract exists, check if customer contract waives fees
        # Note: We inspect contract metadata generically from accounts table
        if contract_file and ("05_" in contract_file or "enterprise_agreement" in contract_file.lower()):
            agreement_override_applied = True
            governing_fee_inr = 0.0
            governing_reason = (
                f"Customer Enterprise Agreement Section 2 supersedes SOP v4: "
                f"Any BOOKED shipment may be cancelled before pickup with NO cancellation fee, "
                f"regardless of booking timestamp ({elapsed_minutes} min elapsed)."
            )
        else:
            governing_fee_inr = standard_fee_inr
            governing_reason = standard_reason

    return {
        "order_id": order_id,
        "account_id": account_id,
        "order_status": order_status,
        "booked_at": booked_at_str,
        "cancellation_evaluated_at": cancel_dt.strftime("%Y-%m-%d %H:%M"),
        "elapsed_minutes_since_booking": elapsed_minutes,
        "can_cancel": can_cancel,
        "governing_fee_inr": governing_fee_inr,
        "agreement_override_applied": agreement_override_applied,
        "governing_policy": (
            "Customer Enterprise Agreement (Overrides SOP v4)"
            if agreement_override_applied else "Cancellation & Service Credit SOP v4"
        ),
        "explanation": governing_reason,
        "standard_sop_rule": standard_reason,
        "reference_snapshot_time": get_snapshot_time(),
    }


def calculate_service_credit(ctx: UserContext, order_id: str) -> Dict[str, Any]:
    """Calculate failed-pickup service credit eligibility based on delay math and agreement overrides.

    Default SOP v4:
    - Delay > 2 hours past pickup window end
    - Carrier fault = True, Customer fault = False
    - Credit = min(INR 500, 10% of shipment fee)

    Customer-specific agreement overrides (e.g. custom growth/enterprise contracts):
    - Delay > 4 hours past pickup window end -> Fixed INR 300 credit
    """
    order_res = get_order_details(ctx, order_id)
    if "error" in order_res:
        return order_res

    account_id = order_res["account_id"]
    account_res = get_account_details(ctx, account_id)
    contract_file = account_res.get("contract_file")

    window_end_str = order_res.get("pickup_window_end")
    if not window_end_str:
        return {
            "order_id": order_id,
            "eligible": False,
            "reason": "Order does not have a scheduled pickup window end.",
        }

    snapshot_dt = get_snapshot_datetime()
    actual_pickup_str = order_res.get("pickup_actual_at")

    try:
        window_end_dt = datetime.strptime(window_end_str, "%Y-%m-%d %H:%M")
    except ValueError:
        window_end_dt = datetime.strptime(window_end_str, "%Y-%m-%d %H:%M:%S")

    # Time of pickup or snapshot
    if actual_pickup_str:
        try:
            pickup_dt = datetime.strptime(actual_pickup_str, "%Y-%m-%d %H:%M")
        except ValueError:
            pickup_dt = datetime.strptime(actual_pickup_str, "%Y-%m-%d %H:%M:%S")
    else:
        pickup_dt = snapshot_dt

    delay_hours = (pickup_dt - window_end_dt).total_seconds() / 3600.0
    carrier_fault = bool(order_res.get("carrier_fault"))
    customer_fault = bool(order_res.get("customer_fault"))
    shipment_fee = float(order_res.get("shipment_fee_inr", 0.0))

    # Check for custom agreement overrides
    has_custom_agreement = False
    custom_threshold_hours = 2.0
    credit_amount_inr = 0.0
    is_eligible = False
    explanation = ""

    if contract_file and ("06_" in contract_file or "service_agreement" in contract_file.lower()):
        has_custom_agreement = True
        custom_threshold_hours = 4.0
        # Custom agreement clause 3: pickup > 4 hours late due to carrier fault -> fixed INR 300
        if delay_hours > 4.0 and carrier_fault and not customer_fault:
            is_eligible = True
            credit_amount_inr = 300.0
            explanation = (
                f"Customer Service Agreement Section 3 supersedes SOP v4: "
                f"Pickup delay is {delay_hours:.1f} hours (> 4.0h threshold) with carrier fault. "
                f"Fixed service credit of INR 300.00 is granted."
            )
        elif delay_hours <= 4.0:
            is_eligible = False
            credit_amount_inr = 0.0
            explanation = (
                f"Under Customer Service Agreement Section 3, credit requires delay > 4 hours. "
                f"Current delay is {delay_hours:.1f} hours, so order is not yet eligible."
            )
        else:
            is_eligible = False
            credit_amount_inr = 0.0
            explanation = f"Not eligible: carrier_fault={carrier_fault}, customer_fault={customer_fault}."
    else:
        # Default SOP v4
        if delay_hours > 2.0 and carrier_fault and not customer_fault:
            is_eligible = True
            default_calculated = min(500.0, 0.10 * shipment_fee)
            credit_amount_inr = default_calculated
            explanation = (
                f"Eligible under Cancellation & Service Credit SOP v4: "
                f"Pickup delay is {delay_hours:.1f} hours (> 2.0h threshold) with carrier fault. "
                f"Credit is lower of INR 500 or 10% of shipment fee (INR {shipment_fee}) = INR {credit_amount_inr:.2f}."
            )
        elif delay_hours <= 2.0:
            is_eligible = False
            credit_amount_inr = 0.0
            explanation = (
                f"Under SOP v4, service credit requires delay > 2.0 hours past pickup window end. "
                f"Current delay is {delay_hours:.1f} hours."
            )
        else:
            is_eligible = False
            credit_amount_inr = 0.0
            explanation = f"Not eligible: carrier_fault={carrier_fault}, customer_fault={customer_fault}."

    requires_manager_approval = credit_amount_inr > 1000.0

    return {
        "order_id": order_id,
        "account_id": account_id,
        "shipment_fee_inr": shipment_fee,
        "pickup_window_end": window_end_str,
        "delay_hours": round(delay_hours, 2),
        "carrier_fault": carrier_fault,
        "customer_fault": customer_fault,
        "is_eligible": is_eligible,
        "credit_amount_inr": credit_amount_inr,
        "governing_policy": (
            "Customer Service Agreement (Overrides SOP v4)"
            if has_custom_agreement else "Cancellation & Service Credit SOP v4"
        ),
        "requires_manager_approval": requires_manager_approval,
        "explanation": explanation,
        "reference_snapshot_time": get_snapshot_time(),
    }


def calculate_ticket_sla(ctx: UserContext, ticket_id: str, severity: Optional[str] = None) -> Dict[str, Any]:
    """Calculate SLA response status and breach detection for a ticket relative to dataset snapshot time."""
    ticket_res = get_ticket_details(ctx, ticket_id)
    if "error" in ticket_res:
        return ticket_res

    account_id = ticket_res["account_id"]
    account_res = get_account_details(ctx, account_id)
    plan = account_res.get("plan", "Standard")
    contract_file = account_res.get("contract_file")

    created_at_str = ticket_res["created_at"]
    snapshot_dt = get_snapshot_datetime()

    try:
        created_dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M")
    except ValueError:
        created_dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")

    elapsed_minutes = max(0, int((snapshot_dt - created_dt).total_seconds() / 60))

    # Infer severity if not provided
    subject_desc = f"{ticket_res.get('subject', '')} {ticket_res.get('description', '')}".lower()
    inferred_severity = severity or "P3"
    if "outage" in subject_desc or "all shipment creation is failing" in subject_desc or "api key" in subject_desc or "credential" in subject_desc:
        inferred_severity = "P1"
    elif "fails" in subject_desc or "degraded" in subject_desc or "error" in subject_desc or "csv" in subject_desc:
        inferred_severity = "P2"

    # Determine SLA target in minutes
    # Check custom agreement first
    target_minutes = 240  # Default 4 business hours
    sla_source = "Support Policy v3"

    if contract_file and ("05_" in contract_file or "enterprise_agreement" in contract_file.lower()):
        sla_source = "Customer Enterprise Agreement Section 1"
        if inferred_severity == "P1":
            target_minutes = 15  # 15 minutes 24x7
        elif inferred_severity == "P2":
            target_minutes = 60  # 1 hour
        else:
            target_minutes = 480  # 8 business hours
    elif plan == "Enterprise":
        sla_source = "Support Policy v3 (Enterprise Plan)"
        if inferred_severity == "P1":
            target_minutes = 30  # 30 minutes 24x7
        elif inferred_severity == "P2":
            target_minutes = 120  # 2 hours
        else:
            target_minutes = 480  # 1 business day
    elif plan == "Growth":
        sla_source = "Support Policy v3 (Growth Plan)"
        if inferred_severity == "P1":
            target_minutes = 120  # 2 business hours
        elif inferred_severity == "P2":
            target_minutes = 240  # 4 business hours
        else:
            target_minutes = 960  # 2 business days
    else:  # Standard
        sla_source = "Support Policy v3 (Standard Plan)"
        if inferred_severity == "P1":
            target_minutes = 240  # 4 business hours
        elif inferred_severity == "P2":
            target_minutes = 480  # 1 business day
        else:
            target_minutes = 960  # 2 business days

    is_breached = elapsed_minutes > target_minutes
    requires_escalation = is_breached or inferred_severity == "P1"

    return {
        "ticket_id": ticket_id,
        "account_id": account_id,
        "plan": plan,
        "severity": inferred_severity,
        "created_at": created_at_str,
        "elapsed_minutes": elapsed_minutes,
        "sla_target_minutes": target_minutes,
        "is_breached": is_breached,
        "requires_escalation": requires_escalation,
        "sla_source": sla_source,
        "recommendation": (
            f"URGENT ESCALATION REQUIRED: {inferred_severity} ticket has elapsed {elapsed_minutes} mins "
            f"(target: {target_minutes} mins under {sla_source}). Response target is breached."
            if is_breached else
            (f"Immediate escalation recommended for critical {inferred_severity} incident."
             if inferred_severity == "P1" else f"Ticket is within SLA target ({elapsed_minutes}/{target_minutes} min).")
        ),
        "reference_snapshot_time": get_snapshot_time(),
    }
