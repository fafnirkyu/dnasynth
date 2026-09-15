"""
Full pipeline integration test - runs ALL 10 cases from data/test_cases.json
through the actual orchestrator (all three agents + verdict logic) and
checks the resulting verdict's flag/no-flag outcome against each case's
`expected` field.

This is the closest thing to "does the whole system work" and also
doubles as the eval harness structure the hackathon submission needs for
the baseline comparison (see eval/run_eval.py, built separately).

Ordering matters: cases with linked_order(s) must have those inserted into
order history BEFORE the case's own order is screened, to correctly
simulate "these were submitted earlier."
"""
import json
from pathlib import Path

import pytest

import app.db as db_module
from app.db import get_connection, init_schema, seed, insert_order
from app.agents.orchestrator import screen_order
from app.models import ScreeningVerdict

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


def _load_all_cases():
    with open(DATA_DIR / "test_cases.json") as f:
        return json.load(f)


def _seed_linked_orders(conn, case):
    if "linked_order" in case:
        lo = case["linked_order"]
        insert_order(conn, lo["order_id"], lo["customer_id"], lo["sequences"])
    if "linked_orders" in case:
        for lo in case["linked_orders"]:
            insert_order(conn, lo["order_id"], lo["customer_id"], lo["sequences"])


CASES = _load_all_cases()


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_pipeline_matches_expected_flag(conn, case):
    _seed_linked_orders(conn, case)

    order = case["order"] | {"customer_id": case["customer_id"]}
    report = screen_order(conn, order)

    actual_flag = report.verdict != ScreeningVerdict.CLEAR
    expected_flag = case["expected"]["flag"]

    assert actual_flag == expected_flag, (
        f"{case['case_id']} ({case['description']}): "
        f"expected flag={expected_flag}, got verdict={report.verdict.value} "
        f"with signals={[s.description for s in report.signals]}"
    )


def test_all_ten_cases_present():
    """Guard against silently losing test cases if test_cases.json changes."""
    assert len(CASES) == 10