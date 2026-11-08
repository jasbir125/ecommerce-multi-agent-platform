"""Layer 4 (business) + grounding: is the policy evidence strong enough to act on?"""
from app.models.policy import PolicyDecision


def policy_violations(policy: PolicyDecision | None, min_confidence: float) -> list[str]:
    """Reasons we may NOT rely on this policy result to propose a refund."""
    if policy is None:
        return ["POLICY_NOT_VERIFIED"]
    out = []
    if policy.insufficient_evidence or not policy.grounded:
        out.append("INSUFFICIENT_POLICY_EVIDENCE")
    if policy.rag_confidence < min_confidence:
        out.append("LOW_POLICY_CONFIDENCE")
    if not policy.citations:
        out.append("MISSING_CITATIONS")
    if not policy.policy_key or not policy.policy_version:
        out.append("MISSING_POLICY_VERSION")
    return out
