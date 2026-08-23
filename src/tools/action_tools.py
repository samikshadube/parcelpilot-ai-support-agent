"""State-changing action tools with two-phase commit: propose_action -> user confirm -> execute_action."""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from src.db import get_db_connection, get_snapshot_datetime, get_snapshot_time
from src.models import ActionStatus, ActionType, UserContext


def _resolve_entity_account(target_entity_type: str, target_entity_id: str) -> Optional[str]:
    """Look up the owning account_id for a given entity."""
    conn = get_db_connection()
    cursor = conn.cursor()
    account_id = None

    if target_entity_type.lower() == "ticket":
        cursor.execute("SELECT account_id FROM tickets WHERE ticket_id = ?", (target_entity_id.strip(),))
        row = cursor.fetchone()
        if row:
            account_id = row["account_id"]
    elif target_entity_type.lower() == "order":
        cursor.execute("SELECT account_id FROM orders WHERE order_id = ?", (target_entity_id.strip(),))
        row = cursor.fetchone()
        if row:
            account_id = row["account_id"]
    elif target_entity_type.lower() == "account":
        cursor.execute("SELECT account_id FROM accounts WHERE account_id = ?", (target_entity_id.strip(),))
        row = cursor.fetchone()
        if row:
            account_id = row["account_id"]

    conn.close()
    return account_id


def propose_action(
    ctx: UserContext,
    action_type: str,
    target_entity_type: str,
    target_entity_id: str,
    description: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Stage a state-changing action for explicit user confirmation. Does NOT execute the action.

    Access Control:
    - Customer users can ONLY stage actions for entities owned by their account.
    - Internal users can stage actions for any entity.

    Args:
        ctx: Injected authenticated user context.
        action_type: Type of action (create_escalation, update_ticket, create_followup_task,
                     approve_service_credit, request_order_cancellation).
        target_entity_type: Type of entity ('ticket', 'order', 'account').
        target_entity_id: Identifier of the target record.
        description: Human-readable explanation of why this action is being taken.
        parameters: Additional parameters for the action (e.g. priority, notes, credit amount).

    Returns:
        Confirmation payload with a unique action_id and description of staged changes.
    """
    if not action_type or not target_entity_type or not target_entity_id:
        return {"error": "BadRequest", "message": "action_type, target_entity_type, and target_entity_id are required."}

    # Verify entity exists and resolve account
    entity_account_id = _resolve_entity_account(target_entity_type, target_entity_id)
    if not entity_account_id:
        return {
            "error": "NotFound",
            "message": f"{target_entity_type.capitalize()} with ID '{target_entity_id}' not found.",
        }

    # Access control verification
    if ctx.is_customer() and not ctx.can_access_account(entity_account_id):
        return {
            "error": "AccessDenied",
            "message": f"Customer {ctx.account_id} cannot perform actions on {target_entity_type} {target_entity_id}.",
        }

    params = parameters or {}
    action_id = f"ACT-{uuid.uuid4().hex[:8].upper()}"
    staged_at = get_snapshot_time()

    # Check for approval requirements
    requires_manager_approval = False
    if action_type == ActionType.APPROVE_SERVICE_CREDIT.value:
        credit_amt = float(params.get("credit_amount_inr", 0.0))
        if credit_amt > 1000.0:
            requires_manager_approval = True

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO staged_actions
        (action_id, action_type, account_id, target_entity_type, target_entity_id,
         description, parameters_json, status, staged_by_role, staged_by_user,
         staged_at, requires_manager_approval)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        action_id,
        action_type,
        entity_account_id,
        target_entity_type,
        target_entity_id,
        description,
        json.dumps(params),
        ActionStatus.PENDING_CONFIRMATION.value,
        ctx.role,
        ctx.user_name or ctx.internal_role or ctx.account_id,
        staged_at,
        1 if requires_manager_approval else 0,
    ))
    conn.commit()
    conn.close()

    return {
        "status": "staged_pending_confirmation",
        "action_id": action_id,
        "action_type": action_type,
        "target_entity": f"{target_entity_type}:{target_entity_id}",
        "account_id": entity_account_id,
        "description": description,
        "parameters": params,
        "requires_manager_approval": requires_manager_approval,
        "confirmation_prompt": (
            f"Action Staged: {action_type.replace('_', ' ').title()} for {target_entity_type.upper()} {target_entity_id}. "
            f"Description: '{description}'. "
            f"Do you want to confirm and execute this action? (Please respond with Yes to confirm)."
        ),
    }


def execute_action(ctx: UserContext, action_id: str) -> Dict[str, Any]:
    """Execute a previously staged action following explicit user confirmation.

    Access Control:
    - Re-validates that the current ctx has authorization for the staged entity's account.
    - Prevents double execution or executing out-of-scope actions.
    """
    if not action_id:
        return {"error": "BadRequest", "message": "action_id is required."}

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM staged_actions WHERE action_id = ?", (action_id.strip(),))
    action_row = cursor.fetchone()

    if not action_row:
        conn.close()
        return {"error": "NotFound", "message": f"Staged action '{action_id}' not found."}

    if action_row["status"] != ActionStatus.PENDING_CONFIRMATION.value:
        conn.close()
        return {
            "error": "InvalidState",
            "message": f"Action '{action_id}' cannot be executed because its status is '{action_row['status']}'.",
        }

    # Re-validate access scope at execution time
    target_account = action_row["account_id"]
    if ctx.is_customer() and not ctx.can_access_account(target_account):
        conn.close()
        return {
            "error": "AccessDenied",
            "message": f"Customer {ctx.account_id} is not authorized to execute action for account {target_account}.",
        }

    action_type = action_row["action_type"]
    target_type = action_row["target_entity_type"]
    target_id = action_row["target_entity_id"]
    params = json.loads(action_row["parameters_json"])
    now_str = get_snapshot_time()

    execution_msg = ""

    # Perform the state change
    try:
        if action_type == ActionType.CREATE_ESCALATION.value or action_type == "create_escalation":
            cursor.execute("""
                UPDATE tickets
                SET status = 'escalated',
                    assigned_to = 'Tier 2 Support Lead',
                    historical_resolution = 'Escalated by ParcelPilot AI agent'
                WHERE ticket_id = ?
            """, (target_id,))
            execution_msg = f"Ticket {target_id} has been escalated to Tier 2 Support Lead."

        elif action_type == ActionType.UPDATE_TICKET.value or action_type == "update_ticket":
            new_status = params.get("status", "in_progress")
            notes = params.get("notes", "")
            cursor.execute("""
                UPDATE tickets
                SET status = ?,
                    historical_resolution = CASE WHEN ? != '' THEN ? ELSE historical_resolution END
                WHERE ticket_id = ?
            """, (new_status, notes, notes, target_id))
            execution_msg = f"Ticket {target_id} updated with status='{new_status}'."

        elif action_type == ActionType.REQUEST_ORDER_CANCELLATION.value or action_type == "request_order_cancellation":
            cursor.execute("""
                UPDATE orders
                SET status = 'CANCELLED',
                    cancellation_requested_at = ?
                WHERE order_id = ?
            """, (now_str, target_id))
            execution_msg = f"Order {target_id} cancellation successfully processed (Status set to CANCELLED)."

        elif action_type == ActionType.APPROVE_SERVICE_CREDIT.value or action_type == "approve_service_credit":
            credit_amt = params.get("credit_amount_inr", 0.0)
            cursor.execute("""
                UPDATE orders
                SET notes = CASE WHEN notes IS NULL THEN ? ELSE notes || ' | ' || ? END
                WHERE order_id = ?
            """, (f"Service credit approved: INR {credit_amt}", f"Service credit approved: INR {credit_amt}", target_id))
            execution_msg = f"Service credit of INR {credit_amt} recorded for Order {target_id}."

        elif action_type == ActionType.CREATE_FOLLOWUP_TASK.value or action_type == "create_followup_task":
            execution_msg = f"Follow-up task created for {target_type} {target_id}: {action_row['description']}."

        else:
            execution_msg = f"Action {action_type} executed successfully on {target_type} {target_id}."

        # Mark action as executed in staged_actions
        cursor.execute("""
            UPDATE staged_actions
            SET status = ?,
                executed_at = ?,
                execution_result = ?
            WHERE action_id = ?
        """, (ActionStatus.EXECUTED.value, now_str, execution_msg, action_id))

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return {"error": "ExecutionFailed", "message": f"Failed to execute action: {str(e)}"}

    conn.close()
    return {
        "status": "executed",
        "action_id": action_id,
        "action_type": action_type,
        "target_entity": f"{target_type}:{target_id}",
        "execution_result": execution_msg,
        "executed_at": now_str,
    }


def list_staged_actions(ctx: UserContext, status: Optional[str] = None) -> Dict[str, Any]:
    """List staged actions scoped to the caller's account or role."""
    conn = get_db_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM staged_actions WHERE 1=1"
    params: List[Any] = []

    if ctx.is_customer() and ctx.account_id:
        query += " AND account_id = ?"
        params.append(ctx.account_id)

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY staged_at DESC LIMIT 20"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    actions = [dict(r) for r in rows]
    return {
        "caller_role": ctx.role,
        "caller_account_id": ctx.account_id,
        "actions": actions,
    }
