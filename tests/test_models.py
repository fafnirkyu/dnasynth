"""Quick sanity tests for Pydantic schemas."""
import pytest
from app.models import (
    Order, Customer, Signal, ScreeningReport, ScreeningVerdict,
    RiskTier, SignalSource
)


def test_order_requires_at_least_one_sequence():
    with pytest.raises(Exception):
        Order(order_id="X", customer_id="Y", sequences=[])


def test_order_valid_construction():
    o = Order(order_id="ORD-1", customer_id="CUST-1", sequences=["ACGT"])
    assert o.order_id == "ORD-1"
    assert len(o.sequences) == 1


def test_signal_confidence_must_be_0_to_1():
    with pytest.raises(Exception):
        Signal(source=SignalSource.SEQUENCE_SCREEN, description="x",
               risk_tier=RiskTier.HIGH, confidence=1.5)


def test_screening_report_forces_human_review_true():
    s = Signal(source=SignalSource.CUSTOMER_VERIFY, description="x",
               risk_tier=RiskTier.LOW, confidence=0.2)
    r = ScreeningReport(order_id="O", customer_id="C",
                         verdict=ScreeningVerdict.CLEAR, signals=[s], summary="ok")
    assert r.requires_human_review is True