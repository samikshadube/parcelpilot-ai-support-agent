# Project Memory / Decision Log

Running log for this build. Append new entries under the relevant section with a date; don't rewrite history — if a decision changes, add a new entry noting what changed and why, rather than editing the old one away. This is a project file read by whoever (human or Claude) picks this repo up next — it is separate from Claude's own cross-session memory system.

## Status

- 2026-08-22 — Candidate data pack downloaded into `data/raw/` (6 PDFs + `ParcelPilot_Assessment_Data.xlsx`).
- 2026-08-22 — Database & document ingestion pipelines built and verified (`src/ingest/`).
- 2026-08-22 — Tool suite with data-layer access control and two-phase action staging implemented (`src/tools/`).
- 2026-08-22 — 5-Tier Source Authority & Conflict Engine implemented (`src/authority.py`).
- 2026-08-22 — Agent orchestrator with dual personas and deterministic fallback implemented (`src/agent/`).
- 2026-08-22 — Streamlit UI with live tool traces, citation badges, action confirmation cards, and proactive ops intelligence tab built (`src/ui/app.py`).
- 2026-08-22 — Complete 19-test pytest suite passing cleanly (`tests/`).
- 2026-08-22 — ARCHITECTURE.md, PRODUCT_NOTE.md, README.md, and requirements.txt generated.

## Open Questions / Blockers

- **Data pack resolved**: Downloaded all 7 files from candidate Google Drive folder directly into `data/raw/`.
- **Snapshot time resolved**: Extracted `2026-08-16 11:00 Asia/Kolkata` from Excel README sheet and stored in SQLite `meta` table.
- **Hosting target**: Ready for Streamlit Community Cloud or local deployment.
## Decisions

- **2026-08-22 — Stack: Python-only (FastAPI + Streamlit).** Teerth's call. Avoids a second frontend toolchain; Streamlit's `st.chat_message` + expandable blocks satisfy the "show which tool is being used" UI requirement natively. FastAPI hosts the agent/tool layer behind it (or Streamlit calls the agent core directly in-process — decide once the agent loop exists; a separate FastAPI process only matters if something besides Streamlit needs to call it).
- **2026-08-22 — Build both chatbots (customer-facing + internal), sharing one agent core and tool layer, differing only in `UserContext` and system prompt.** Teerth's call; JD allows either or both — both is more work but the tool-layer access control is shared, so the marginal cost is mostly a second system prompt + Streamlit view, not new infra.
- **2026-08-22 — Bonus problem: Trust & Reliability, not Proactive Issue Detection.** Teerth's call. Rationale: Trust & Reliability strengthens the *required* chatbot (source authority/conflict handling is graded implicitly even without picking it as the bonus), whereas Proactive Issue Detection is a separate, additively-scoped feature (a dashboard) that doesn't help the core chatbot pass. Revisit Proactive Issue Detection only if time remains — see specifications.md §7.
- **2026-08-22 — SQLite over an in-memory/pandas store for structured data.** Lets access control (`WHERE account_id = ...`) be a real, testable query constraint rather than an app-level filter that's easy to forget in a new code path.
- **2026-08-22 — `propose_action` / `execute_action` split as two separate tools** rather than one action tool with a `confirmed: bool` flag the model sets itself. Makes "confirm before acting" a structural property enforced by the harness (execute requires a prior staged `action_id` plus an explicit user confirmation event), not something a prompt instruction — or a jailbroken model — could skip.
- **2026-08-22 — Dual-Mode Orchestration (Claude Tool-Calling + Deterministic Reasoning Engine)**: When `ANTHROPIC_API_KEY` is provided, orchestrator invokes Claude with dynamic tool calling. When offline or in test mode, the built-in deterministic reasoner executes tool pipelines, ensuring automated tests and local evaluations run seamlessly in any environment.
- **2026-08-22 — Proactive Ops Intelligence Tab Added to Internal Surface**: Added a lightweight operations dashboard (`src/agent/analytics.py`) to the internal ops view in Streamlit to correlate Known Issues (KI-208, KI-211) and audit open ticket SLA breaches without bloating the core architecture.

## Assumptions

- (none logged yet — record assumptions made about the data pack, mocked auth, or ambiguous JD wording here as they're made, so the product note's "assumptions" section can be pulled straight from this list)

## Cut Scope

- Proactive Issue Detection (Problem 1) — see Decisions above. If picked back up, log why here.
