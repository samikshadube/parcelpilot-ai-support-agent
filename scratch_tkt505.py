"""Verify deterministic engine output for TKT-505 security investigation."""
import sys
sys.path.insert(0, ".")
from src.db import init_db
from src.ingest.data_loader import ingest_structured_data
from src.agent.loop import AgentOrchestrator
from src.models import UserContext

init_db()
ingest_structured_data()

orch = AgentOrchestrator()

print("=== TKT-505 Deterministic Response ===")
ctx = UserContext(role="internal", internal_role="Operations Lead")
r = orch._run_deterministic_reasoner(
    ctx=ctx,
    query="For TKT-505, investigate the exposed production API key. Explain the security risk, why it is classified as P1, what immediate actions internal support should take, and what the current SLA status is."
)
print(r.answer)
print("\nHandled by:", r.handled_by)
print("Tools:", [t.tool_name for t in r.tool_traces])
