"""
Tests for the baseline. Mocks the Gemini API call entirely (via the
google-genai Client) - we're testing our own parsing/plumbing logic, not
Gemini itself, and we don't want the test suite to require a live API key
or burn quota on every run.
"""
from unittest.mock import patch, MagicMock

import pytest

from app.baseline import run_baseline
from app.models import ScreeningVerdict


def _mock_response(text: str):
    mock_resp = MagicMock()
    mock_resp.text = text
    return mock_resp


@patch("app.baseline._get_client")
def test_baseline_parses_suspicious_response(mock_get_client):
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _mock_response("SUSPICIOUS")
    mock_get_client.return_value = mock_client

    order = {"order_id": "ORD-1", "customer_id": "CUST-1", "sequences": ["ACGT"]}
    customer = {"institution_name": "Test Inc", "declared_purpose": "research"}

    result = run_baseline(order, customer)
    assert result["verdict"] == ScreeningVerdict.ESCALATE.value
    assert result["order_id"] == "ORD-1"


@patch("app.baseline._get_client")
def test_baseline_parses_clear_response(mock_get_client):
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _mock_response("CLEAR")
    mock_get_client.return_value = mock_client

    order = {"order_id": "ORD-2", "customer_id": "CUST-2", "sequences": ["TTTT"]}
    customer = {"institution_name": "Test Inc", "declared_purpose": "research"}

    result = run_baseline(order, customer)
    assert result["verdict"] == ScreeningVerdict.CLEAR.value


@patch("app.baseline._get_client")
def test_baseline_handles_messy_whitespace_and_case(mock_get_client):
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _mock_response("  suspicious.\n")
    mock_get_client.return_value = mock_client

    order = {"order_id": "ORD-3", "customer_id": "CUST-3", "sequences": ["GGGG"]}
    customer = {"institution_name": "Test Inc", "declared_purpose": "research"}

    result = run_baseline(order, customer)
    assert result["verdict"] == ScreeningVerdict.ESCALATE.value


def test_get_client_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from app.baseline import _get_client
    with pytest.raises(RuntimeError):
        _get_client()