"""
FastAPI entrypoint for the DNA Synthesis Screening Assistant.

Endpoints:
  GET  /health           - liveness check
  POST /orders/screen     - screen an order, returns a ScreeningReport
  POST /admin/reset       - wipe and reseed with synthetic data (demo/dev only)

Design note on startup seeding: we do NOT reseed on every server start.
Seeding wipes the orders table, which would destroy the order history the
Fragmentation Agent depends on for cross-order memory - restarting the
server would otherwise silently erase the system's ability to catch
fragmented orders submitted before the restart. We only seed automatically
if the customers table is empty (i.e. this is truly a fresh database).
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_connection, init_schema, seed, insert_order, get_all_customers
from app.agents.orchestrator import screen_order
from app.models import ScreeningReport

STATIC_DIR = Path(__file__).parent / "static"


class OrderRequest(BaseModel):
    order_id: str
    customer_id: str
    sequences: list[str]


def _is_db_empty(conn) -> bool:
    row = conn.execute("SELECT COUNT(*) as c FROM customers").fetchone()
    return row["c"] == 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = get_connection()
    init_schema(conn)
    if _is_db_empty(conn):
        seed(conn)
    conn.close()
    yield


app = FastAPI(
    title="DNA Synthesis Screening Assistant",
    description="Hackathon compliance agent - screens oligo pool orders for "
                "biosecurity risk signals. All output is advisory; a human "
                "reviewer makes the final call on every flagged order.",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
def root():
    """Serves the demo console UI. The API itself is still fully explorable
    at /docs - this is just a more presentable way to exercise it."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/customers")
def list_customers():
    """Read-only list of known customers, used to populate the demo UI's
    customer dropdown. Not something a real deployment would expose
    unauthenticated alongside institution names in production."""
    conn = get_connection()
    try:
        return get_all_customers(conn)
    finally:
        conn.close()


@app.post("/orders/screen", response_model=ScreeningReport)
def screen(order: OrderRequest):
    """
    Screens an order and returns a ScreeningReport. The order is recorded
    into history AFTER screening (not before), so this order's own
    sequences are correctly excluded from its own fragmentation check -
    but are available for any FUTURE order's fragmentation check.
    """
    conn = get_connection()
    try:
        report = screen_order(conn, order.model_dump())
        insert_order(conn, order.order_id, order.customer_id, order.sequences)
        return report
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@app.post("/admin/reset")
def reset():
    conn = get_connection()
    try:
        init_schema(conn)
        seed(conn)
        return {"status": "reset"}
    finally:
        conn.close()