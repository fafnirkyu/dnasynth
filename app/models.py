from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class RiskTier(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SignalSource(str, Enum):
    SEQUENCE_SCREEN = "sequence_screen"
    FRAGMENTATION = "fragmentation"
    CUSTOMER_VERIFY = "customer_verify"


class Customer(BaseModel):
    customer_id: str
    institution_name: str
    email_domain: str
    shipping_address: str
    declared_purpose: str
    account_age_days: int
    denylist_flag: bool = False


class Order(BaseModel):
    order_id: str
    customer_id: str
    sequences: list[str] = Field(..., min_length=1)
    submitted_at: datetime = Field(default_factory=datetime.utcnow)


class HazardSequence(BaseModel):
    hazard_id: str
    sequence: str
    length: int
    label: str
    risk_tier: RiskTier
    note: str


class Signal(BaseModel):
    """One atomic finding from a specialist agent."""
    source: SignalSource
    description: str
    risk_tier: RiskTier
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: dict = Field(default_factory=dict)


class ScreeningVerdict(str, Enum):
    CLEAR = "clear"                    # no meaningful signals
    REVIEW_RECOMMENDED = "review_recommended"   # medium-confidence signals
    ESCALATE = "escalate"              # high-confidence signal(s), human must act before fulfillment


class ScreeningReport(BaseModel):
    """
    Final output for a given order. This is always advisory - a human
    reviewer makes the actual accept/reject/hold call. The agent's job is
    to surface evidence, not to act on it.
    """
    order_id: str
    customer_id: str
    verdict: ScreeningVerdict
    signals: list[Signal]
    summary: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    requires_human_review: bool = True   # intentionally hardcoded True - see note below