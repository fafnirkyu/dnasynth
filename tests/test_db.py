"""
Quick sanity tests for db.py - uses a temp DB file per test run so we
never touch the real data/screening.db.
"""
import pytest
import app.db as db_module
from app.db import (
    get_connection, init_schema, seed, get_customer,
    get_customers_at_same_address, insert_order,
    get_order_history_for_customer, get_all_hazard_sequences,
    is_customer_denylisted
)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Point db.py at a throwaway DB file for the duration of the test."""
    test_db_path = tmp_path / "test_screening.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    c = get_connection()
    init_schema(c)
    seed(c)
    yield c
    c.close()


def test_seed_loads_all_customers(conn):
    c = get_customer(conn, "CUST-001")
    assert c is not None
    assert c["institution_name"] == "Riverbend University - Dept of Molecular Biology"


def test_seed_loads_hazard_sequences(conn):
    hazards = get_all_hazard_sequences(conn)
    assert len(hazards) == 8


def test_address_cluster_detects_shell_group(conn):
    related_to_004 = {r["customer_id"] for r in get_customers_at_same_address(conn, "CUST-004")}
    assert related_to_004 == {"CUST-003", "CUST-005"}


def test_denylist_flag_true_for_flagged_customer(conn):
    assert is_customer_denylisted(conn, "CUST-007") is True


def test_denylist_false_for_clean_customer(conn):
    assert is_customer_denylisted(conn, "CUST-001") is False


def test_order_insert_and_history_roundtrip(conn):
    insert_order(conn, "ORD-TEST-1", "CUST-002", ["AAAA", "TTTT"])
    history = get_order_history_for_customer(conn, "CUST-002")
    assert len(history) == 1
    assert history[0]["sequences"] == ["AAAA", "TTTT"]


def test_order_history_excludes_current_order_when_asked(conn):
    insert_order(conn, "ORD-TEST-2", "CUST-002", ["GGGG"])
    insert_order(conn, "ORD-TEST-3", "CUST-002", ["CCCC"])
    history = get_order_history_for_customer(conn, "CUST-002", exclude_order_id="ORD-TEST-3")
    order_ids = {h["order_id"] for h in history}
    assert "ORD-TEST-3" not in order_ids
    assert "ORD-TEST-2" in order_ids