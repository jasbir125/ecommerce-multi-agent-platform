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
    # The evidence must be about the same version the deterministic check used (e.g. not the superseded v1 text).
    if any(c.policy_key == policy.policy_key and c.version != policy.policy_version for c in policy.citations):
        out.append("POLICY_VERSION_MISMATCH")
    return out
