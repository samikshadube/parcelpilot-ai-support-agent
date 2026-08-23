"""Tool registry and dispatcher for Claude tool-calling API with ctx injection."""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from src.models import ToolTrace, UserContext
from src.tools.action_tools import execute_action, list_staged_actions, propose_action
from src.tools.query_structured_data import (
    calculate_cancellation_terms,
    calculate_service_credit,
    calculate_ticket_sla,
    get_account_details,
    get_order_details,
    get_ticket_details,
    list_orders,
    list_tickets,
)
from src.tools.search_documents import search_documents


# Anthropic tool schemas
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "search_documents",
        "description": (
            "Search knowledge base documents including general policies, standard operating procedures (SOPs), "
            "customer agreements, and product operations guides. "
            "Returns document chunks with authority tiers and metadata."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to match against policies, agreements, or product documentation.",
                },
                "doc_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of document types to filter: 'customer_agreement', 'sop', 'support_policy', 'product_operations', 'deprecated_policy'.",
                },
                "n_results": {
                    "type": "integer",
                    "description": "Number of relevant chunks to retrieve (default: 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_account_details",
        "description": "Look up account profile, plan tier, dedicated CSM, and customer agreement contract filename.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Account ID to retrieve (optional for customers, required for internal looking up specific accounts).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_order_details",
        "description": "Retrieve comprehensive details for an order (status, carrier, booked time, pickup window, actual pickup, carrier/customer fault flags, fees).",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The Order identifier (e.g. ORD-xxxx).",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "list_orders",
        "description": "List orders with optional filtering by status (e.g. BOOKED, PICKED_UP, DELIVERED) and carrier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Account ID filter (internal users only).",
                },
                "status": {
                    "type": "string",
                    "description": "Order status filter (e.g. 'BOOKED', 'PICKED_UP', 'DELIVERED').",
                },
                "carrier": {
                    "type": "string",
                    "description": "Carrier name filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max orders to return (default: 10).",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_ticket_details",
        "description": "Retrieve support ticket details, status, timestamps, subject, description, and historical resolution notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "The Support Ticket identifier (e.g. TKT-xxxx).",
                }
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "list_tickets",
        "description": "List support tickets with optional filtering by status (open, closed) and assigned staff.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Account ID filter (internal users only).",
                },
                "status": {
                    "type": "string",
                    "description": "Ticket status ('open', 'closed').",
                },
                "assigned_to": {
                    "type": "string",
                    "description": "Assigned support agent name.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max tickets to return (default: 10).",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    {
        "name": "calculate_cancellation_terms",
        "description": (
            "Evaluate whether an order can be cancelled and what cancellation fee applies. "
            "Automatically evaluates order status, booking elapsed time relative to reference snapshot time, "
            "standard SOP v4 rules, and any customer enterprise agreement fee-waiver overrides."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The Order ID to calculate cancellation terms for.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "calculate_service_credit",
        "description": (
            "Calculate eligibility and amount for failed-pickup service credits. "
            "Evaluates pickup window delay against snapshot reference time, carrier fault, customer fault, "
            "and customer-specific agreement overrides (e.g. custom agreement fixed INR 300 vs default SOP min(500, 10%))."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The Order ID to calculate service credit for.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "calculate_ticket_sla",
        "description": (
            "Calculate SLA response targets, elapsed time, and breach status for a support ticket "
            "relative to dataset snapshot reference time. Recommends escalation if SLA is breached or P1."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "The Ticket ID to check SLA status for.",
                },
                "severity": {
                    "type": "string",
                    "description": "Optional severity override ('P1', 'P2', 'P3'). If omitted, inferred from ticket details.",
                },
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "propose_action",
        "description": (
            "Stage a state-changing action (e.g., ticket escalation, order cancellation, service credit approval, "
            "or creating a follow-up task). Does NOT execute the action; returns an action_id and confirmation prompt "
            "requiring explicit user confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": [
                        "create_escalation",
                        "update_ticket",
                        "create_followup_task",
                        "approve_service_credit",
                        "request_order_cancellation",
                    ],
                    "description": "The type of action to stage.",
                },
                "target_entity_type": {
                    "type": "string",
                    "enum": ["ticket", "order", "account"],
                    "description": "The type of entity targeted.",
                },
                "target_entity_id": {
                    "type": "string",
                    "description": "The ID of the target record (e.g., ticket ID or order ID).",
                },
                "description": {
                    "type": "string",
                    "description": "Clear justification and description of the action being proposed.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Optional parameters (e.g., priority, credit_amount_inr, status, notes).",
                },
            },
            "required": ["action_type", "target_entity_type", "target_entity_id", "description"],
        },
    },
    {
        "name": "execute_action",
        "description": (
            "Execute a previously staged action by action_id ONLY after the user has explicitly confirmed it. "
            "Re-validates access permissions before applying any database modification."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": "The unique action ID returned by propose_action (e.g. ACT-xxxx).",
                }
            },
            "required": ["action_id"],
        },
    },
]


TOOL_FUNCTION_MAP: Dict[str, Callable] = {
    "search_documents": search_documents,
    "get_account_details": get_account_details,
    "get_order_details": get_order_details,
    "list_orders": list_orders,
    "get_ticket_details": get_ticket_details,
    "list_tickets": list_tickets,
    "calculate_cancellation_terms": calculate_cancellation_terms,
    "calculate_service_credit": calculate_service_credit,
    "calculate_ticket_sla": calculate_ticket_sla,
    "propose_action": propose_action,
    "execute_action": execute_action,
}


def dispatch_tool_call(ctx: UserContext, tool_name: str, arguments: Dict[str, Any]) -> Tuple[Any, ToolTrace]:
    """Execute a registered tool function with injected UserContext."""
    fn = TOOL_FUNCTION_MAP.get(tool_name)
    if not fn:
        error_result = {"error": "UnknownTool", "message": f"Tool '{tool_name}' is not registered."}
        trace = ToolTrace(
            tool_name=tool_name,
            arguments=arguments,
            result=error_result,
            is_error=True,
            execution_time_ms=0.0,
        )
        return error_result, trace

    start_time = time.perf_counter()
    try:
        # Inject ctx as first argument
        result = fn(ctx, **arguments)
        is_error = isinstance(result, dict) and "error" in result
    except Exception as e:
        result = {"error": "ExecutionException", "message": str(e)}
        is_error = True

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    trace = ToolTrace(
        tool_name=tool_name,
        arguments=arguments,
        result=result,
        is_error=is_error,
        execution_time_ms=round(elapsed_ms, 2),
    )
    return result, trace



