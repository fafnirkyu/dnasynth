"""
Generates representative agent trajectories for the hackathon's
"Agent trajectories" deliverable: for a hand-picked subset of
data/test_cases.json covering each capability the system demonstrates,
records what each agent did, what its tools returned, and the final
orchestrator decision - traceable end to end.

Does NOT call any external API (the orchestrator itself is fully
rule-based/deterministic) - this is fast and free to run.

Usage:
    python -m eval.dump_trajectories
"""
import json
from pathlib import Path

from app.db import get_connection, init_schema, seed, insert_order
from app.agents.orchestrator import screen_order_with_trajectory

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_PATH = Path(__file__).parent / "trajectories.json"

# Hand-picked to cover each distinct capability, not just a random sample:
REPRESENTATIVE_CASE_IDS = [
    "TC-01",  # clean order - all three agents run, nothing fires
    "TC-04",  # direct hazard match - sequence_screen agent fires alone
    "TC-06",  # two-part fragmentation, same customer - fragmentation agent's core case
    "TC-08",  # adversarial: fragmentation across related-but-different customers
    "TC-09",  # denylisted customer, clean sequence - customer_verify agent fires alone
]


def _load_cases():
    with open(DATA_DIR / "test_cases.json") as f:
        all_cases = json.load(f)
    by_id = {c["case_id"]: c for c in all_cases}
    return [by_id[cid] for cid in REPRESENTATIVE_CASE_IDS]


def _seed_linked_orders(conn, case):
    if "linked_order" in case:
        lo = case["linked_order"]
        insert_order(conn, lo["order_id"], lo["customer_id"], lo["sequences"])
    if "linked_orders" in case:
        for lo in case["linked_orders"]:
            insert_order(conn, lo["order_id"], lo["customer_id"], lo["sequences"])


def run():
    conn = get_connection()
    init_schema(conn)
    seed(conn)

    trajectories = []
    for case in _load_cases():
        _seed_linked_orders(conn, case)
        order = case["order"] | {"customer_id": case["customer_id"]}
        _report, trajectory = screen_order_with_trajectory(conn, order)
        trajectory["case_id"] = case["case_id"]
        trajectory["case_description"] = case["description"]
        trajectories.append(trajectory)
        conn.close()
        # Fresh isolated connection per case, same reasoning as run_eval.py -
        # prevents one case's linked orders leaking into the next.
        conn = get_connection()
        init_schema(conn)
        seed(conn)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(trajectories, f, indent=2)

    print(f"Wrote {len(trajectories)} representative trajectories to {OUTPUT_PATH}")
    for t in trajectories:
        print(f"  {t['case_id']}: verdict={t['final_verdict']}")


if __name__ == "__main__":
    run()