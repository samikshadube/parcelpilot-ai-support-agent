"""Data models, auth context, and type definitions for ParcelPilot AI."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class Role(str, Enum):
    CUSTOMER = "customer"
    INTERNAL = "internal"


@dataclass
class UserContext:
    """Authenticated user context injected by the harness into all tool invocations.

    The LLM never supplies or manipulates this object directly.
    """
    role: Literal["customer", "internal"]
    account_id: Optional[str] = None
    internal_role: Optional[str] = None  # e.g., "agent", "support_lead", "ops_manager"
    user_name: Optional[str] = None

    def is_internal(self) -> bool:
        return self.role == Role.INTERNAL or self.role == "internal"

    def is_customer(self) -> bool:
        return self.role == Role.CUSTOMER or self.role == "customer"

    def can_access_account(self, target_account_id: Optional[str]) -> bool:
        if self.is_internal():
            return True
        if not target_account_id or not self.account_id:
            return False
        return self.account_id.strip().upper() == target_account_id.strip().upper()


class AuthorityTier(IntEnum):
    """Source authority hierarchy (1 = highest priority)."""
    CUSTOMER_AGREEMENT = 1      # Account-specific agreement (overrides general policy for that account)
    CURRENT_SOP_POLICY = 2      # Current standard operating procedure or support policy
    PRODUCT_OPS_GUIDE = 3       # Operational facts and known issues
    DEPRECATED_POLICY = 4       # Deprecated policy (never sole authority, labeled deprecated)
    HISTORICAL_TICKET = 5       # Historical ticket resolutions (unreliable context only)


class DocStatus(str, Enum):
    CURRENT = "current"
    DEPRECATED = "deprecated"
    ACTIVE = "active"


class DocMetadata(BaseModel):
    source_file: str
    doc_title: str
    doc_type: str
    version: str
    status: str
    customer_scope: Optional[str] = None  # None for general, account_id for customer agreement
    authority_tier: int = AuthorityTier.CURRENT_SOP_POLICY


class SearchResult(BaseModel):
    chunk_id: str
    content: str
    metadata: DocMetadata
    similarity_score: float
    is_accessible: bool = True
    authority_name: str = ""


class ActionType(str, Enum):
    CREATE_ESCALATION = "create_escalation"
    UPDATE_TICKET = "update_ticket"
    CREATE_FOLLOWUP_TASK = "create_followup_task"
    APPROVE_SERVICE_CREDIT = "approve_service_credit"
    REQUEST_ORDER_CANCELLATION = "request_order_cancellation"


class ActionStatus(str, Enum):
    PENDING_CONFIRMATION = "pending_confirmation"
    EXECUTED = "executed"
    REJECTED = "rejected"
    FAILED = "failed"


class StagedAction(BaseModel):
    action_id: str
    action_type: str
    account_id: Optional[str] = None
    target_entity_type: str  # "ticket", "order", "account"
    target_entity_id: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    status: str = ActionStatus.PENDING_CONFIRMATION
    staged_by_role: str
    staged_by_user: Optional[str] = None
    staged_at: str
    executed_at: Optional[str] = None
    execution_result: Optional[str] = None
    requires_manager_approval: bool = False


class ToolTrace(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]
    result: Any
    is_error: bool = False
    execution_time_ms: float = 0.0


class SourceCitation(BaseModel):
    source_file: str
    authority_tier: int
    authority_label: str
    status: str
    customer_scope: Optional[str] = None
    summary: str
    is_governing: bool = False


class TokenUsage(BaseModel):
    provider: str
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    token_limit: Optional[int] = None
    remaining_tokens: Optional[int] = None
    status: str = "success"  # "success", "rate_limited", "failed", "deterministic"
    error_message: Optional[str] = None


class AgentResponse(BaseModel):
    answer: str
    citations: List[SourceCitation] = Field(default_factory=list)
    tool_traces: List[ToolTrace] = Field(default_factory=list)
    token_usages: List[TokenUsage] = Field(default_factory=list)
    staged_action: Optional[StagedAction] = None
    conflict_detected: bool = False
    conflict_details: Optional[str] = None
    confidence_level: str = "high"  # "high", "medium", "escalation_recommended"
    requires_escalation: bool = False
    handled_by: str = "deterministic"  # "groq", "nvidia_nim", or "deterministic"

