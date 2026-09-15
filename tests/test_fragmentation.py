"""
Tests for the Fragmentation Agent, validated against TC-06/07/08 from
data/test_cases.json - same-customer 2-part, same-customer 3-part, and the
adversarial cross-customer (address-cluster) case.
"""
import json
from pathlib import Path

import pytest

import app.db as db_module
from app.db import get_connection, init_schema, seed, insert_order, get_all_hazard_sequences
from app.agents.fragmentation import check_fragmentation

DATA_DIR = Path(__file__).parent.parent / "data"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    test_db_path = tmp_path / "test_screening.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    c = get_connection()
    init_schema(c)
    seed(c)
    yield c
    c.close()


def _load_case(case_id: str):
    with open(DATA_DIR / "test_cases.json") as f:
        cases = json.load(f)
    return next(c for c in cases if c["case_id"] == case_id)


def test_two_part_fragmentation_same_customer(conn):
    """TC-06: HZ-001 split across two orders from the same customer."""
    case = _load_case("TC-06")
    hazards = get_all_hazard_sequences(conn)

    # Simulate the earlier order already having been submitted/recorded.
    linked = case["linked_order"]
    insert_order(conn, linked["order_id"], linked["customer_id"], linked["sequences"])

    signals = check_fragmentation(conn, case["order"] | {"customer_id": case["customer_id"]}, hazards)

    assert len(signals) == 1
    assert signals[0].evidence["hazard_id"] == case["expected"]["hazard_id"]
    assert signals[0].evidence["reason"] == "fragmentation_same_customer"
    assert set(signals[0].evidence["order_ids"]) == {case["order"]["order_id"], linked["order_id"]}


def test_three_part_fragmentation_same_customer(conn):
    """TC-07: HZ-002 split across three orders from the same customer."""
    case = _load_case("TC-07")
    hazards = get_all_hazard_sequences(conn)

    for linked in case["linked_orders"]:
        insert_order(conn, linked["order_id"], linked["customer_id"], linked["sequences"])

    signals = check_fragmentation(conn, case["order"] | {"customer_id": case["customer_id"]}, hazards)

    assert len(signals) == 1
    assert signals[0].evidence["hazard_id"] == case["expected"]["hazard_id"]
    expected_order_ids = {case["order"]["order_id"]} | {l["order_id"] for l in case["linked_orders"]}
    assert set(signals[0].evidence["order_ids"]) == expected_order_ids


def test_adversarial_fragmentation_across_related_customers(conn):
    """
    TC-08: hardest case - HZ-001 split across two DIFFERENT customer_ids
    (CUST-004, CUST-005) that share a shipping-address cluster. Requires
    the address-cluster lookup, not just a same-customer_id match.
    """
    case = _load_case("TC-08")
    hazards = get_all_hazard_sequences(conn)

    linked = case["linked_order"]
    insert_order(conn, linked["order_id"], linked["customer_id"], linked["sequences"])

    signals = check_fragmentation(conn, case["order"] | {"customer_id": case["customer_id"]}, hazards)

    assert len(signals) == 1
    assert signals[0].evidence["hazard_id"] == case["expected"]["hazard_id"]
    assert signals[0].evidence["reason"] == "fragmentation_related_customers"
    assert set(signals[0].evidence["customer_ids"]) == {"CUST-004", "CUST-005"}


def test_no_false_positive_for_clean_customer_with_no_history(conn):
    """A clean order with no order history and no related customers should
    produce zero fragmentation signals."""
    hazards = get_all_hazard_sequences(conn)
    order = {"order_id": "ORD-CLEAN", "customer_id": "CUST-001", "sequences": ["ATGCGTACGTTAGCCTAGGCTAACGTAGCTT"]}
    signals = check_fragmentation(conn, order, hazards)
    assert signals == []


def test_no_false_positive_within_single_order_multi_sequence(conn):
    """Multiple unrelated sequences within ONE order should not be treated
    as cross-order fragmentation (that would be the Sequence Screening
    Agent's job, not this one's)."""
    hazards = get_all_hazard_sequences(conn)
    order = {
        "order_id": "ORD-MULTI",
        "customer_id": "CUST-002",
        "sequences": ["GGCTAGCTAGGATCCGAATTC", "TTAGGCATGCCTAGGAATTCG", "CGGATCCAAGCTTGAGTACTG"]
    }
    signals = check_fragmentation(conn, order, hazards)
    assert signals == []