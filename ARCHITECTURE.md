# ParcelPilot AI Support & Operations — Architecture Note

## 1. Agent Design

The system implements a unified backend supporting two distinct user surfaces:
1. **Customer-Facing Support Agent**: Assisting customers with their specific shipments, orders, ticket statuses, cancellation eligibility, and service credit terms. Access is strictly scoped to the authenticated customer account.
2. **Internal Support & Operations Agent**: Equipping ParcelPilot operations staff and support leads to investigate cross-account tickets, audit SLA compliance, analyze carrier fault patterns, correlate known product issues, and stage operational actions.

```
                  ┌─────────────────────────────────────────┐
                  │       Streamlit Interface (UI)          │
                  │  - Customer Portal / Internal Ops View   │
                  │  - Live Tool Traces & Citation Badges   │
                  │  - Two-Phase Action Confirmation Cards  │
                  └────────────────────┬────────────────────┘
                                       │ Injects UserContext
                                       ▼
                  ┌─────────────────────────────────────────┐
                  │          Agent Orchestrator             │
                  │  - Persona Prompts (Customer/Internal)  │
                  │  - Claude Tool-Calling Agentic Loop     │
                  │  - Deterministic Evaluation Engine      │
                  └────────────────────┬────────────────────┘
                                       │ Dispatches with Context
                                       ▼
                  ┌─────────────────────────────────────────┐
                  │        Tool Layer & Access Control      │
                  │  - search_documents (ChromaDB + Scope)  │
                  │  - query_structured_data (SQLite Scoped)│
                  │  - propose_action / execute_action      │
                  └────────────────────┬────────────────────┘
                                       │ Queries / Updates
                     ┌─────────────────┴─────────────────┐
                     ▼                                   ▼
         ┌───────────────────────┐           ┌───────────────────────┐
         │ SQLite Relational DB  │           │   Chroma Vector DB    │
         │ - accounts / orders   │           │ - Document Chunks     │
         │ - tickets / actions   │           │ - Authority Tiers     │
         │ - snapshot metadata   │           │ - Customer Scope      │
         └───────────────────────┘           └───────────────────────┘
```

### Key Architectural Invariants:
- **Harness-Injected `UserContext`**: Every tool receives an immutable `UserContext(role, account_id, internal_role)` object injected by the execution harness. The LLM never supplies, sees, or modifies the auth context.
- **Dual Persona Prompts**: Role-specific instructions enforce tone, scoping guidelines, and proactive escalation recommendations without relying on the prompt as a security boundary.
- **Full Traceability**: Every assistant turn produces a detailed `ToolTrace` (tool name, exact arguments, execution time, and output payload) and `SourceCitation` records rendered directly in the UI.

---

## 2. Tool Design & Safety

Every tool adheres to the project's **Tool Contract**:

1. **Access Control in the Data Layer**:
   - `get_order_details`, `get_ticket_details`, `list_orders`, `list_tickets`, and `get_account_details` enforce SQL-level filtering (`WHERE account_id = :ctx.account_id`).
   - If a customer queries an entity belonging to another account, the tool rejects the request with an `AccessDenied` error and leaks zero entity metadata.
2. **Parameterized Queries (No Raw SQL)**:
   - Structured tools expose typed, parameterized functions (`calculate_cancellation_terms`, `calculate_service_credit`, `calculate_ticket_sla`) rather than a raw SQL passthrough, preventing SQL injection and authorization bypass.
3. **Two-Phase Action Execution (Confirm Before Acting)**:
   - State-changing operations are strictly decoupled into:
     - `propose_action`: Stages the action in the `staged_actions` table with a unique `action_id` and returns a human-readable confirmation prompt. No database mutations occur on the target entity.
     - `execute_action`: Invoked only after explicit user confirmation. Re-validates the caller's authorization scope against the staged entity at execution time before modifying the database.
4. **No Wall-Clock Time**:
   - All time-based calculations (SLA elapsed times, cancellation windows, pickup delays) use the fixed dataset snapshot reference time (`2026-08-16 11:00 Asia/Kolkata`) retrieved from the SQLite `meta` table, guaranteeing repeatable evaluations.

---

## 3. Document & Structured Data Handling

### Structured Data Pipeline (`src/ingest/data_loader.py`):
- Ingests `ParcelPilot_Assessment_Data.xlsx` into a relational SQLite database (`data/processed/parcelpilot.db`).
- Parses the workbook's `README` sheet first to store reference metadata (`dataset_snapshot`, `currency`, notes) in a `meta` table.
- Loads `accounts`, `orders`, and `tickets` with relational constraints, foreign keys, and indexes on `account_id`.

### Document Ingestion & Vector Store (`src/ingest/doc_loader.py`):
- Chunks source PDFs using section-aware recursive text splitting while preserving numbered clause headers and context.
- Tags each chunk with rich metadata:
  - `source_file`: File name (e.g. `01_Support_Policy_v3_CURRENT.pdf`).
  - `doc_type`: `customer_agreement`, `sop`, `support_policy`, `product_operations`, or `deprecated_policy`.
  - `version`: Version identifier (e.g. `v3`, `v4`, `2026`).
  - `status`: `current` vs `deprecated`.
  - `customer_scope`: Account ID (e.g. `ACCT-001`, `ACCT-002`) or `general`.
  - `authority_tier`: Numeric ranking from 1 (highest) to 5 (lowest).
- Stores embeddings in a persistent on-disk ChromaDB vector store (`data/processed/chroma_db`).
- Re-runs are idempotent via SHA-256 content hashing.

---

## 4. Source Reliability & Conflict Handling (Trust & Reliability)

To address the core Trust & Reliability challenge (JD Problem 2), the system enforces a strict 5-tier Source Authority Model evaluated on every turn:

| Tier | Source Category | Description | Authority Rule |
| :--- | :--- | :--- | :--- |
| **Tier 1** | **Customer-Specific Agreement** | Signed contracts (e.g., Enterprise/Service Agreements) | **Governing override** for that account. Supersedes general policies and standard SOPs. |
| **Tier 2** | **Current Policy & SOP** | `Support_Policy_v3_CURRENT`, `Cancellation_and_Service_Credit_SOP_v4` | Default governing standard across all accounts unless overridden by Tier 1. |
| **Tier 3** | **Product Operations Guide** | Product capabilities & Known Issues (e.g. `KI-208`, `KI-211`) | Operational facts, feature limits, and temporary workarounds. |
| **Tier 4** | **Deprecated Policy** | `Support_Policy_v2_DEPRECATED` | Historical reference only. **Never cited as authority**; explicitly flagged if consulted. |
| **Tier 5** | **Historical Ticket Resolutions** | Past closed ticket resolution notes | Unverified historical context. **Never used as sole justification**; flagged when contradictory. |

### Conflict Resolution Invariants:
1. **Agreement vs. SOP Conflicts**: When a customer agreement and SOP conflict (e.g., fee waiver before pickup vs standard INR 250 fee, or fixed INR 300 credit vs default 10% credit), the customer agreement governs if the caller is authorized for that account. Both sources are cited, stating which governs and why.
2. **Historical Ticket Contradictions**: If a historical ticket resolution contains obsolete or contradictory guidance (e.g., TKT-450 claiming a cancellation fee applied to Northstar, or TKT-451 claiming Growth plan supports only 3,000 rows), the agent flags the ticket resolution as inaccurate and cites the authoritative Tier 1 or Tier 2 source.
3. **Confidence Gating**: If sources conflict at the same authority level without a clear precedence rule, the agent refuses to guess, states the discrepancy, and recommends human escalation.

---

## 5. Major Technical Trade-Offs

1. **Python-Only Architecture (Streamlit + FastAPI Core)**:
   - *Decision*: Avoided separate frontend build pipelines (React/Next.js) in favor of Streamlit backed by modular Python agent libraries.
   - *Rationale*: Streamlit's native `st.chat_message`, `st.expander`, and dynamic forms natively provide live tool execution inspection, citation rendering, and action confirmation cards with zero frontend compilation overhead.
2. **SQLite Relational Store vs In-Memory Pandas**:
   - *Decision*: Loaded Excel records into SQLite with relational schema rather than querying raw Pandas DataFrames.
   - *Rationale*: Expresses access control as genuine SQL `WHERE account_id = :ctx.account_id` query constraints, preventing accidental bypasses in newly added analytical workflows.
3. **Two-Phase Action Protocol vs Single Tool with Confirmation Flag**:
   - *Decision*: Split state changes into distinct `propose_action` and `execute_action` tool invocations.
   - *Rationale*: Makes "confirm before acting" an architectural guarantee enforced by the system harness rather than a model convention that could be jailbroken or bypassed.
4. **Dual Execution Engine (Live Claude API + Deterministic Fallback)**:
   - *Decision*: Implemented full Anthropic tool-calling loop alongside a deterministic offline reasoning engine.
   - *Rationale*: Ensures the entire application, evaluation suite, and UI operate reliably in live production with Claude models as well as in offline/test environments without API keys.
