---
description: Scaffold a new agent tool following the project's tool contract
---

Add a new agent tool named `$ARGUMENTS` (if no name given, ask me for one and what it should do).

Follow `.claude/skills/tool-contract/SKILL.md` exactly:

1. New file under `src/tools/`, one tool per file.
2. Typed function signature with `ctx: UserContext` as the first argument; enforce scoping inside the function body, not via a docstring/prompt instruction.
3. A tool schema (name, description, JSON-schema args) registered wherever the agent loop collects its tool list.
4. If this is a state-changing action, split it into a `propose_*` (stages, returns a confirmation payload) and `execute_*` (requires a valid staged action id) pair per specifications.md §5 — do not build a single tool that both decides and executes.
5. A test in `tests/` that asserts: (a) the happy path, (b) that a caller scoped to a different account/role is denied or filtered, not just warned.

Update memory.md's Decisions section with a one-line note if this tool's design required a non-obvious call.
