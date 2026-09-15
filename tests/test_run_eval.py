"""
Structural smoke test for eval/run_eval.py - mocks run_baseline entirely
(no live API calls in the test suite) to confirm the per-case DB isolation
and result-aggregation logic are wired correctly. The REAL Gemini-backed
numbers can only come from `python -m eval.run_eval` with a live API key.
"""
import sqlite3
from unittest.mock import patch

from eval.run_eval import run
from app.models import ScreeningVerdict


@patch("eval.run_eval.run_baseline")
def test_run_eval_structure_with_mocked_baseline(mock_run_baseline, monkeypatch, tmp_path):
    """
    Mock the baseline to always say CLEAR - a deliberately weak baseline,
    same spirit as the real single-prompt one, so we can confirm: (a) the
    script runs all 10 cases without crashing, (b) per-case DB isolation
    works (no leaked history changes an outcome), (c) result aggregation
    counts and JSON output are internally consistent.
    """
    mock_run_baseline.return_value = {
        "order_id": "mock", "verdict": ScreeningVerdict.CLEAR.value, "raw_response": "CLEAR"
    }

    # A mocked baseline needs neither live-API pacing nor permission to
    # overwrite the checked-in results or the regular screening database.
    results_path = tmp_path / "mock_results.json"
    db_path = tmp_path / "mock_screening.db"

    def isolated_connection():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr("eval.run_eval.MIN_SECONDS_BETWEEN_BASELINE_CALLS", 0.0)
    monkeypatch.setattr("eval.run_eval.RESULTS_PATH", results_path)
    monkeypatch.setattr("eval.run_eval.get_connection", isolated_connection)

    summary = run()

    assert results_path.exists()

    assert summary["total_cases"] == 10
    assert summary["baseline_cases_attempted"] == 10
    assert 0.0 <= summary["agent_accuracy"] <= 1.0
    assert len(summary["results"]) == 10

    # The agent should meaningfully outperform an always-CLEAR baseline,
    # since several cases expect flag=True.
    assert summary["agent_accuracy"] > summary["baseline_accuracy"]

    # Fragmentation cases specifically should be ones where the agent is
    # correct and the always-CLEAR baseline is not - this is the whole
    # point of the project.
    for r in summary["results"]:
        if r["case_id"] in ("TC-06", "TC-07", "TC-08"):
            assert r["agent_correct"] is True
            assert r["baseline_correct"] is False
