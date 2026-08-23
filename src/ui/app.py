"""ParcelPilot AI Agent — Streamlit Web Interface."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import streamlit as st

# Force-reload src.agent.loop on every Streamlit rerun so code changes to the
# deterministic engine are picked up without restarting the process.
import importlib
import sys as _sys
for _mod in list(_sys.modules.keys()):
    if _mod.startswith("src.agent") or _mod.startswith("src.tools"):
        del _sys.modules[_mod]

from src.agent.analytics import get_proactive_operational_insights
from src.agent.loop import AgentOrchestrator
from src.db import get_db_connection, get_snapshot_time, init_db
from src.ingest.data_loader import ingest_structured_data
from src.ingest.doc_loader import ingest_documents
from src.models import UserContext
from src.tools.action_tools import execute_action, list_staged_actions


# Ensure DB & vector store are initialized
init_db()


def load_accounts_list():
    """Dynamically load available accounts for mocked auth selector."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT account_id, account_name, plan FROM accounts ORDER BY account_id")
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def clean_api_notes(text: str) -> str:
    """Strip any (API Note: ...) lines from answer text so API notes appear only in Token Usage."""
    if not text:
        return ""
    lines = text.split("\n")
    cleaned = [line for line in lines if not line.strip().startswith("(API Note:")]
    return "\n".join(cleaned).strip()


def render_token_usage_expander(token_usages: Optional[List[Dict[str, Any]]] = None, handled_by: Optional[str] = None):
    """Render collapsible Token Usage expander positioned below Tool Traces and above Citations."""
    with st.expander("🔢 Token Usage", expanded=False):
        if not token_usages:
            p_name = "Groq" if handled_by == "groq" else ("NVIDIA NIM" if handled_by == "nvidia_nim" else "Deterministic Engine")
            st.markdown(f"**Provider:** `{p_name}`")
            st.markdown("- **Model**: Not available")
            st.markdown("- **Status**: Not available")
            st.markdown("- **Input / Prompt Tokens**: Not available")
            st.markdown("- **Output / Completion Tokens**: Not available")
            st.markdown("- **Total Tokens**: Not available")
            return

        for idx, item in enumerate(token_usages):
            if idx > 0:
                st.markdown("---")
            provider = item.get("provider", "Not available")
            model = item.get("model", "Not available")
            status = item.get("status", "success")
            p_tok = item.get("prompt_tokens")
            c_tok = item.get("completion_tokens")
            t_tok = item.get("total_tokens")
            t_lim = item.get("token_limit")
            rem_tok = item.get("remaining_tokens")
            err = item.get("error_message")

            st.markdown(f"**Provider:** `{provider}`")
            st.markdown(f"- **Model**: `{model}`")
            if status == "rate_limited":
                st.markdown("- **Status**: ⚠️ `Rate Limited (429)`")
                if err:
                    st.caption(f"*(API Note: {err})*")
            elif status == "failed":
                st.markdown(f"- **Status**: ❌ `Failed` ({err or 'API Error'})")
                if err:
                    st.caption(f"*(API Note: {err})*")
            elif status == "deterministic":
                st.markdown("- **Status**: ⚙️ `Deterministic Engine (Offline Rule Pipeline)`")
            else:
                st.markdown("- **Status**: ✅ `Success`")

            st.markdown(f"- **Input / Prompt Tokens**: {f'{p_tok:,}' if isinstance(p_tok, int) else 'Not available'}")
            st.markdown(f"- **Output / Completion Tokens**: {f'{c_tok:,}' if isinstance(c_tok, int) else 'Not available'}")
            st.markdown(f"- **Total Tokens**: {f'{t_tok:,}' if isinstance(t_tok, int) else 'Not available'}")
            if isinstance(t_lim, int):
                st.markdown(f"- **Token Limit**: {t_lim:,}")
            if isinstance(rem_tok, int):
                st.markdown(f"- **Remaining Tokens**: {rem_tok:,}")



# Streamlit Page Config
st.set_page_config(
    page_title="ParcelPilot AI Support & Operations",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Load Mock Account Options
accounts = load_accounts_list()
if not accounts:
    accounts = [{"account_id": "ACCT-001", "account_name": "Northstar Logistics (Enterprise)", "plan": "Enterprise"}]

# Sidebar Authentication Controls
st.sidebar.markdown("### 🔐 Authentication & Role")
role_selection = st.sidebar.radio("Select Operating Surface", ["Customer Support Agent", "Internal Support / Operations"])

account_options = {f"{a['account_id']} — {a['account_name']} ({a['plan']})": a["account_id"] for a in accounts}

if role_selection == "Customer Support Agent":
    selected_label = st.sidebar.selectbox("Mock Customer Account", options=list(account_options.keys()))
    selected_account_id = account_options.get(selected_label)
    user_context = UserContext(
        role="customer",
        account_id=selected_account_id,
        user_name=f"User ({selected_account_id})",
    )
    st.sidebar.info(f"**Authenticated Account**: `{selected_account_id}`\n\nAccess is strictly scoped to this account.")
else:
    internal_role = st.sidebar.selectbox(
        "Operator Role",
        options=["Operations Lead", "Tier 2 Support Specialist", "Customer Success Manager"],
    )
    user_context = UserContext(
        role="internal",
        internal_role=internal_role,
        user_name=f"Staff Operator ({internal_role})",
    )
    st.sidebar.success(f"**Operator Mode**: `{internal_role}`\n\nFull cross-account search and diagnostic access enabled.")

# Sidebar Active Fallback Chain Display
st.sidebar.markdown(
    """
    <div style='background-color: #e6f4ea; border-left: 4px solid #137333; padding: 10px; border-radius: 4px;'>
        <p style='margin:0; font-weight:bold; color: #137333;'>⚡ LLM Chain: <span style='font-size:11px; color:#3c4043;'>Groq → NVIDIA NIM</span></p>
        <p style='margin:5px 0 0 0; font-size:12px; color:#5f6368;'><em>Final fallback: Deterministic mode</em></p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Reference Snapshot Time Indicator
st.sidebar.markdown(f"**Reference Snapshot Time**: `{get_snapshot_time()}`")

# Pipeline Maintenance Section in Sidebar
with st.sidebar.expander("⚙️ Knowledge Base & Data Ingestion"):
    if st.button("Reload Structured Data (SQLite)"):
        with st.spinner("Ingesting data..."):
            counts = ingest_structured_data()
            st.success(f"Loaded: {counts}")
    if st.button("Rebuild Vector Index (Chroma)"):
        with st.spinner("Embedding documents..."):
            doc_counts = ingest_documents(force=True)
            st.success(f"Indexed {sum(doc_counts.values())} chunks")


# Main Layout
st.markdown("<div class='main-title'>ParcelPilot AI Support & Operations</div>", unsafe_allow_html=True)
st.markdown("<div class='sub-title'>Intelligent multi-step reasoning, source-authority conflict resolution & confirmed actions</div>", unsafe_allow_html=True)

# Tabs
if user_context.is_internal():
    chat_tab, ops_tab, actions_tab = st.tabs(["💬 Operations Chat & Investigation", "📊 Proactive Issue Detection", "⚡ Staged Actions Manager"])
else:
    chat_tab, actions_tab = st.tabs(["💬 Customer Support Chat", "⚡ Pending Actions"])

orchestrator = AgentOrchestrator()


# Tab 1: Chat Interface
with chat_tab:
    # Initialize session chat history
    session_key = f"chat_messages_{user_context.role}_{user_context.account_id or 'internal'}"
    if session_key not in st.session_state:
        st.session_state[session_key] = []

    # Quick prompt shortcuts
    st.markdown("**Sample Inquiries:**")
    cols = st.columns(3)
    if user_context.is_customer():
        if cols[0].button("Can I cancel my booked order without a fee?"):
            st.session_state[f"quick_prompt_{session_key}"] = "Can I cancel my booked order without a fee? Explain why based on my account terms."
        if cols[1].button("A pickup is 3 hours late. Am I eligible for credit?"):
            st.session_state[f"quick_prompt_{session_key}"] = "A pickup is three hours late because of carrier fault. Should I get a service credit?"
        if cols[2].button("What is the status of my recent support ticket?"):
            st.session_state[f"quick_prompt_{session_key}"] = "What is the status and SLA of my open support ticket?"
    else:
        if cols[0].button("Audit P1 tickets & SLA breach status"):
            st.session_state[f"quick_prompt_{session_key}"] = "List all open tickets, check their SLA targets against current snapshot time, and flag any breaches."
        if cols[1].button("Investigate CSV upload failures"):
            st.session_state[f"quick_prompt_{session_key}"] = "Investigate ticket regarding CSV upload failure. Does this relate to any known issue?"
        if cols[2].button("Compare SOP v4 vs Customer Contract terms"):
            st.session_state[f"quick_prompt_{session_key}"] = "Compare the standard cancellation SOP v4 terms against custom enterprise agreement terms."

    # Container for conversation history (ensures history is rendered before composer)
    chat_container = st.container()

    # Render conversation history
    with chat_container:
        for msg in st.session_state[session_key]:
            with st.chat_message(msg["role"]):
                st.markdown(clean_api_notes(msg["content"]))

                if msg["role"] == "assistant":
                    # 1. Render tool traces if available
                    if "tool_traces" in msg and msg["tool_traces"]:
                        with st.expander(f"🔧 Tool Execution Trace ({len(msg['tool_traces'])} tools used)", expanded=False):
                            for idx, trace in enumerate(msg["tool_traces"]):
                                tname = trace.get("tool_name", "tool")
                                args = trace.get("arguments", {})
                                res = trace.get("result", {})
                                time_ms = trace.get("execution_time_ms", 0.0)
                                st.markdown(f"**Step {idx+1}: `{tname}`** ({time_ms:.1f}ms)")
                                st.json({"arguments": args, "output": res})

                    # 2. ALWAYS render Token Usage section for assistant messages
                    render_token_usage_expander(msg.get("token_usages"), handled_by=msg.get("handled_by"))

                    # 3. Render citations if available
                    if "citations" in msg and msg["citations"]:
                        with st.expander(f"📚 Source Citations & Authority Ranking ({len(msg['citations'])} sources)", expanded=False):
                            for cit in msg["citations"]:
                                tier = cit.get("authority_tier", 2)
                                badge_class = f"badge-tier-{tier}" if tier in (1, 2, 4, 5) else "badge-tier-2"
                                st.markdown(
                                    f"<span class='{badge_class}'>Tier {tier}: {cit.get('authority_label')}</span> "
                                    f"**`{cit.get('source_file')}`** ({cit.get('status')})",
                                    unsafe_allow_html=True
                                )
                                st.caption(f"Content extract: {cit.get('summary')}")

                    # Render handled_by provider info if available
                    if "handled_by" in msg and msg["handled_by"]:
                        p_name = "Groq" if msg["handled_by"] == "groq" else ("NVIDIA NIM" if msg["handled_by"] == "nvidia_nim" else "Deterministic Engine")
                        st.caption(f"⚡ Handled by: `{p_name}`")

                    # Render staged action card if present
                    if "staged_action" in msg and msg["staged_action"]:
                        act = msg["staged_action"]
                        act_id = act.get("action_id")
                        st.markdown(
                            f"""
                            <div class='staged-card'>
                                <h4>⚠️ Action Staged — Explicit Confirmation Required</h4>
                                <p><strong>Action ID:</strong> <code>{act_id}</code></p>
                                <p><strong>Action Type:</strong> {act.get('action_type')}</p>
                                <p><strong>Description:</strong> {act.get('description')}</p>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                        btn_cols = st.columns([1, 1, 4])
                        if btn_cols[0].button("Confirm & Execute", key=f"btn_confirm_{act_id}"):
                            exec_res = execute_action(user_context, act_id)
                            st.success(f"Action Executed: {exec_res.get('execution_result')}")
                            st.session_state[session_key].append({
                                "role": "assistant",
                                "content": f"✅ **Action `{act_id}` Executed:** {exec_res.get('execution_result')}",
                                "handled_by": "deterministic",
                            })
                            st.rerun()

    # Chat Input
    quick_input = st.session_state.pop(f"quick_prompt_{session_key}", None)
    user_prompt = st.chat_input("Ask a question regarding shipments, policies, SLAs, or account terms...") or quick_input

    if user_prompt:
        # Display user message and generate response inside chat_container (above composer)
        st.session_state[session_key].append({"role": "user", "content": user_prompt})
        with chat_container:
            with st.chat_message("user"):
                st.markdown(user_prompt)

            # Generate agent response
            with st.chat_message("assistant"):
                with st.spinner("Analyzing knowledge base & structured records..."):
                    response = orchestrator.run(
                        ctx=user_context,
                        query=user_prompt,
                        chat_history=st.session_state[session_key][:-1],
                    )

                st.markdown(clean_api_notes(response.answer))


                # 1. Render tool traces
                if response.tool_traces:
                    with st.expander(f"🔧 Tool Execution Trace ({len(response.tool_traces)} tools used)", expanded=False):
                        for idx, trace in enumerate(response.tool_traces):
                            st.markdown(f"**Step {idx+1}: `{trace.tool_name}`** ({trace.execution_time_ms:.1f}ms)")
                            st.json({"arguments": trace.arguments, "output": trace.result})

                # 2. ALWAYS render Token Usage section
                tu_list = [tu.model_dump() for tu in response.token_usages] if getattr(response, "token_usages", None) else None
                render_token_usage_expander(tu_list, handled_by=getattr(response, "handled_by", None))

                # 3. Render source citations
                if response.citations:
                    with st.expander(f"📚 Source Citations & Authority ({len(response.citations)} sources)", expanded=False):
                        for cit in response.citations:
                            tier = cit.authority_tier
                            badge_class = f"badge-tier-{tier}" if tier in (1, 2, 4, 5) else "badge-tier-2"
                            st.markdown(
                                f"<span class='{badge_class}'>Tier {tier}: {cit.authority_label}</span> "
                                f"**`{cit.source_file}`** ({cit.status})",
                                unsafe_allow_html=True
                            )
                            st.caption(f"Extract: {cit.summary}")

        # Append assistant response to session state and sync layout state
        st.session_state[session_key].append({
            "role": "assistant",
            "content": response.answer,
            "tool_traces": [t.model_dump() for t in response.tool_traces],
            "token_usages": [tu.model_dump() for tu in response.token_usages] if getattr(response, "token_usages", None) else [],
            "citations": [c.model_dump() for c in response.citations],
            "staged_action": response.staged_action.model_dump() if response.staged_action else None,
            "handled_by": getattr(response, "handled_by", "deterministic"),
        })
        st.rerun()


# Tab 2: Proactive Ops Intelligence (Internal Mode Only)
if user_context.is_internal() and "ops_tab" in locals():
    with ops_tab:
        st.subheader("📊 Proactive Issue Detection & Operations Intelligence")
        insights = get_proactive_operational_insights(user_context)

        # Top metric cards
        summary = insights["summary"]
        mcols = st.columns(4)
        mcols[0].metric("Total Accounts", summary["total_accounts"])
        mcols[1].metric("Active Orders", summary["total_orders"], f"{summary['carrier_fault_orders']} Carrier Faults")
        mcols[2].metric("Open Tickets", summary["open_tickets"], f"{len(insights['critical_sla_alerts'])} SLA Breaches")
        mcols[3].metric("Pending Action Approvals", summary["pending_actions"])

        st.markdown("---")

        # Critical SLA Alerts Table
        st.markdown("### 🚨 Critical Tickets & SLA Breach Alerts")
        alerts = insights["critical_sla_alerts"]
        if alerts:
            st.dataframe(
                alerts,
                column_config={
                    "ticket_id": "Ticket",
                    "account_id": "Account",
                    "subject": "Subject",
                    "severity": "Severity",
                    "elapsed_minutes": "Elapsed (min)",
                    "sla_target_minutes": "Target (min)",
                    "is_breached": "Breached?",
                    "recommendation": "Recommended Action",
                },
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.success("All open tickets are within SLA targets.")

        st.markdown("---")

        # Known Issues Correlation
        st.markdown("### 🔍 Product Operations Known Issues Correlation")
        ki_list = insights["known_issue_correlations"]
        if ki_list:
            for item in ki_list:
                with st.container():
                    st.markdown(f"**Ticket `{item['ticket_id']}` ({item['account_id']})**: {item['subject']}")
                    st.warning(f"**Correlated Issue**: {item['known_issue']}")
                    st.info(f"**Actionable Workaround**: {item['recommended_workaround']}")
                    st.markdown("---")
        else:
            st.info("No active tickets correlated with known product issues.")


# Tab 3: Staged Actions Manager
with actions_tab:
    st.subheader("⚡ Staged Actions & Approvals")
    staged_data = list_staged_actions(user_context)
    actions = staged_data.get("actions", [])

    if not actions:
        st.info("No staged actions currently on record.")
    else:
        for act in actions:
            act_id = act["action_id"]
            status = act["status"]
            with st.container():
                st.markdown(f"#### Action `{act_id}` — Status: `{status.upper()}`")
                st.markdown(f"- **Type**: `{act['action_type']}` | **Target**: `{act['target_entity_type']}:{act['target_entity_id']}`")
                st.markdown(f"- **Description**: {act['description']}")
                st.markdown(f"- **Staged By**: {act['staged_by_role']} at `{act['staged_at']}`")
                if act.get("executed_at"):
                    st.markdown(f"- **Executed Result**: {act.get('execution_result')} (at `{act.get('executed_at')}`)")

                if status == "pending_confirmation":
                    c1, c2 = st.columns([1, 5])
                    if c1.button("Execute Now", key=f"mgr_exec_{act_id}"):
                        res = execute_action(user_context, act_id)
                        st.success(f"Executed: {res.get('execution_result')}")
                        st.rerun()
                st.markdown("---")
