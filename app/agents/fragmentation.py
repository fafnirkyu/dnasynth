"""
Fragmentation Agent.

Responsibility: detect when a hazard sequence has been split across
multiple orders - either from the same customer, or from different
customer accounts that share a shipping-address cluster (the shell-company
pattern) - such that no single order contains the full hazard sequence,
but the concatenation of fragments does.

This is the agent that requires persistent memory (order history in
SQLite) and cross-order reasoning. A stateless single-prompt baseline has
no access to this history and cannot catch this pattern - that's the
central comparison point for the hackathon submission.

Scope cuts (documented, not oversights):
- Exact-match concatenation only. A production system would also score
  near-exact reconstructions (a few mismatches after concatenation); we
  only check exact equality here since our synthetic test cases split
  hazard sequences cleanly.
- Brute-force permutation search over the fragment pool (size 2 and 3
  combinations). This is fine at hackathon scale (a handful of orders per
  customer/cluster) but would NOT scale to a real production order volume -
  a real system would need smarter candidate generation (e.g. k-mer
  indexing) rather than trying every permutation. Called out explicitly
  as a known limitation / hot take.
"""

from itertools import permutations

from app.db import get_order_history_for_customer, get_customers_at_same_address
from app.models import Signal, RiskTier, SignalSource

MAX_COMBO_SIZE = 3  # covers 2-part and 3-part fragmentation; documented scope cut above


def _build_fragment_pool(conn, current_order: dict) -> list[tuple[str, str, str]]:
    """
    Returns a list of (order_id, customer_id, sequence) tuples drawn from:
      - the order currently being screened
      - that customer's prior order history
      - the order history of any customer sharing a shipping-address cluster
    """
    customer_id = current_order["customer_id"]
    pool: list[tuple[str, str, str]] = [
        (current_order["order_id"], customer_id, seq)
        for seq in current_order["sequences"]
    ]

    same_customer_history = get_order_history_for_customer(
        conn, customer_id, exclude_order_id=current_order["order_id"]
    )
    for h in same_customer_history:
        for seq in h["sequences"]:
            pool.append((h["order_id"], customer_id, seq))

    for related in get_customers_at_same_address(conn, customer_id):
        related_history = get_order_history_for_customer(conn, related["customer_id"])
        for h in related_history:
            for seq in h["sequences"]:
                pool.append((h["order_id"], related["customer_id"], seq))

    return pool


def check_fragmentation(conn, current_order: dict, hazard_bank: list[dict]) -> list[Signal]:
    """
    Args:
        conn: sqlite3 connection
        current_order: dict with keys order_id, customer_id, sequences
        hazard_bank: list of dicts as returned by db.get_all_hazard_sequences()
    """
    pool = _build_fragment_pool(conn, current_order)
    signals: list[Signal] = []
    seen_matches: set[tuple[str, frozenset]] = set()

    n = len(pool)
    for combo_size in range(2, min(MAX_COMBO_SIZE, n) + 1):
        for combo in permutations(range(n), combo_size):
            order_ids = {pool[i][0] for i in combo}
            if len(order_ids) < 2:
                # All fragments came from the same order - not cross-order
                # fragmentation, and already covered by the Sequence
                # Screening Agent.
                continue

            concatenated = "".join(pool[i][2] for i in combo).upper()

            for hazard in hazard_bank:
                if concatenated != hazard["sequence"].upper():
                    continue

                match_key = (hazard["hazard_id"], frozenset(order_ids))
                if match_key in seen_matches:
                    continue
                seen_matches.add(match_key)

                customer_ids = sorted({pool[i][1] for i in combo})
                reason = (
                    "fragmentation_same_customer"
                    if len(customer_ids) == 1
                    else "fragmentation_related_customers"
                )

                signals.append(Signal(
                    source=SignalSource.FRAGMENTATION,
                    description=(
                        f"Sequences from orders {sorted(order_ids)} concatenate exactly "
                        f"to reconstruct hazard sequence {hazard['hazard_id']}"
                    ),
                    risk_tier=RiskTier(hazard["risk_tier"]),
                    confidence=1.0,
                    evidence={
                        "hazard_id": hazard["hazard_id"],
                        "order_ids": sorted(order_ids),
                        "customer_ids": customer_ids,
                        "reason": reason,
                    }
                ))

    return signals