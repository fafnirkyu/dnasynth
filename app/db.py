"""
SQLite persistence layer.

This is what gives the Fragmentation Agent its "memory" - without a
persistent order/customer history, there is no way to notice that two
separate orders (possibly from two different customer_ids) combine into
a hazard sequence. A single-prompt baseline with no DB access structurally
cannot do this, which is exactly the comparison point for the hackathon.

Design notes:
- One SQLite file, created fresh from the synthetic data/ files each run
  (via `seed()`) - keeps the eval fully reproducible from a clean environment.
- Orders are stored with sequences as a JSON-encoded list in a TEXT column
  rather than a separate normalized table - fine for hackathon scale
  (dozens of orders), keeps queries simple.
- Customer address clustering (needed for the adversarial TC-08 case) is
  done with a simple exact/normalized string match on shipping_address,
  not fuzzy matching - deliberate scope cut, documented as a known
  limitation for the changelog / hot take.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "screening.db"
DATA_DIR = Path(__file__).parent.parent / "data"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            institution_name TEXT NOT NULL,
            email_domain TEXT NOT NULL,
            shipping_address TEXT NOT NULL,
            shipping_address_normalized TEXT NOT NULL,
            declared_purpose TEXT,
            account_age_days INTEGER,
            denylist_flag INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            sequences_json TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE TABLE IF NOT EXISTS hazard_sequences (
            hazard_id TEXT PRIMARY KEY,
            sequence TEXT NOT NULL,
            length INTEGER NOT NULL,
            label TEXT,
            risk_tier TEXT NOT NULL,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS denylist (
            entity_name TEXT NOT NULL,
            identifier_type TEXT NOT NULL,
            identifier_value TEXT NOT NULL,
            list_source TEXT,
            note TEXT
        );
    """)
    conn.commit()


def _normalize_address(address: str) -> str:
    """
    Crude normalization so '9 Rural Route 2, Unit B' and '9 Rural Route 2,
    Unit C' can still be clustered on their shared base address.
    Strips unit/suite fragments. Documented limitation: this is naive
    string handling, not a real address-parsing library - fine for
    synthetic data, would need a real geocoding/parsing step in production.
    """
    lowered = address.lower()
    for marker in ["unit ", "suite ", "ste ", "#"]:
        if marker in lowered:
            lowered = lowered.split(marker)[0]
    return lowered.strip().rstrip(",").strip()


def seed(conn: sqlite3.Connection) -> None:
    """Wipe and reload all tables from the synthetic data/ files."""
    conn.executescript("""
        DELETE FROM customers;
        DELETE FROM orders;
        DELETE FROM hazard_sequences;
        DELETE FROM denylist;
    """)

    with open(DATA_DIR / "synthetic_customers.json") as f:
        customers = json.load(f)
    for c in customers:
        conn.execute(
            """INSERT INTO customers
               (customer_id, institution_name, email_domain, shipping_address,
                shipping_address_normalized, declared_purpose, account_age_days, denylist_flag)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (c["customer_id"], c["institution_name"], c["email_domain"],
             c["shipping_address"], _normalize_address(c["shipping_address"]),
             c["declared_purpose"], c["account_age_days"], int(c["denylist_flag"]))
        )

    with open(DATA_DIR / "synthetic_hazard_sequences.json") as f:
        hazards = json.load(f)
    for h in hazards:
        conn.execute(
            """INSERT INTO hazard_sequences (hazard_id, sequence, length, label, risk_tier, note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (h["hazard_id"], h["sequence"], h["length"], h["label"], h["risk_tier"], h["note"])
        )

    import csv
    with open(DATA_DIR / "synthetic_denylist.csv") as f:
        reader = csv.DictReader(f)
        for row in reader:
            conn.execute(
                """INSERT INTO denylist (entity_name, identifier_type, identifier_value, list_source, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (row["entity_name"], row["identifier_type"], row["identifier_value"],
                 row["list_source"], row["note"])
            )

    conn.commit()


# --- Query helpers used by the agents ---

def get_customer(conn: sqlite3.Connection, customer_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()


def get_all_customers(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM customers ORDER BY institution_name").fetchall()
    return [dict(r) for r in rows]


def get_customers_at_same_address(conn: sqlite3.Connection, customer_id: str) -> list[sqlite3.Row]:
    """Find other customers sharing a normalized shipping address - this is
    what powers the TC-08 adversarial case (different customer_ids, same
    address cluster)."""
    customer = get_customer(conn, customer_id)
    if not customer:
        return []
    return conn.execute(
        """SELECT * FROM customers
           WHERE shipping_address_normalized = ? AND customer_id != ?""",
        (customer["shipping_address_normalized"], customer_id)
    ).fetchall()


def insert_order(conn: sqlite3.Connection, order_id: str, customer_id: str,
                  sequences: list[str], submitted_at: datetime | None = None) -> None:
    conn.execute(
        "INSERT INTO orders (order_id, customer_id, sequences_json, submitted_at) VALUES (?, ?, ?, ?)",
        (order_id, customer_id, json.dumps(sequences), (submitted_at or datetime.utcnow()).isoformat())
    )
    conn.commit()


def get_order_history_for_customer(conn: sqlite3.Connection, customer_id: str,
                                    exclude_order_id: str | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM orders WHERE customer_id = ?", (customer_id,)
    ).fetchall()
    result = []
    for r in rows:
        if exclude_order_id and r["order_id"] == exclude_order_id:
            continue
        result.append({
            "order_id": r["order_id"],
            "customer_id": r["customer_id"],
            "sequences": json.loads(r["sequences_json"]),
            "submitted_at": r["submitted_at"],
        })
    return result


def get_all_hazard_sequences(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM hazard_sequences").fetchall()
    return [dict(r) for r in rows]


def is_customer_denylisted(conn: sqlite3.Connection, customer_id: str) -> bool:
    customer = get_customer(conn, customer_id)
    if not customer:
        return False
    if customer["denylist_flag"]:
        return True
    match = conn.execute(
        """SELECT 1 FROM denylist
           WHERE (identifier_type = 'email_domain' AND identifier_value = ?)
              OR (identifier_type = 'institution_name' AND identifier_value = ?)""",
        (customer["email_domain"], customer["institution_name"])
    ).fetchone()
    return match is not None