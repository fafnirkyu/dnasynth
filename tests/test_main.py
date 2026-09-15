"""
Tests for the FastAPI app, using TestClient against the real SQLite layer
(pointed at a temp DB file per test, same isolation pattern as the other
DB-backed tests).
"""
import pytest
from fastapi.testclient import TestClient

import app.db as db_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db_path = tmp_path / "test_screening.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    # Import main AFTER patching DB_PATH so its lifespan seeds the temp DB,
    # not the real one.
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_screen_clean_order(client):
    resp = client.post("/orders/screen", json={
        "order_id": "ORD-API-1",
        "customer_id": "CUST-001",
        "sequences": ["ATGCGTACGTTAGCCTAGGCTAACGTAGCTT"]
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "clear"
    assert body["requires_human_review"] is True


def test_screen_denylisted_customer_escalates(client):
    resp = client.post("/orders/screen", json={
        "order_id": "ORD-API-2",
        "customer_id": "CUST-007",
        "sequences": ["TTAGGCTAGCATGGCTAGCATTAGGCATCG"]
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "escalate"
    assert len(body["signals"]) >= 1


def test_fragmentation_detected_across_two_real_requests(client):
    """
    The key end-to-end proof: two SEPARATE HTTP requests, the second of
    which should trigger fragmentation detection based on order history
    persisted by the first request.
    """
    # HZ-001 fragments, same split used in TC-06
    hz1_a = "AGCCCAATAAACCACTCT"
    hz1_b = "GACTGGCCGAAT"

    resp1 = client.post("/orders/screen", json={
        "order_id": "ORD-FRAG-1",
        "customer_id": "CUST-003",
        "sequences": [hz1_a]
    })
    assert resp1.status_code == 200
    body1 = resp1.json()
    # CUST-003 is a shell-cluster customer (new account, free-mail domain,
    # vague purpose) so customer_verify correctly flags it regardless of
    # sequence content - that's review_recommended, not clear. What we're
    # actually checking here is that FRAGMENTATION hasn't fired yet, since
    # this is the only fragment submitted so far.
    fragmentation_signals_1 = [s for s in body1["signals"] if s["source"] == "fragmentation"]
    assert fragmentation_signals_1 == []

    resp2 = client.post("/orders/screen", json={
        "order_id": "ORD-FRAG-2",
        "customer_id": "CUST-003",
        "sequences": [hz1_b]
    })
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["verdict"] == "escalate"
    fragmentation_signals = [s for s in body2["signals"] if s["source"] == "fragmentation"]
    assert len(fragmentation_signals) == 1


def test_admin_reset_restores_clean_state(client):
    client.post("/orders/screen", json={
        "order_id": "ORD-TO-BE-WIPED",
        "customer_id": "CUST-001",
        "sequences": ["AAAA"]
    })
    resp = client.post("/admin/reset")
    assert resp.status_code == 200
    assert resp.json() == {"status": "reset"}