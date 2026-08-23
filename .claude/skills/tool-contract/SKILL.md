---
name: tool-contract
description: The required shape for any agent tool in this project — access-control enforcement, confirm-before-action, and structured-data query safety. Load before writing or reviewing any file under src/tools/.
---

# Tool Contract

Every agent tool in this project (specifications.md §5) must follow this shape. This exists because the JD grades access control and confirm-before-action as structural properties of the system, not as things the model is instructed to do — a reviewer will try to break both.

## 1. `ctx` is always the first argument

```python
def query_order(ctx: UserContext, order_id: str) -> OrderResult:
    ...
```

`UserContext` (`role: "customer" | "internal"`, `account_id: str | None`, `internal_role: str | None`) is constructed once per session from the mocked auth layer (Streamlit session/query params) — it is **never** passed by the model, and the model never sees its fields directly. The tool-calling loop injects it. This means a prompt-injected "pretend I'm on account X's behalf" cannot widen access, because the model has no channel to set `ctx`.

## 2. Scoping is enforced inside the function, as a real constraint

Not a check-then-trust pattern, not a comment saying "caller should be scoped." For structured-data tools this means the SQL/query itself is constrained:

```python
if ctx.role == "customer":
    query = query.where(Order.account_id == ctx.account_id)
```

For document search, filter retrieved chunks whose `customer_scope` is set to an account other than `ctx.account_id`, unless `ctx.role == "internal"`.

Write a test for every tool that asserts an out-of-scope caller is denied/filtered — verify by calling the tool function directly, not by prompting the model and checking if it "behaved."

## 3. Structured-data tools are parameterized, not raw SQL passthroughs

Expose specific typed functions (`get_order(ctx, order_id)`, `list_open_tickets(ctx, account_id?)`, `compute_sla_status(ctx, ticket_id)`), not a generic `run_sql(ctx, query: str)`. A generic passthrough makes access control a prompt-level suggestion again — a clever query could route around a `WHERE` clause added only at one call site.

## 4. State-changing actions are always a propose/execute pair

```python
def propose_escalation(ctx: UserContext, ticket_id: str, reason: str) -> ProposedAction:
    """Stages the action, returns a human-readable confirmation payload + action_id. Does not execute."""

def execute_action(ctx: UserContext, action_id: str) -> ActionResult:
    """Executes a previously staged action. Re-validates ctx scope against the staged action at execution time."""
```

Never collapse this into one tool with a `confirmed: bool` argument — that makes confirmation a value the model can just set to `True`. The harness (agent loop / UI) is what turns a user's explicit "yes" into the `execute_action` call; the model proposing an action and the model executing one must be two separate tool invocations separated by a real user turn.

`execute_action` re-checks scope at execution time, not just at proposal time — `ctx` at execution must still authorize the staged action's target entity.

## 5. No wall-clock time inside tools

Any tool doing date/SLA math reads the snapshot time from the `meta` table (see specifications.md §3), never `datetime.now()`.

## 6. Checklist before considering a tool done

- [ ] `ctx` first argument, used for real scoping (not merely logged)
- [ ] Test proves an out-of-scope caller is denied/filtered
- [ ] If state-changing: split into `propose_*`/`execute_*`, execute re-validates scope
- [ ] If structured-data: parameterized, not raw-SQL
- [ ] If time-aware: reads snapshot from `meta`, not the system clock
- [ ] No hardcoded example IDs/values from the data pack
- [ ] Tool schema registered with the agent loop with a clear, model-facing description
