from typing import Literal

from pydantic import BaseModel, Field


class RiskAssessment(BaseModel):
    level: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    score: float = 0.5
    factors: list[str] = Field(default_factory=list)
    source: str = "RULES"
