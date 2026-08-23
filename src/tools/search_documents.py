"""Document search tool with access control, authority tiering, and metadata enrichment."""

from typing import Any, Dict, List, Optional
import chromadb
from src.config import CHROMA_DIR
from src.ingest.doc_loader import COLLECTION_NAME
from src.models import AuthorityTier, DocStatus, UserContext


AUTHORITY_LABELS = {
    AuthorityTier.CUSTOMER_AGREEMENT.value: "Customer Agreement (Governing Override for this Account)",
    AuthorityTier.CURRENT_SOP_POLICY.value: "Current Policy / SOP (Authoritative Standard)",
    AuthorityTier.PRODUCT_OPS_GUIDE.value: "Product Operations Guide (Operational Fact / Known Issue)",
    AuthorityTier.DEPRECATED_POLICY.value: "Deprecated Policy (Historical Reference Only - NOT Governing)",
    AuthorityTier.HISTORICAL_TICKET.value: "Historical Ticket (Unreliable Context Only)",
}


def search_documents(
    ctx: UserContext,
    query: str,
    doc_types: Optional[List[str]] = None,
    n_results: int = 5,
) -> Dict[str, Any]:
    """Search knowledge base documents (policies, SOPs, agreements, product guides).

    Access Control:
    - Customer users can ONLY retrieve general documents or agreements specifically scoped to their own account_id.
    - Other customer agreements are strictly filtered at the data layer.
    - Internal users can search general documents and any customer agreement.

    Args:
        ctx: Injected authenticated user context.
        query: Natural language search query.
        doc_types: Optional list of document types to filter by.
        n_results: Number of results to retrieve before filtering.

    Returns:
        Structured dictionary with matched chunks, authority rankings, and metadata.
    """
    if not query or not query.strip():
        return {
            "query": query,
            "total_matches": 0,
            "results": [],
            "message": "Empty query provided.",
        }

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(name=COLLECTION_NAME)

    # Fetch extra candidates to account for post-retrieval access control filtering
    fetch_k = max(n_results * 3, 10)

    # Chroma query
    raw_results = collection.query(
        query_texts=[query.strip()],
        n_results=fetch_k,
        include=["documents", "metadatas", "distances"]
    )

    docs = raw_results.get("documents", [[]])[0]
    metas = raw_results.get("metadatas", [[]])[0]
    distances = raw_results.get("distances", [[]])[0]

    filtered_results: List[Dict[str, Any]] = []

    for doc_text, meta, dist in zip(docs, metas, distances):
        doc_scope = meta.get("customer_scope", "general")
        meta_doc_type = meta.get("doc_type", "general")

        # Access control enforcement
        if ctx.is_customer():
            # If doc is customer-scoped, caller must match the scope exactly
            if doc_scope != "general":
                if not ctx.account_id or doc_scope.strip().upper() != ctx.account_id.strip().upper():
                    # Strictly filter out out-of-scope customer agreements
                    continue

        # Optional doc_type filter
        if doc_types and meta_doc_type not in doc_types:
            continue

        tier = int(meta.get("authority_tier", AuthorityTier.CURRENT_SOP_POLICY.value))
        status = meta.get("status", DocStatus.CURRENT.value)

        # Convert distance to similarity score
        similarity = max(0.0, 1.0 - (float(dist) / 2.0))

        filtered_results.append({
            "source_file": meta.get("source_file"),
            "doc_title": meta.get("doc_title"),
            "doc_type": meta_doc_type,
            "version": meta.get("version"),
            "status": status,
            "customer_scope": doc_scope if doc_scope != "general" else None,
            "authority_tier": tier,
            "authority_label": AUTHORITY_LABELS.get(tier, "General Document"),
            "is_deprecated": status == DocStatus.DEPRECATED.value or tier == AuthorityTier.DEPRECATED_POLICY.value,
            "content": doc_text,
            "similarity_score": round(similarity, 4),
        })

        if len(filtered_results) >= n_results:
            break

    # Sort results by authority tier (most authoritative first), then by similarity
    filtered_results.sort(key=lambda x: (x["authority_tier"], -x["similarity_score"]))

    return {
        "query": query,
        "caller_role": ctx.role,
        "caller_account_id": ctx.account_id,
        "total_matches": len(filtered_results),
        "results": filtered_results,
    }
