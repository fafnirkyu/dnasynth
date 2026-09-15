"""
Tests for the Customer Verification Agent, validated against the
synthetic customer records in data/synthetic_customers.json.
"""
import pytest

import app.db as db_module
from app.db import get_connection, init_schema, seed
from app.agents.customer_verify import verify_customer


@pytest.fixture
def conn(tmp_path, monkeypatch):
    test_db_path = tmp_path / "test_screening.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    c = get_connection()
    init_schema(c)
    seed(c)
    yield c
    c.close()


def test_denylisted_customer_flagged(conn):
    """CUST-007: pre-flagged denylist_flag=True, brand new account (1 day),
    confidential declared purpose - should produce at least a denylist signal."""
    signals = verify_customer(conn, "CUST-007")
    reasons = [s.description for s in signals]
    assert any("denylist" in r.lower() for r in reasons)
    assert any(s.risk_tier.value == "high" for s in signals)


def test_established_clean_customer_no_signals(conn):
    """CUST-001: 820-day-old university account, institutional email domain,
    specific declared purpose - should be clean."""
    signals = verify_customer(conn, "CUST-001")
    assert signals == []


def test_established_clean_diagnostics_customer_no_signals(conn):
    """CUST-002: 1500-day-old account, institutional domain, specific purpose."""
    signals = verify_customer(conn, "CUST-002")
    assert signals == []


def test_shell_pattern_customer_flagged(conn):
    """CUST-003: 4-day-old account, gmail.com domain, vague 'general research'
    purpose - hits all three shell heuristics."""
    signals = verify_customer(conn, "CUST-003")
    assert len(signals) == 1
    assert signals[0].risk_tier.value == "medium"
    assert signals[0].confidence == 1.0  # all 3 of 3 shell indicators


def test_shell_pattern_customer_partial_indicators(conn):
    """CUST-004: 6-day-old account, institutional-looking email domain,
    vague purpose - hits 2 of 3 (new account + vague purpose)."""
    signals = verify_customer(conn, "CUST-004")
    assert len(signals) == 1
    assert signals[0].risk_tier.value == "medium"


def test_unknown_customer_id_produces_signal_not_crash(conn):
    signals = verify_customer(conn, "CUST-DOES-NOT-EXIST")
    assert len(signals) == 1
    assert signals[0].confidence == 1.0