"""
Eval harness: runs the baseline (single Gemini prompt, no memory, no
structured verification) and the full agentic orchestrator pipeline over
every case in data/test_cases.json, and reports the comparison table the
hackathon rubric asks for (baseline vs. agent, same cases, same evaluation
method).

Requires a live Gemini API key (GOOGLE_API_KEY or GEMINI_API_KEY) - loaded
from a .env file via python-dotenv if present - since run_baseline() makes
a real API call per case. This is NOT mocked, unlike tests/test_baseline.py.

Each case gets a FRESH, freshly-seeded database before it runs, so a
case's linked orders never leak into a later case's fragmentation pool -
this mirrors the per-case isolation used in tests/test_pipeline.py.

Usage:
    python -m eval.run_eval
"""
import json
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from app.db import get_connection, init_schema, seed, insert_order, get_customer
from app.agents.orchestrator import screen_order
from app.baseline import run_baseline, BaselineUnavailableError
from app.models import ScreeningVerdict

DATA_DIR = Path(__file__).parent.parent / "data"
RESULTS_PATH = Path(__file__).parent / "results.json"

# Free-tier Gemini quota is 5 requests/minute per model. Space calls out to
# stay comfortably under that instead of relying on retries to recover
# after the fact - retries alone can't fix a hard per-minute cap; only
# pacing can. 13s gives a small margin over the strict 12s (60/5) minimum.
MIN_SECONDS_BETWEEN_BASELINE_CALLS = 13.0


def _load_cases() -> list[dict]:
    with open(DATA_DIR / "test_cases.json") as f:
        return json.load(f)


def _seed_linked_orders(conn, case: dict) -> None:
    if "linked_order" in case:
        lo = case["linked_order"]
        insert_order(conn, lo["order_id"], lo["customer_id"], lo["sequences"])
    if "linked_orders" in case:
        for lo in case["linked_orders"]:
            insert_order(conn, lo["order_id"], lo["customer_id"], lo["sequences"])


def run() -> dict:
    cases = _load_cases()
    results = []
    agent_correct = 0
    baseline_correct = 0
    baseline_attempted = 0
    agent_total_time = 0.0
    baseline_total_time = 0.0
    last_baseline_call_time: float | None = None

    print(f"Running {len(cases)} cases. Baseline calls are paced ~{MIN_SECONDS_BETWEEN_BASELINE_CALLS:.0f}s apart "
          f"to respect the free-tier rate limit - this will take a few minutes.\n")

    for case in cases:
        # Fresh, isolated DB per case - prevents one case's linked orders
        # from leaking into another case's fragmentation pool.
        conn = get_connection()
        init_schema(conn)
        seed(conn)
        _seed_linked_orders(conn, case)

        order = case["order"] | {"customer_id": case["customer_id"]}
        expected_flag = case["expected"]["flag"]

        t0 = time.perf_counter()
        report = screen_order(conn, order)
        agent_total_time += time.perf_counter() - t0
        agent_flag = report.verdict != ScreeningVerdict.CLEAR
        agent_match = agent_flag == expected_flag
        agent_correct += int(agent_match)

        baseline_flag = None
        baseline_match = None
        customer = get_customer(conn, order["customer_id"])
        try:
            if last_baseline_call_time is not None:
                elapsed = time.perf_counter() - last_baseline_call_time
                remaining = MIN_SECONDS_BETWEEN_BASELINE_CALLS - elapsed
                if remaining > 0:
                    print(f"  (pacing: waiting {remaining:.0f}s before next baseline call...)")
                    time.sleep(remaining)

            t0 = time.perf_counter()
            last_baseline_call_time = t0  # set before the call so a failed attempt still paces the next one
            baseline_result = run_baseline(order, dict(customer))
            baseline_total_time += time.perf_counter() - t0
            baseline_flag = baseline_result["verdict"] == ScreeningVerdict.ESCALATE.value
            baseline_match = baseline_flag == expected_flag
            baseline_attempted += 1
            baseline_correct += int(baseline_match)
        except (RuntimeError, BaselineUnavailableError) as e:
            print(f"[warn] Baseline skipped for {case['case_id']}: {e}")

        results.append({
            "case_id": case["case_id"],
            "description": case["description"],
            "expected_flag": expected_flag,
            "agent_flag": agent_flag,
            "agent_correct": agent_match,
            "baseline_flag": baseline_flag,
            "baseline_correct": baseline_match,
        })
        conn.close()

    n = len(cases)
    print(f"{'Case':<8}{'Expected':<10}{'Agent':<8}{'Base':<8}{'AgentOK':<10}{'BaseOK'}")
    for r in results:
        print(f"{r['case_id']:<8}{str(r['expected_flag']):<10}{str(r['agent_flag']):<8}"
              f"{str(r['baseline_flag']):<8}{str(r['agent_correct']):<10}{str(r['baseline_correct'])}")

    print()
    print(f"Agent accuracy:    {agent_correct}/{n} ({agent_correct/n:.0%})")
    if baseline_attempted:
        print(f"Baseline accuracy: {baseline_correct}/{baseline_attempted} "
              f"({baseline_correct/baseline_attempted:.0%}) [{baseline_attempted}/{n} cases attempted]")
    else:
        print("Baseline accuracy: N/A (no API key set - see .env)")
    print(f"Agent avg time/case:    {agent_total_time/n*1000:.1f} ms")
    if baseline_attempted:
        print(f"Baseline avg time/case: {baseline_total_time/baseline_attempted*1000:.1f} ms")

    summary = {
        "results": results,
        "agent_accuracy": agent_correct / n,
        "baseline_accuracy": (baseline_correct / baseline_attempted) if baseline_attempted else None,
        "baseline_cases_attempted": baseline_attempted,
        "total_cases": n,
    }
    with open(RESULTS_PATH, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSaved detailed results to {RESULTS_PATH}")
    return summary


if __name__ == "__main__":
    run()