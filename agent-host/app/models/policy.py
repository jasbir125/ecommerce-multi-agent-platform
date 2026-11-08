from pydantic import BaseModel, Field


class Citation(BaseModel):
    policy_key: str
    version: str
    source: str | None = None
    score: float = 0.0


class PolicyDecision(BaseModel):
    """Policy facts. refund_eligible comes only from the deterministic validation tool."""
    refund_eligible: bool = False
    approval_required: bool = True
    reasons: list[str] = Field(default_factory=list)
    policy_key: str | None = None
    policy_version: str | None = None
    rag_confidence: float = 0.0
    grounded: bool = False
    insufficient_evidence: bool = True
    citations: list[Citation] = Field(default_factory=list)
    summary: str = ""
    injection_suspected: bool = False
