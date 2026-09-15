"""
Unit tests for the orchestrator's signal-aggregation and verdict logic,
independent of the full pipeline (that's covered separately in
test_pipeline.py against all 10 hackathon test cases).
"""
import pytest

import app.db as db_module
from app.db import get_connection, init_schema, seed
from app.agents.orchestrator import screen_order, _verdict_from_signals
from app.models import Signal, RiskTier, SignalSource, ScreeningVerdict


@pytest.fixture
def conn(tmp_path, monkeypatch):
    test_db_path = tmp_path / "test_screening.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    c = get_connection()
    init_schema(c)
    seed(c)
    yield c
    c.close()


def _sig(risk_tier):
    return Signal(source=SignalSource.SEQUENCE_SCREEN, description="x",
                  risk_tier=risk_tier, confidence=0.9)


def test_verdict_no_signals_is_clear():
    assert _verdict_from_signals([]) == ScreeningVerdict.CLEAR


def test_verdict_medium_only_is_review_recommended():
    assert _verdict_from_signals([_sig(RiskTier.MEDIUM)]) == ScreeningVerdict.REVIEW_RECOMMENDED


def test_verdict_any_high_escalates_even_with_medium_present():
    signals = [_sig(RiskTier.MEDIUM), _sig(RiskTier.HIGH), _sig(RiskTier.LOW)]
    assert _verdict_from_signals(signals) == ScreeningVerdict.ESCALATE


def test_verdict_low_only_is_clear():
    assert _verdict_from_signals([_sig(RiskTier.LOW)]) == ScreeningVerdict.CLEAR


def test_screen_order_clean_case_returns_clear_report(conn):
    order = {"order_id": "ORD-CLEAN", "customer_id": "CUST-001",
             "sequences": ["ATGCGTACGTTAGCCTAGGCTAACGTAGCTT"]}
    report = screen_order(conn, order)
    assert report.verdict == ScreeningVerdict.CLEAR
    assert report.signals == []
    assert report.requires_human_review is True  # always true, regardless of verdict


def test_screen_order_denylisted_customer_escalates(conn):
    order = {"order_id": "ORD-BAD-CUST", "customer_id": "CUST-007",
             "sequences": ["TTAGGCTAGCATGGCTAGCATTAGGCATCG"]}
    report = screen_order(conn, order)
    assert report.verdict == ScreeningVerdict.ESCALATE
    assert len(report.signals) >= 1