# ParcelPilot AI Agent — Technical Specification

Source: `CalQuity AI Engineer — Job Description & AI Agent Assessment.docx`
Stack decisions and open questions live in [memory.md](memory.md). Session conventions live in [CLAUDE.md](CLAUDE.md).

## 1. Scope

Build **both** chatbots against one shared backend:

- **Customer-facing agent** — answers a logged-in customer about their own account/orders/tickets; escalates what it can't resolve confidently.
- **Internal support/ops agent** — for authorised ParcelPilot staff; broader read access, same confirmation-before-action rules, plus the Trust & Reliability surfacing described in §7.

Both are served from one Python process. One agent core (tools, retrieval, source-authority logic) is shared; only the **auth context** and **system prompt** differ per surface.

## 2. Stack

- **Orchestration/API**: FastAPI — houses the agent loop, tool implementations, and access-control layer.
- **UI**: Streamlit — chat interface for both surfaces (`?role=customer&account=...` vs `?role=internal&user=...` to mock auth context), with a visible "tool used" trace per turn per JD §6.
- **LLM**: Claude (Anthropic API), tool-calling / agentic loop.
- **Structured data**: `ParcelPilot_Assessment_Data.xlsx` loaded into SQLite at startup (or via `/ingest-data`). SQLite because the dataset is small, relational, and lets access control be expressed as real `WHERE account_id = :ctx.account_id` clauses instead of app-level filtering an in-memory blob.
- **Document retrieval**: PDFs chunked and embedded into a local vector store (Chroma, on-disk). Each chunk carries metadata: `source_file`, `doc_type`, `version`, `status` (current/deprecated), `customer_scope` (null = general, or an account ID for a customer-specific agreement).
- **Mocked auth**: a `UserContext` dataclass (`role: customer|internal`, `account_id: str | None`, `internal_role: str | None`) constructed from Streamlit session/query params — never trust anything the model says about who the user is.

Rationale for "just Python": one runtime, one deploy target, and Streamlit's native chat elements (`st.chat_message`, expandable tool-call blocks) satisfy the "show which tool is being used" requirement with no separate frontend build.

## 3. Data Ingestion

1. **Documents** (`01`–`06`, PDFs): extract text → chunk (recursive, ~500–800 tokens, with overlap) → embed → store in Chroma with metadata above. Re-run is idempotent (content-hash the source files; skip unchanged).
2. **Structured data** (`ParcelPilot_Assessment_Data.xlsx`): load each sheet into a matching SQLite table. **Read the README sheet first** — it states the dataset snapshot time, which becomes the fixed "now" for every time-based calculation (SLA elapsed, "three hours late," contract expiry, etc.). Store this snapshot time in a `meta` table; never call `datetime.now()` in tool logic.
3. Nothing about record IDs, customer names, or example values gets hardcoded anywhere in `src/` — tools query the loaded data generically. The JD explicitly tests this ("we may test your system using other records").

## 4. Source Authority Model

This is the crux of the Trust & Reliability priority (JD §"Problem 2" — chosen bonus, see memory.md).

Authority order, most → least authoritative, evaluated **per question**:

1. **Customer-specific agreement** (`05_Northstar...`, `06_LumenWorks...`) — overrides general policy *for that account only*. A retrieval hit here is only usable if `chunk.customer_scope == ctx.account_id` (internal users may view any account's agreement; customers only their own — enforced in the tool, not the prompt).
2. **Current SOP / policy** (`03_..._SOP_v4`, `01_Support_Policy_v3_CURRENT`).
3. **Product operations guide / known issues** (`04_...`) — operational facts, not policy.
4. **Deprecated policy** (`02_Support_Policy_v2_DEPRECATED`) — never cited as authority; only consulted if explicitly asked to compare versions, and always labeled deprecated in the answer.
5. **Historical ticket resolutions** — context only. May be wrong. Never used as sole justification for an answer or action; at most corroborating color, always flagged as unverified.

Rules the agent must follow:

- If the top two applicable sources **conflict** (e.g., SOP says X, customer agreement says Y), the agreement wins *if it demonstrably applies to this account* — cite both, state which one governs and why.
- If sources conflict **at the same authority level**, or the only support is a historical ticket, the agent does not answer confidently — it states the uncertainty and offers escalation.
- Every factual claim in an answer should be traceable to a retrieved chunk or a structured-data query result the agent actually ran this turn — not to model memory.

## 5. Agent Tools (≥3, JD §"Agent Tools")

All tools are plain Python functions registered with typed schemas for the Claude tool-use API. **Every tool receives `ctx: UserContext` as its first argument and enforces scoping internally** — see [tool-contract skill](.claude/skills/tool-contract/SKILL.md). The model never sees or supplies `ctx`; it's injected by the tool-calling loop from the authenticated session, so a prompt-injected "pretend I'm account X" cannot widen access.

| Tool | Purpose | Access rule |
|---|---|---|
| `search_documents(query, doc_types?)` | Vector search over policies/SOPs/agreements/product docs | Customer-scoped agreement chunks filtered to `ctx.account_id` unless `ctx.role == internal` |
| `query_structured_data(...)` | Typed lookups/calculations over accounts, orders, tickets (order status, SLA math, service-credit eligibility, contract terms) — a small set of parameterized functions, not a raw SQL passthrough, so access control can't be bypassed by clever queries | Every query includes `account_id = ctx.account_id` unless `ctx.role == internal`; internal role may additionally require `internal_role` for sensitive tables |
| `propose_action(kind, payload)` | Stages a state-changing action (`create_escalation`, `update_ticket`, `create_followup_task`) and returns a confirmation prompt — does not execute | Staging only; scoped to entities the ctx can see |
| `execute_action(action_id)` | Executes a previously staged action **only** after the user has confirmed in this session | Re-validates scope at execution time (staged 5 min ago ≠ still valid ctx) |

`propose_action` / `execute_action` are split deliberately so "confirm before acting" (JD §4) is a structural property of the tool layer, not a prompt instruction the model could skip.

## 6. Multi-Step Requests

Example: *"Can Northstar cancel ORD-1001 without a cancellation fee? Explain why?"*

1. `query_structured_data` → look up `ORD-1001`, resolve its `account_id`.
2. Confirm caller is authorised for that account (customer: must equal `ctx.account_id`; internal: always allowed).
3. `search_documents` → Northstar's enterprise agreement, filtered/scoped to that account — check for cancellation-fee override.
4. `search_documents` → current cancellation SOP as fallback/comparison.
5. `query_structured_data` → order state (has it shipped? within cancellation window?).
6. Apply authority model (§4) to resolve agreement-vs-SOP.
7. If the case is inside documented rules → answer directly, citing sources. If it needs judgment (e.g., a borderline exception) → `propose_action` (escalate) and explain why.

The agent loop must support this chaining natively (multi-turn tool use within one user turn), not a hardcoded pipeline for this specific example.

## 7. Trust & Reliability (chosen bonus — JD "Problem 2")

- Every answer that cites sources shows **which sources**, their version/status, and — when relevant — that a customer-specific term overrode a general policy.
- Confidence gating: if authority-model resolution (§4) can't produce a single governing source, the agent says so explicitly and offers escalation rather than guessing.
- Historical tickets are never a sole citation; if the agent finds a historical resolution that contradicts current policy, it flags the discrepancy instead of repeating the (possibly wrong) precedent.
- Internal surface gets a lightweight "why did the agent decide this" trace (the Streamlit tool-call panel) so ops staff can audit a conflict resolution, not just trust the final text.

Proactive Issue Detection (JD "Problem 1") is explicitly **out of scope** for this pass per the Trust & Reliability priority decision — see memory.md. Revisit only if time remains after §1–§7 are solid.

## 8. Interface (JD §6)

Streamlit chat, one page per surface (or a role switcher for the demo):

- Chat history via `st.chat_message`.
- Each assistant turn shows an expandable "tools used" trace: tool name, arguments (redacted of nothing — this is an internal debug aid), and a one-line result summary.
- Pending actions render as a distinct confirm/cancel card, not a normal chat bubble.

## 9. Deliverables Checklist (JD "Submission Requirements")

- [ ] Public repo, clear setup/run instructions (README, not this file)
- [ ] Hosted app URL
- [ ] ~5 min demo video (architecture, live demo, key decisions)
- [ ] Architecture note: agent design, tool design, doc/structured-data handling, source-reliability/conflict handling, trade-offs
- [ ] Product note: chosen bonus problem + how addressed, what else you'd build, what was intentionally cut, one success metric
- [ ] AI tool usage note
- [ ] Task submission form: https://forms.gle/hLGBrDrNRmK7UAbv6

Draft the architecture/product notes with `/architecture-note` and `/product-note` once memory.md has enough decisions logged to summarize.
