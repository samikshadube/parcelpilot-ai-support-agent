"""Source authority hierarchy, conflict detection, and citation engine."""

from typing import Any, Dict, List, Optional, Tuple
from src.models import AuthorityTier, DocStatus, SourceCitation


TIER_LABELS = {
    AuthorityTier.CUSTOMER_AGREEMENT.value: "Customer Agreement (Governing Override for this Account)",
    AuthorityTier.CURRENT_SOP_POLICY.value: "Current Policy / SOP (Standard Authority)",
    AuthorityTier.PRODUCT_OPS_GUIDE.value: "Product Operations Guide (Operational Facts / Known Issues)",
    AuthorityTier.DEPRECATED_POLICY.value: "Deprecated Policy (Historical Reference Only - NOT Governing)",
    AuthorityTier.HISTORICAL_TICKET.value: "Historical Ticket (Unreliable Context Only - Not Authority)",
}


class AuthorityEngine:
    """Evaluates multiple retrieved sources against the source authority hierarchy."""

    @staticmethod
    def build_citations(retrieved_chunks: List[Dict[str, Any]], governing_file: Optional[str] = None) -> List[SourceCitation]:
        """Convert raw search results into structured citations with authority tiering."""
        citations: List[SourceCitation] = []
        seen_files = set()

        for chunk in retrieved_chunks:
            sfile = chunk.get("source_file", "unknown")
            if sfile in seen_files:
                continue
            seen_files.add(sfile)

            tier = int(chunk.get("authority_tier", AuthorityTier.CURRENT_SOP_POLICY.value))
            status = chunk.get("status", DocStatus.CURRENT.value)
            scope = chunk.get("customer_scope")

            # Determine governing status
            is_governing = False
            if governing_file and sfile == governing_file:
                is_governing = True
            elif not governing_file and tier == AuthorityTier.CUSTOMER_AGREEMENT.value:
                is_governing = True

            citations.append(
                SourceCitation(
                    source_file=sfile,
                    authority_tier=tier,
                    authority_label=TIER_LABELS.get(tier, "Reference Document"),
                    status=status,
                    customer_scope=scope,
                    summary=chunk.get("content", "")[:200] + "...",
                    is_governing=is_governing,
                )
            )

        citations.sort(key=lambda c: (c.authority_tier, not c.is_governing))
        return citations

    @staticmethod
    def resolve_conflicts(
        citations: List[SourceCitation],
        has_historical_ticket_resolution: bool = False,
        historical_resolution_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyze citations for conflicts, overrides, and confidence gating."""
        agreement_citations = [c for c in citations if c.authority_tier == AuthorityTier.CUSTOMER_AGREEMENT.value]
        sop_citations = [c for c in citations if c.authority_tier == AuthorityTier.CURRENT_SOP_POLICY.value]
        deprecated_citations = [c for c in citations if c.authority_tier == AuthorityTier.DEPRECATED_POLICY.value]

        has_agreement_override = len(agreement_citations) > 0
        has_deprecated_cited = len(deprecated_citations) > 0
        conflict_detected = False
        confidence = "high"
        explanation = ""

        if has_agreement_override:
            agreement = agreement_citations[0]
            explanation = (
                f"Customer Agreement '{agreement.source_file}' is the highest authority (Tier 1) "
                f"and governs this account, superseding standard policy/SOP terms."
            )

        if has_historical_ticket_resolution and historical_resolution_text:
            explanation += (
                f" Note: Historical ticket context was found ('{historical_resolution_text}'), "
                f"but historical ticket resolutions are unverified context (Tier 5) and do not override official policy or signed agreements."
            )

        if has_deprecated_cited:
            explanation += (
                " Warning: A deprecated policy was retrieved. Deprecated policies (Tier 4) are never cited "
                "as current authority."
            )

        return {
            "has_agreement_override": has_agreement_override,
            "governing_citation": agreement_citations[0] if agreement_citations else (sop_citations[0] if sop_citations else None),
            "conflict_detected": conflict_detected,
            "confidence_level": confidence,
            "explanation": explanation.strip(),
        }
