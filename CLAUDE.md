# CLAUDE.md

This is the ParcelPilot AI Agent take-home (CalQuity AI Engineer assessment). Read [specifications.md](specifications.md) for the technical spec and [memory.md](memory.md) for the running decision log before making non-trivial design calls — check memory.md first so you don't re-litigate a decision Teerth already made.

## What this project is

A Python-only AI agent system for ParcelPilot (fictional B2B logistics platform): a customer-facing support chatbot and an internal ops chatbot, sharing one agent core, that answer questions over an intentionally imperfect document + structured-data pack (conflicting policy versions, contract overrides, unreliable historical tickets) and can propose/execute state-changing actions with confirmation.

Grading criteria are the JD's minimum requirements (chatbot + NL queries, access control, ≥3 tools, confirm-before-action, multi-step requests, interface, demo video) plus a chosen bonus problem (Trust & Reliability — see memory.md for why). Optimize for those explicitly, not for generic completeness.

## Stack

- Python only. FastAPI-style agent/tool core + **Streamlit** for the chat UI (confirmed choice — do not swap to a JS frontend without asking).
- SQLite for structured data (loaded from the data pack's `.xlsx`), Chroma for document embeddings.
- Claude (Anthropic API) for the agent loop / tool-calling.

## Repo layout (target — create as needed)

```
data/raw/            # untouched source files from the candidate data pack (gitignored if large/licensed)
data/processed/      # sqlite db, chroma persist dir (gitignored)
src/ingest/          # doc chunking+embedding, xlsx->sqlite loaders
src/tools/           # one file per agent tool — see tool-contract skill
src/agent/           # agent loop, system prompts (customer vs internal), source-authority resolution
src/ui/              # streamlit app(s)
tests/
```

## Hard constraints (from the JD — do not relax these)

1. **Never hardcode example record IDs or answers** (e.g. `ORD-1001`, `Northstar`, `LumenWorks`) in `src/` outside of `tests/`/`data/`. The system is tested against other records from the same pack — logic must be generic. A hook blocks this at edit time; don't work around it, fix the code.
2. **Access control lives in the tool/data layer, not the prompt.** Every tool takes a `ctx: UserContext` first argument and enforces scoping itself (e.g. `WHERE account_id = ctx.account_id`). Never rely on "the system prompt says only answer about the user's own account" as the only control.
3. **State-changing actions require a real confirmation step** (`propose_action` → user confirms → `execute_action`), not a model-decided "I'll ask permission" convention.
4. **No wall-clock time.** Time-based logic (SLA math, "three hours late", contract windows) uses the dataset snapshot time from the workbook's README sheet, stored in a `meta` table — never `datetime.now()`.
5. Treat historical ticket resolutions as unreliable context, never sole justification for an answer.

## Working style

- Log real decisions (stack/scope/architecture choices, not routine implementation detail) to memory.md as you make them — future-you and Teerth both read it. Follow the existing entry format (date, decision, one-line rationale).
- Before adding a new tool, check the [tool-contract skill](.claude/skills/tool-contract/SKILL.md) for the required shape.
- Prefer the slash commands in `.claude/commands/` for repeated workflows (ingestion, eval runs, note drafting) over ad hoc scripts.
- The data pack (PDFs + xlsx) is not yet in this repo — see memory.md "Open Questions". Don't fabricate sample data to fill the gap without flagging it in memory.md as a stand-in.
