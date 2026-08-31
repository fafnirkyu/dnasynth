from difflib import SequenceMatcher

from app.models import Signal, RiskTier, SignalSource

EXACT_MATCH_THRESHOLD = 0.999
NEAR_MISS_THRESHOLD = 0.85


def _best_local_similarity(order_seq: str, hazard_seq: str) -> float | None:
    """
    Checks whether the full hazard sequence appears (exactly or near-exactly)
    somewhere within the submitted sequence, by sliding a hazard-length
    window across the submitted sequence.

    Returns None if the submitted sequence is SHORTER than the hazard
    sequence - a short fragment trivially "matching" some window of a
    longer hazard sequence is not a meaningful signal (any sub-fragment of
    a hazard sequence would match its own corresponding window). Catching
    fragments that only become hazardous when combined across orders is the
    Fragmentation Agent's job, not this one's - conflating the two would
    silently defeat the fragmentation test cases.
    """
    order_seq = order_seq.upper()
    hazard_seq = hazard_seq.upper()

    if len(order_seq) < len(hazard_seq):
        return None

    if len(order_seq) == len(hazard_seq):
        return SequenceMatcher(None, order_seq, hazard_seq).ratio()

    best = 0.0
    window_len = len(hazard_seq)
    for start in range(0, len(order_seq) - window_len + 1):
        window = order_seq[start:start + window_len]
        ratio = SequenceMatcher(None, window, hazard_seq).ratio()
        best = max(best, ratio)
    return best


def screen_sequences(sequences: list[str], hazard_bank: list[dict]) -> list[Signal]:
    """
    Args:
        sequences: raw sequence strings from a single order
        hazard_bank: list of dicts as returned by db.get_all_hazard_sequences()
    Returns:
        list of Signals - empty if nothing found
    """
    signals: list[Signal] = []

    for seq in sequences:
        for hazard in hazard_bank:
            similarity = _best_local_similarity(seq, hazard["sequence"])
            if similarity is None:
                continue

            if similarity >= EXACT_MATCH_THRESHOLD:
                signals.append(Signal(
                    source=SignalSource.SEQUENCE_SCREEN,
                    description=f"Submitted sequence matches known hazard sequence {hazard['hazard_id']}",
                    risk_tier=RiskTier(hazard["risk_tier"]),
                    confidence=1.0,
                    evidence={
                        "hazard_id": hazard["hazard_id"],
                        "similarity": round(similarity, 3),
                        "matched_submitted_sequence": seq,
                    }
                ))
            elif similarity >= NEAR_MISS_THRESHOLD:
                signals.append(Signal(
                    source=SignalSource.SEQUENCE_SCREEN,
                    description=(
                        f"Submitted sequence has high similarity ({similarity:.2f}) to "
                        f"hazard sequence {hazard['hazard_id']} but is below the exact-match threshold"
                    ),
                    risk_tier=RiskTier.MEDIUM,
                    confidence=round(similarity, 3),
                    evidence={
                        "hazard_id": hazard["hazard_id"],
                        "similarity": round(similarity, 3),
                        "matched_submitted_sequence": seq,
                    }
                ))

    return signals