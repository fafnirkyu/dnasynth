"""
Tests for the Sequence Screening Agent, validated directly against
data/test_cases.json so we're testing against the real hackathon eval
scenarios, not synthetic examples invented separately.
"""
import json
from pathlib import Path

from app.agents.sequence_screen import screen_sequences

DATA_DIR = Path(__file__).parent.parent / "data"


def _load_hazards():
    with open(DATA_DIR / "synthetic_hazard_sequences.json") as f:
        return json.load(f)


def _load_case(case_id: str):
    with open(DATA_DIR / "test_cases.json") as f:
        cases = json.load(f)
    return next(c for c in cases if c["case_id"] == case_id)


def test_clean_order_produces_no_signals():
    case = _load_case("TC-01")
    signals = screen_sequences(case["order"]["sequences"], _load_hazards())
    assert signals == []


def test_direct_hazard_match_verbatim():
    case = _load_case("TC-04")
    signals = screen_sequences(case["order"]["sequences"], _load_hazards())
    assert len(signals) == 1
    assert signals[0].confidence == 1.0
    assert signals[0].evidence["hazard_id"] == case["expected"]["hazard_id"]


def test_direct_hazard_match_embedded_in_longer_strand():
    case = _load_case("TC-05")
    signals = screen_sequences(case["order"]["sequences"], _load_hazards())
    assert len(signals) == 1
    assert signals[0].confidence == 1.0
    assert signals[0].evidence["hazard_id"] == case["expected"]["hazard_id"]


def test_near_miss_below_threshold_produces_no_signal():
    """
    TC-10: sequence shares 20/30 bases positionally with a hazard sequence
    but is expected to fall BELOW the near-miss threshold and not flag at
    all - this is the false-positive control case.
    """
    case = _load_case("TC-10")
    signals = screen_sequences(case["order"]["sequences"], _load_hazards())
    assert signals == [], f"Expected no signal, got: {signals}"


def test_fragmented_half_does_not_trigger_direct_match_alone():
    """
    Sanity check that a lone fragment (half of HZ-001) does NOT get flagged
    by this agent in isolation - proving the fragmentation catch in TC-06
    genuinely requires the Fragmentation Agent's cross-order memory, not
    this agent accidentally catching it.
    """
    case = _load_case("TC-06")
    signals = screen_sequences(case["order"]["sequences"], _load_hazards())
    assert signals == [], (
        "Sequence screening agent should NOT catch this alone - "
        "if it does, TC-06 no longer demonstrates the fragmentation gap."
    )