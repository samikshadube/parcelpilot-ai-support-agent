"""Agent execution loop supporting multi-provider LLM fallback and deterministic offline reasoning."""

import logging
import os
import json
import re
from typing import Any, Dict, List, Optional
import openai
from src.agent.llm_providers import LLMProviderConfig, circuit_breaker, get_active_providers
from src.agent.prompts import get_system_prompt
from src.authority import AuthorityEngine
from src.config import DEFAULT_ANTHROPIC_MODEL, DEFAULT_GROQ_MODEL
from src.models import (
    ActionStatus,
    AgentResponse,
    SourceCitation,
    StagedAction,
    TokenUsage,
    ToolTrace,
    UserContext,
)
from src.tools.registry import TOOL_SCHEMAS, dispatch_tool_call

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Executes the multi-provider agentic reasoning and tool-calling loop."""

    def __init__(self, model: Optional[str] = None):
        self.override_model = model

    def run(
        self,
        ctx: UserContext,
        query: str,
        chat_history: Optional[List[Dict[str, str]]] = None,
        max_iterations: int = 8,
    ) -> AgentResponse:
        """Run agent loop across active LLM provider chain, falling through to offline deterministic mode."""
        active_providers = get_active_providers()
        accumulated_token_usages: List[TokenUsage] = []

        for cfg, api_key, model_str in active_providers:
            model = self.override_model if self.override_model else model_str
            logger.info(f"[LLM] Attempting provider '{cfg.display_name}' (model: {model})...")
            try:
                response = self._run_provider_loop(cfg, api_key, model, ctx, query, chat_history, max_iterations)
                if response.answer == "Agent reached maximum tool iterations.":
                    raise ValueError("Agent reached maximum tool iterations.")
                circuit_breaker.record_success(cfg.name)
                response.handled_by = cfg.name
                response.token_usages = accumulated_token_usages + response.token_usages
                return response
            except (
                openai.RateLimitError,
                openai.AuthenticationError,
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.APIError,
                ValueError,
                Exception,
            ) as err:
                err_msg = str(err)
                logger.warning(
                    f"[LLM] {cfg.display_name} failed ({type(err).__name__}: {err_msg}) — attempting next fallback provider."
                )
                circuit_breaker.record_failure(cfg.name)

                # Parse token limit / requested tokens from error message if available
                limit_match = re.search(r"Limit\s*(\d+)", err_msg, re.IGNORECASE)
                used_match = re.search(r"Used\s*(\d+)", err_msg, re.IGNORECASE)
                req_match = re.search(r"Requested\s*(\d+)", err_msg, re.IGNORECASE)

                t_limit = int(limit_match.group(1)) if limit_match else None
                t_used = int(used_match.group(1)) if used_match else None
                t_req = int(req_match.group(1)) if req_match else None
                t_rem = (t_limit - t_used) if (t_limit is not None and t_used is not None) else None

                accumulated_token_usages.append(
                    TokenUsage(
                        provider=cfg.display_name,
                        model=model,
                        prompt_tokens=t_req,
                        completion_tokens=None,
                        total_tokens=t_req,
                        token_limit=t_limit,
                        remaining_tokens=t_rem,
                        status="rate_limited" if "429" in err_msg or "rate" in err_msg.lower() else "failed",
                        error_message=err_msg[:120],
                    )
                )
                continue

        # If all LLM providers in chain failed or none configured, fall through to deterministic mode
        fallback = self._run_deterministic_reasoner(ctx, query, chat_history)
        fallback.handled_by = "deterministic"
        fallback.token_usages = accumulated_token_usages
        return fallback

    def _run_provider_loop(
        self,
        cfg: LLMProviderConfig,
        api_key: str,
        model: str,
        ctx: UserContext,
        query: str,
        chat_history: Optional[List[Dict[str, str]]] = None,
        max_iterations: int = 8,
    ) -> AgentResponse:
        """Run agent tool-calling loop for a specific OpenAI-compatible provider."""
        if cfg.name == "groq":
            try:
                from groq import Groq
                client = Groq(api_key=api_key)
            except ImportError:
                client = openai.OpenAI(api_key=api_key, base_url=cfg.base_url)
        else:
            client = openai.OpenAI(api_key=api_key, base_url=cfg.base_url)

        system_prompt = get_system_prompt(ctx)

        openai_tools = []
        for schema in TOOL_SCHEMAS:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema["description"],
                    "parameters": schema["input_schema"],
                },
            })

        valid_tool_names = {schema["name"] for schema in TOOL_SCHEMAS}

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if chat_history:
            for msg in chat_history[-6:]:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": query})

        tool_traces: List[ToolTrace] = []
        citations: List[SourceCitation] = []
        staged_action: Optional[StagedAction] = None

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_total_tokens = 0

        for _ in range(max_iterations):
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
                temperature=0.1,
            )

            # Accumulate token usage from API response
            usage = getattr(response, "usage", None)
            if usage:
                p_tok = getattr(usage, "prompt_tokens", None)
                c_tok = getattr(usage, "completion_tokens", None)
                t_tok = getattr(usage, "total_tokens", None)
                if p_tok is not None:
                    total_prompt_tokens += p_tok
                if c_tok is not None:
                    total_completion_tokens += c_tok
                if t_tok is not None:
                    total_total_tokens += t_tok

            choice = response.choices[0]
            message = choice.message
            tool_calls = getattr(message, "tool_calls", None)

            if not tool_calls or choice.finish_reason != "tool_calls":
                final_text = message.content or ""
                retrieved_chunks = []
                for trace in tool_traces:
                    if trace.tool_name == "search_documents" and isinstance(trace.result, dict):
                        retrieved_chunks.extend(trace.result.get("results", []))
                if retrieved_chunks:
                    citations = AuthorityEngine.build_citations(retrieved_chunks)

                usage_record = TokenUsage(
                    provider=cfg.display_name,
                    model=model,
                    prompt_tokens=total_prompt_tokens if total_prompt_tokens > 0 else None,
                    completion_tokens=total_completion_tokens if total_completion_tokens > 0 else None,
                    total_tokens=total_total_tokens if total_total_tokens > 0 else None,
                    status="success",
                )

                return AgentResponse(
                    answer=final_text,
                    citations=citations,
                    tool_traces=tool_traces,
                    token_usages=[usage_record],
                    staged_action=staged_action,
                    confidence_level="high",
                    handled_by=cfg.name,
                )


            # Validate returned tool calls against registry before execution
            for call in tool_calls:
                tool_name = call.function.name
                if tool_name not in valid_tool_names:
                    raise ValueError(
                        f"Provider '{cfg.display_name}' returned invalid tool call '{tool_name}' not in registry."
                    )

            # Append assistant message with tool calls
            messages.append(message)

            for call in tool_calls:
                tool_name = call.function.name
                try:
                    tool_input = json.loads(call.function.arguments) if call.function.arguments else {}
                except Exception as parse_err:
                    raise ValueError(
                        f"Provider '{cfg.display_name}' returned unparseable tool call arguments: {parse_err}"
                    )

                tool_id = call.id
                result, trace = dispatch_tool_call(ctx, tool_name, tool_input)
                tool_traces.append(trace)

                if tool_name == "propose_action" and isinstance(result, dict) and "action_id" in result:
                    staged_action = StagedAction(
                        action_id=result["action_id"],
                        action_type=result.get("action_type", ""),
                        account_id=result.get("account_id"),
                        target_entity_type=tool_input.get("target_entity_type", "entity"),
                        target_entity_id=tool_input.get("target_entity_id", ""),
                        description=result.get("description", ""),
                        parameters=tool_input.get("parameters", {}),
                        status=ActionStatus.PENDING_CONFIRMATION.value,
                        staged_by_role=ctx.role,
                        staged_at=result.get("status", ""),
                        requires_manager_approval=result.get("requires_manager_approval", False),
                    )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": json.dumps(result, default=str),
                })

        return AgentResponse(
            answer="Agent reached maximum tool iterations.",
            tool_traces=tool_traces,
            citations=citations,
            staged_action=staged_action,
            handled_by=cfg.name,
        )


    def _run_deterministic_reasoner(

        self,
        ctx: UserContext,
        query: str,
        chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> AgentResponse:
        """Deterministic multi-step reasoning engine for offline testing and verification."""
        lower_q = query.lower()
        tool_traces: List[ToolTrace] = []
        citations: List[SourceCitation] = []
        staged_action: Optional[StagedAction] = None

        # 1. Action Confirmation Trigger
        confirm_match = re.search(r"\b(yes|confirm|execute|proceed|approve)\b", lower_q)
        action_id_match = re.search(r"act-[0-9a-f]{8}", lower_q, re.IGNORECASE)
        if confirm_match:
            # Check for recent staged action in chat history or query
            act_id = action_id_match.group(0).upper() if action_id_match else None
            if not act_id and chat_history:
                for h in reversed(chat_history):
                    m = re.search(r"ACT-[0-9A-F]{8}", h.get("content", ""))
                    if m:
                        act_id = m.group(0)
                        break

            if act_id:
                exec_res, trace = dispatch_tool_call(ctx, "execute_action", {"action_id": act_id})
                tool_traces.append(trace)
                if "error" in exec_res:
                    return AgentResponse(
                        answer=f"Could not execute action: {exec_res.get('message')}",
                        tool_traces=tool_traces,
                    )
                return AgentResponse(
                    answer=(
                        f"**Action Confirmed & Executed**\n\n"
                        f"- **Action ID**: `{exec_res.get('action_id')}`\n"
                        f"- **Entity**: `{exec_res.get('target_entity')}`\n"
                        f"- **Result**: {exec_res.get('execution_result')}\n"
                        f"- **Timestamp**: {exec_res.get('executed_at')}"
                    ),
                    tool_traces=tool_traces,
                )

        # 2. Extract entity IDs (order_id e.g. ORD-xxxx, ticket_id e.g. TKT-xxxx)
        order_match = re.search(r"ord-\d+", lower_q, re.IGNORECASE)
        ticket_match = re.search(r"tkt-\d+", lower_q, re.IGNORECASE)
        target_order = order_match.group(0).upper() if order_match else None
        target_ticket = ticket_match.group(0).upper() if ticket_match else None

        # 3. Order Cancellation Evaluation
        if target_order and ("cancel" in lower_q or "cancellation" in lower_q or "fee" in lower_q):
            # Fetch order details
            order_res, t1 = dispatch_tool_call(ctx, "get_order_details", {"order_id": target_order})
            tool_traces.append(t1)

            if "error" in order_res:
                return AgentResponse(
                    answer=f"Unable to access order {target_order}: {order_res.get('message')}",
                    tool_traces=tool_traces,
                )

            # Search policies & agreements
            search_res, t2 = dispatch_tool_call(ctx, "search_documents", {"query": "order cancellation fee policy exception window"})
            tool_traces.append(t2)

            # Calculate terms
            calc_res, t3 = dispatch_tool_call(ctx, "calculate_cancellation_terms", {"order_id": target_order})
            tool_traces.append(t3)

            retrieved_chunks = search_res.get("results", [])
            citations = AuthorityEngine.build_citations(retrieved_chunks)

            override_applied = calc_res.get("agreement_override_applied", False)
            fee = calc_res.get("governing_fee_inr", 0.0)
            can_cancel = calc_res.get("can_cancel", False)

            lines = [
                f"### Cancellation Evaluation for Order `{target_order}`\n",
                f"- **Account**: `{calc_res.get('account_id')}`",
                f"- **Current Status**: `{calc_res.get('order_status')}`",
                f"- **Elapsed Time Since Booking**: {calc_res.get('elapsed_minutes_since_booking')} minutes",
                f"- **Governing Authority**: {calc_res.get('governing_policy')}",
                f"- **Cancellation Permitted**: {'Yes' if can_cancel else 'No'}",
                f"- **Applicable Cancellation Fee**: **INR {fee:.2f}**\n",
                f"**Reasoning & Policy Hierarchy:**",
                calc_res.get("explanation", ""),
            ]

            if override_applied:
                lines.append(
                    "\n> **Authority Precedence Note**: Under ParcelPilot Source Authority Hierarchy (Tier 1), "
                    "the signed customer enterprise agreement strictly supersedes the standard SOP v4 rule."
                )

            return AgentResponse(
                answer="\n".join(lines),
                citations=citations,
                tool_traces=tool_traces,
                confidence_level="high",
            )

        # 4. Escalation / State-Changing Action Request
        if "escalat" in lower_q or "create escalation" in lower_q:
            if not target_ticket:
                # Find most recent open ticket for context
                tickets_res, t0 = dispatch_tool_call(ctx, "list_tickets", {"status": "open", "limit": 1})
                tool_traces.append(t0)
                t_list = tickets_res.get("tickets", [])
                if t_list:
                    target_ticket = t_list[0].get("ticket_id")

            if target_ticket:
                prop_res, t1 = dispatch_tool_call(ctx, "propose_action", {
                    "action_type": "create_escalation",
                    "target_entity_type": "ticket",
                    "target_entity_id": target_ticket,
                    "description": f"Escalating ticket {target_ticket} due to SLA breach / user request.",
                })
                tool_traces.append(t1)

                if "error" in prop_res:
                    return AgentResponse(
                        answer=f"Could not propose escalation: {prop_res.get('message')}",
                        tool_traces=tool_traces,
                    )

                staged_action = StagedAction(
                    action_id=prop_res["action_id"],
                    action_type="create_escalation",
                    account_id=prop_res.get("account_id"),
                    target_entity_type="ticket",
                    target_entity_id=target_ticket,
                    description=prop_res.get("description", ""),
                    status=ActionStatus.PENDING_CONFIRMATION.value,
                    staged_by_role=ctx.role,
                    staged_at=prop_res.get("status", ""),
                )

                return AgentResponse(
                    answer=(
                        f"### Action Staged for Confirmation\n\n"
                        f"I have prepared an escalation for Ticket `{target_ticket}`.\n\n"
                        f"- **Action ID**: `{prop_res.get('action_id')}`\n"
                        f"- **Target**: `{prop_res.get('target_entity')}`\n"
                        f"- **Status**: `Pending Confirmation`\n\n"
                        f"**Confirmation Prompt**: {prop_res.get('confirmation_prompt')}"
                    ),
                    tool_traces=tool_traces,
                    staged_action=staged_action,
                )

        # 5. Service Credit Evaluation
        if (target_order or re.search(r"\b(credit|service credit|late|delay)\b", lower_q)):
            # If no target order explicitly specified, inspect available orders
            if not target_order:
                orders_res, t0 = dispatch_tool_call(ctx, "list_orders", {"limit": 5})
                tool_traces.append(t0)
                orders_list = orders_res.get("orders", [])
                if orders_list:
                    # Pick first eligible or candidate order
                    target_order = orders_list[0].get("order_id")

            if target_order:
                order_res, t1 = dispatch_tool_call(ctx, "get_order_details", {"order_id": target_order})
                tool_traces.append(t1)

                if "error" in order_res:
                    return AgentResponse(
                        answer=f"Unable to access order {target_order}: {order_res.get('message')}",
                        tool_traces=tool_traces,
                    )

                search_res, t2 = dispatch_tool_call(ctx, "search_documents", {"query": "failed pickup service credit carrier fault delay threshold"})
                tool_traces.append(t2)

                calc_res, t3 = dispatch_tool_call(ctx, "calculate_service_credit", {"order_id": target_order})
                tool_traces.append(t3)

                citations = AuthorityEngine.build_citations(search_res.get("results", []))

                is_eligible = calc_res.get("is_eligible", False)
                amt = calc_res.get("credit_amount_inr", 0.0)
                delay_h = calc_res.get("delay_hours", 0.0)

                lines = [
                    f"### Service Credit Assessment for Order `{target_order}`\n",
                    f"- **Account**: `{calc_res.get('account_id')}`",
                    f"- **Pickup Window End**: {calc_res.get('pickup_window_end')}",
                    f"- **Calculated Delay**: {delay_h:.1f} hours past scheduled window",
                    f"- **Carrier Fault**: {'Yes' if calc_res.get('carrier_fault') else 'No'}",
                    f"- **Customer Fault**: {'Yes' if calc_res.get('customer_fault') else 'No'}",
                    f"- **Service Credit Eligible**: **{'YES' if is_eligible else 'NO'}**",
                    f"- **Calculated Credit Amount**: **INR {amt:.2f}**",
                    f"- **Governing Rule**: {calc_res.get('governing_policy')}\n",
                    f"**Explanation:**\n{calc_res.get('explanation')}",
                ]
                return AgentResponse(
                    answer="\n".join(lines),
                    citations=citations,
                    tool_traces=tool_traces,
                    confidence_level="high",
                )
        # 6. Ticket / SLA Investigation
        if target_ticket or "ticket" in lower_q or "sla" in lower_q or "outage" in lower_q:
            if not target_ticket:
                tickets_res, t0 = dispatch_tool_call(ctx, "list_tickets", {"status": "open", "limit": 1})
                tool_traces.append(t0)
                t_list = tickets_res.get("tickets", [])
                if t_list:
                    target_ticket = t_list[0].get("ticket_id")

            if target_ticket:
                ticket_res, t1 = dispatch_tool_call(ctx, "get_ticket_details", {"ticket_id": target_ticket})
                tool_traces.append(t1)

                if "error" in ticket_res:
                    return AgentResponse(
                        answer=f"Unable to access ticket {target_ticket}: {ticket_res.get('message')}",
                        tool_traces=tool_traces,
                    )

                search_res, t2 = dispatch_tool_call(ctx, "search_documents", {"query": f"{ticket_res.get('subject')} {ticket_res.get('description')}"})
                tool_traces.append(t2)

                sla_res, t3 = dispatch_tool_call(ctx, "calculate_ticket_sla", {"ticket_id": target_ticket})
                tool_traces.append(t3)

                citations = AuthorityEngine.build_citations(search_res.get("results", []))

                is_breached = sla_res.get("is_breached", False)
                severity = sla_res.get("severity", "P3")

                subj_desc = f"{ticket_res.get('subject', '')} {ticket_res.get('description', '')}".lower()
                results = search_res.get("results", [])

                investigation_notes = []
                if "swiftship" in subj_desc or "booked after driver pickup" in subj_desc or "driver" in subj_desc:
                    investigation_notes.append(
                        "**Root Cause / Operational Insight**: Correlates with **Known Issue KI-211 (SwiftShip Pickup Webhook Delay)**.\n"
                        "SwiftShip pickup confirmation webhooks can experience delays of up to 20 minutes. "
                        "When a parcel is physically collected by the driver, ParcelPilot may temporarily remain in `BOOKED` status until the webhook arrives.\n\n"
                        "**Internal Support Action Steps**:\n"
                        "1. Verify driver manifest or carrier portal directly to confirm physical pickup.\n"
                        "2. Allow the known 20-minute webhook delay window to elapse before assuming the pickup failed or advising the customer that pickup did not occur.\n"
                        "3. Update ticket status once webhook syncs or carrier manifest confirmation is obtained."
                    )
                elif "bulk" in subj_desc or "csv" in subj_desc or "upload" in subj_desc or "4,200" in subj_desc:
                    investigation_notes.append(
                        "**Root Cause / Operational Insight**: Correlates with **Known Issue KI-208 (Bulk Upload Failures on Large CSVs > 3,000 rows)**.\n"
                        "Intermittent processing timeouts occur on CSV uploads containing over ~3,000 rows, despite the supported product limit of 5,000 rows.\n\n"
                        "**Internal Support Action Steps**:\n"
                        "1. Advise the customer to split their bulk upload into files containing fewer than 3,000 rows as a workaround.\n"
                        "2. Confirm individual shipment creation functions normally.\n"
                        "3. Escalate file logs to engineering if small CSVs also fail."
                    )
                elif "outage" in subj_desc or "shipment creation is failing" in subj_desc or "500" in subj_desc:
                    investigation_notes.append(
                        "**Root Cause / Operational Insight**: P1 Outage Incident — Core shipment creation API returning HTTP 500.\n\n"
                        "**Internal Support Action Steps**:\n"
                        "1. Stage immediate escalation (`create_escalation`) to Tier 2 / Infrastructure team.\n"
                        "2. Notify CSM and account representative immediately as this breaches strategic SLA limits."
                    )
                elif "api key" in subj_desc or "exposure" in subj_desc or "credential" in subj_desc:
                    # Determine if description references a public channel to give more targeted advice
                    public_channel_mention = "public channel" in subj_desc or "public slack" in subj_desc or "screenshot" in subj_desc
                    channel_note = (
                        "The exposed credential was shared via a screenshot in a **public channel**. "
                        "Treat the API key as fully compromised — assume it has been observed by "
                        "unauthorized third parties."
                        if public_channel_mention else
                        "The exposed credential may have been observed by unauthorized parties."
                    )
                    investigation_notes.append(
                        "**Security Risk Identified**: P1 Security Incident — Production API key exposure.\n\n"
                        f"{channel_note}\n\n"
                        "**Investigation Findings & Immediate Actions**:\n\n"
                        "1. **Revoke / rotate the exposed API key immediately** — Access the security admin panel "
                        "or API key management console and revoke the compromised key right away. Generate a "
                        "new production credential and distribute it securely to authorised personnel only. "
                        "Do NOT reproduce or log the exposed key value anywhere.\n\n"
                        "2. **Audit logs for unauthorized usage** — Pull API access logs for the period "
                        "between the time the key was exposed and the time of revocation. Look for any "
                        "unusual request patterns, unknown IP addresses, unexpected endpoints, or abnormal "
                        "request volumes that could indicate unauthorized exploitation.\n\n"
                        "3. **Remove the exposed credential from all public locations** — Delete or redact "
                        "the screenshot or message in the public channel immediately. If the channel is "
                        "externally accessible, request platform-level deletion of message history "
                        "containing the credential to limit further exposure.\n\n"
                        "4. **Escalate as a P1 security incident** — Notify the account's security team "
                        "or CISO, the internal CSM (if applicable), and the ParcelPilot Security Operations "
                        "team. Open a formal P1 security incident record and track revocation confirmation, "
                        "log audit completion, and credential redistribution as required closure criteria.\n\n"
                        "5. **Confirm remediation** — Once the key is rotated and logs reviewed, verify that "
                        "no further unauthorized access occurred. Update the incident record with findings "
                        "and close only after all remediation steps are documented and verified."
                    )
                elif results:
                    top_chunk = results[0]
                    investigation_notes.append(
                        f"**Knowledge Base Reference** (`{top_chunk.get('source_file')}`):\n"
                        f"{top_chunk.get('content')}"
                    )
                else:
                    investigation_notes.append(
                        f"Internal support should review the ticket description ('{ticket_res.get('description')}') "
                        f"and verify account status (`{ticket_res.get('account_id')}`)."
                    )

                findings_text = "\n\n".join(investigation_notes)

                lines = [
                    f"### Support Ticket `{target_ticket}` Investigation\n",
                    f"- **Account**: `{ticket_res.get('account_id')}`",
                    f"- **Subject**: {ticket_res.get('subject')}",
                    f"- **Assigned To**: {ticket_res.get('assigned_to')}",
                    f"- **Identified Severity**: `{severity}`",
                    f"- **Created At**: {ticket_res.get('created_at')}",
                    f"- **Elapsed Time**: {sla_res.get('elapsed_minutes')} minutes",
                    f"- **SLA Target**: {sla_res.get('sla_target_minutes')} minutes ({sla_res.get('sla_source')})",
                    f"- **SLA Status**: **{'BREACHED' if is_breached else 'ON TRACK'}**\n",
                    f"**Investigation Findings & Immediate Actions:**\n{findings_text}\n"
                    if (severity in ("P1", "P2") and ("api key" in subj_desc or "exposure" in subj_desc or "credential" in subj_desc or "outage" in subj_desc))
                    else f"**Investigation Findings & Root Cause Analysis:**\n{findings_text}\n",
                    f"**Recommendation:**\n{sla_res.get('recommendation')}",
                ]

                return AgentResponse(
                    answer="\n".join(lines),
                    citations=citations,
                    tool_traces=tool_traces,
                    confidence_level="high",
                    requires_escalation=sla_res.get("requires_escalation", False),
                )


        # 7. General Document / Knowledge Base Search
        search_res, t1 = dispatch_tool_call(ctx, "search_documents", {"query": query})
        tool_traces.append(t1)
        results = search_res.get("results", [])
        citations = AuthorityEngine.build_citations(results)

        if not results:
            return AgentResponse(
                answer="I searched our policies, SOPs, and knowledge base, but found no matching information for your request.",
                tool_traces=tool_traces,
                citations=citations,
            )

        top_chunk = results[0]
        answer_text = (
            f"Based on `{top_chunk.get('source_file')}` ({top_chunk.get('authority_label')}):\n\n"
            f"{top_chunk.get('content')}"
        )

        return AgentResponse(
            answer=answer_text,
            citations=citations,
            tool_traces=tool_traces,
            confidence_level="high",
        )
