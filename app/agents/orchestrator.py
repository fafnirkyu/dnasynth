"""
Orchestrator.

Responsibility: run all three specialist agents against an order, collect
their Signals, and turn that combined evidence into a single
ScreeningReport with a verdict. This is the ONLY place a verdict is ever
decided - none of the specialist agents can produce one themselves (see
models.py design notes).

Also records a structured trajectory of the run (which agent ran, what it
was given, what it returned) - this is what backs the hackathon's
"Agent trajectories" deliverable. See eval/dump_trajectories.py for
generating representative examples to submit.

Verdict logic (v1, deterministic - see conversation notes for planned
LLM-enhanced v2):
  - Any HIGH risk_tier signal, from any source -> ESCALATE
  - Otherwise, any MEDIUM risk_tier signal -> REVIEW_RECOMMENDED
  - No signals at all -> CLEAR

This is a simple max-severity rule, not a weighted score. Documented
trade-off: a weighted/LLM-based approach could better handle a case with
several medium signals that collectively warrant escalation even without
a single high signal - flagged as a known limitation and the intended
next iteration.
"""

from datetime import datetime, timezone

from app.db import get_all_hazard_sequences
from app.agents.sequence_screen import screen_sequences
from app.agents.fragmentation import check_fragmentation
from app.agents.customer_verify import verify_customer
from app.models import Signal, ScreeningReport, ScreeningVerdict, RiskTier


def _verdict_from_signals(signals: list[Signal]) -> ScreeningVerdict:
    if any(s.risk_tier == RiskTier.HIGH for s in signals):
        return ScreeningVerdict.ESCALATE
    if any(s.risk_tier == RiskTier.MEDIUM for s in signals):
        return ScreeningVerdict.REVIEW_RECOMMENDED
    return ScreeningVerdict.CLEAR


def _summarize(order_id: str, verdict: ScreeningVerdict, signals: list[Signal]) -> str:
    if not signals:
        return f"Order {order_id}: no risk signals detected across sequence, fragmentation, or customer checks."

    by_source: dict[str, int] = {}
    for s in signals:
        by_source[s.source.value] = by_source.get(s.source.value, 0) + 1

    parts = [f"{count} from {source}" for source, count in by_source.items()]
    return (
        f"Order {order_id}: verdict={verdict.value}. "
        f"{len(signals)} signal(s) found ({', '.join(parts)})."
    )


def screen_order(conn, order: dict) -> ScreeningReport:
    """
    Args:
        conn: sqlite3 connection
        order: dict with keys order_id, customer_id, sequences
    """
    report, _trajectory = screen_order_with_trajectory(conn, order)
    return report


def screen_order_with_trajectory(conn, order: dict) -> tuple[ScreeningReport, dict]:
    """
    Same as screen_order, but also returns a structured trajectory record:
    which agent ran, what it was given, what it returned, and how long it
    took. This is what backs the hackathon's "Agent trajectories"
    deliverable - each step here is traceable from the agent's instructions
    (this docstring/module) to its tool call (the DB query) to its output
    (the Signal list) to the orchestrator's next step (aggregation).
    """
    started_at = datetime.now(timezone.utc).isoformat()
    steps: list[dict] = []

    hazard_bank = get_all_hazard_sequences(conn)
    steps.append({
        "step": "load_hazard_bank",
        "tool": "db.get_all_hazard_sequences",
        "result_summary": f"{len(hazard_bank)} hazard sequences loaded",
    })

    signals: list[Signal] = []

    seq_signals = screen_sequences(order["sequences"], hazard_bank)
    steps.append({
        "agent": "sequence_screen",
        "input": {"sequences": order["sequences"]},
        "output_signal_count": len(seq_signals),
        "output_signals": [s.model_dump(mode="json") for s in seq_signals],
    })
    signals.extend(seq_signals)

    frag_signals = check_fragmentation(conn, order, hazard_bank)
    steps.append({
        "agent": "fragmentation",
        "input": {"order_id": order["order_id"], "customer_id": order["customer_id"]},
        "tool_calls": ["db.get_order_history_for_customer", "db.get_customers_at_same_address"],
        "output_signal_count": len(frag_signals),
        "output_signals": [s.model_dump(mode="json") for s in frag_signals],
    })
    signals.extend(frag_signals)

    cust_signals = verify_customer(conn, order["customer_id"])
    steps.append({
        "agent": "customer_verify",
        "input": {"customer_id": order["customer_id"]},
        "tool_calls": ["db.get_customer", "db.is_customer_denylisted"],
        "output_signal_count": len(cust_signals),
        "output_signals": [s.model_dump(mode="json") for s in cust_signals],
    })
    signals.extend(cust_signals)

    verdict = _verdict_from_signals(signals)
    report = ScreeningReport(
        order_id=order["order_id"],
        customer_id=order["customer_id"],
        verdict=verdict,
        signals=signals,
        summary=_summarize(order["order_id"], verdict, signals),
    )

    steps.append({
        "step": "orchestrator_verdict",
        "logic": "max_severity_rule",
        "total_signals": len(signals),
        "verdict": verdict.value,
    })

    trajectory = {
        "order_id": order["order_id"],
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "steps": steps,
        "final_verdict": verdict.value,
        "human_checkpoint": "This report is advisory. requires_human_review is always True; "
                             "no code path in this system can auto-approve or auto-reject an order.",
    }

    return report, trajectory