"""Data shapes of the RAG subsystem. RagResult is the response contract used by every consumer."""
from __future__ import annotations

import math
import re
from typing import Any

from pydantic import BaseModel, Field


def est_tokens(text: str) -> int:
    """Rough token estimate (about 1.3 tokens per word)."""
    return math.ceil(len(text.split()) * 1.3) if text and text.strip() else 0


class Chunk(BaseModel):
    """One piece of policy evidence as returned by policy-service."""
    chunk_id: str
    policy_id: str = ""
    policy_key: str
    version: str
    title: str = ""
    section: str | None = None
    policy_type: str = ""
    region: str = "ALL"
    text: str | None = None
    score: float = 0.0
    rerank_score: float = 0.0
    injection_suspected: bool = False

    @classmethod
    def from_service(cls, s: dict[str, Any]) -> "Chunk":
        return cls(chunk_id=str(s.get("chunkId") or s.get("policyId") + ":" + str(s.get("section"))), policy_id=str(s.get("policyId", "")),
                   policy_key=s["policyKey"], version=str(s["version"]), title=s.get("source") or "", section=s.get("section"),
                   policy_type=s.get("policyType") or "", region=s.get("region") or "ALL",
                   text=s.get("text"), score=float(s.get("score") or 0), rerank_score=float(s.get("rerankScore") or 0),
                   injection_suspected=bool(s.get("injectionSuspected")))


class ContextItem(BaseModel):
    """A numbered, budgeted piece of evidence the generator may cite as [n]."""
    n: int
    chunk: Chunk
    text: str


class ContextPack(BaseModel):
    items: list[ContextItem] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)       # reasons, e.g. "injection:ID", "budget:ID"
    tokens: int = 0

    def get(self, n: int) -> ContextItem | None:
        return next((i for i in self.items if i.n == n), None)


class Statement(BaseModel):
    """One claim in the answer and the context numbers that support it."""
    text: str
    cites: list[int] = Field(default_factory=list)


class GroundedAnswer(BaseModel):
    """Structured output of the generator."""
    statements: list[Statement] = Field(default_factory=list)
    insufficient_evidence: bool = False


class Issue(BaseModel):
    code: str
    statement: int | None = None
    detail: str = ""


class GroundingReport(BaseModel):
    passed: bool
    score: float                             # share of statements with no issue, 0..1
    issues: list[Issue] = Field(default_factory=list)


# ------------------------------------------------------------ response contract
class RagSource(BaseModel):
    policyId: str
    chunkId: str
    policyKey: str
    version: str
    source: str
    section: str | None = None
    score: float
    rerankScore: float = 0.0
    text: str | None = None


class RagResult(BaseModel):
    """{answer, confidence, grounded, policyVersion, sources[], insufficientEvidence} plus diagnostics."""
    answer: str
    confidence: float
    grounded: bool
    policyVersion: str | None = None
    sources: list[RagSource] = Field(default_factory=list)
    insufficientEvidence: bool = False
    diagnostics: dict[str, Any] = Field(default_factory=dict)


INSUFFICIENT_ANSWER = "I could not verify this from the available policy information. Please contact support."
INSTRUCTION_LIKE = re.compile(
    r"ignore (all )?(the )?(previous|prior|above) instructions|disregard .{0,40}instructions|system prompt|"
    r"you (must|should) (now )?(issue|approve|process) (a )?refund|act as (an? )?(admin|system)|reveal your (prompt|instructions)",
    re.IGNORECASE)
