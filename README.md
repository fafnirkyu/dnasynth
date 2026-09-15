# DNA Synthesis Screening Assistant

A compliance agent that screens oligo pool orders for biosecurity risk signals — sequence hazard matches, cross-order fragmentation, and customer identity risk — and produces an advisory report for a human biosecurity reviewer. Built for the micro1 Agentic Workflows Hackathon.

**All data in this project is synthetic.** Hazard sequences are random ACGT strings with no biological meaning, customers are fabricated, and the denylist is fabricated. This is intentional (see [Ground rules alignment](#ground-rules-alignment)) and required no data-sourcing time, by design.

## Who has this problem

A biosecurity/compliance reviewer at a DNA synthesis provider, or a screening service that plugs into one. Manual, single-order review doesn't scale, and a documented real-world risk is largely invisible to it: a hazardous sequence can be split across multiple smaller orders — from one customer over time, or from several customer accounts that are actually the same bad actor — so that no individual order looks dangerous, but the pieces reconstruct something that is.

## The bottleneck

Sequence-only screening catches a hazard sequence submitted whole. It cannot catch one that's been fragmented, because doing so requires:

- **Memory** across a customer's order history (not just the order in front of you)
- **Identity reasoning** to notice when two different customer accounts are actually related (e.g. a shell-company cluster sharing a shipping address)
- **Combinatorial checking** to find that fragments, concatenated, reconstruct something on the hazard list

A single LLM prompt asked "is this order suspicious?" has none of this. Our evaluation confirms it: see [Results](#results) below.

## Architecture

Four components, each single-purpose:

    ```
    Order → [Sequence Screening Agent]   → Signal(s)
        → [Fragmentation Agent]         → Signal(s)   →  [Orchestrator] → ScreeningReport
        → [Customer Verification Agent] → Signal(s)
    ```

- **Sequence Screening Agent** (`app/agents/sequence_screen.py`) — checks an order's sequences against a synthetic hazard bank for exact or embedded matches, plus a near-miss similarity threshold.
- **Fragmentation Agent** (`app/agents/fragmentation.py`) — the core "agentic" component. Pulls order history for the same customer *and* any customer sharing a shipping-address cluster (via SQLite, `app/db.py`), then searches fragment combinations for an exact hazard-sequence reconstruction. This is what a stateless baseline structurally cannot do.
- **Customer Verification Agent** (`app/agents/customer_verify.py`) — denylist matching plus a shell-account heuristic (new account age, free-mail domain, vague declared purpose).
- **Orchestrator** (`app/agents/orchestrator.py`) — the *only* place a verdict is decided. Runs all three agents, collects their `Signal`s, and applies a max-severity rule (any HIGH signal → escalate; else any MEDIUM → review recommended; else clear).

Every agent emits structured `Signal` objects (Pydantic, `app/models.py`) with a source, description, risk tier, confidence, and evidence dict — never a bare verdict. This keeps every escalation traceable to the exact signal(s) that caused it.

**Ground rules alignment:** `ScreeningReport.requires_human_review` is hardcoded `True` at the schema level — no code path can construct a report that skips human review, even by accident. The system never auto-approves or auto-rejects an order; it always produces an advisory recommendation.

## Results

Full breakdown and methodology in [`CHANGELOG.md`](CHANGELOG.md). Headline numbers from a live evaluation run against Gemini (`eval/results.json`):

| | Baseline (single prompt, no memory) | Agent (this system) |
|---|---|---|
| Overall accuracy (10 cases) | 50% | **100%** |
| Cross-order fragmentation cases | 0% | **100%** |
| Denylist-only case | 0% | **100%** |

The baseline matches the agent on benign orders, but drops to zero the instant a case requires information it structurally cannot have (a hazard reference bank, order memory, or denylist data) — that gap is the actual finding of this project, not the raw accuracy number.

## Project structure

```
app/
  models.py           # Pydantic schemas (Order, Customer, Signal, ScreeningReport)
  db.py               # SQLite persistence - order history, customer records, hazard bank, denylist
  baseline.py         # single-prompt Gemini baseline for comparison
  main.py             # FastAPI app
  agents/
    sequence_screen.py
    fragmentation.py
    customer_verify.py
    orchestrator.py
data/                 # synthetic hazard sequences, customers, denylist, test cases
eval/
  run_eval.py         # baseline vs. agent comparison harness
  results.json        # real output from the last live eval run
tests/                # pytest suite - unit tests per module + full pipeline integration test
CHANGELOG.md          # improvement changelog with real bugs, decisions, and evidence
Dockerfile / docker-compose.yml
```

GitHub Actions runs the offline test suite on every push and pull request.
The mocked baseline test uses a temporary database and results file; it does
not replace your local `data/screening.db` or the checked-in Gemini-backed
`eval/results.json`. For a lightweight
local reproduction of CI, install `requirements-ci.txt` in Python 3.11 and
run `python -m pytest -q`.

## Reproduction guide

### Requirements

- Python 3.11+
- A Gemini API key (free tier works) — get one at [ai.google.dev](https://ai.google.dev)

### Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:
```
GOOGLE_API_KEY=your_key_here
```

### Run the tests (no API key required — the baseline is mocked)

```bash
pytest -v
```

Expected: all tests pass (57 at time of writing) in a few seconds.

### Run the API

```bash
uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000/docs` for interactive API docs. Try `POST /orders/screen` with:
```json
{"order_id": "ORD-DEMO-1", "customer_id": "CUST-001", "sequences": ["ATGCGTACGTTAGCCTAGGCTAACGTAGCTT"]}
```

The database seeds itself automatically from `data/` on first run. `POST /admin/reset` wipes and reseeds for a clean demo state without restarting the server.

### Run the baseline-vs-agent evaluation (requires a live API key)

```bash
python -m eval.run_eval
```

**This calls the real Gemini API and takes roughly 2 minutes** — it deliberately paces calls ~13 seconds apart to respect the free tier's 5-requests-per-minute limit (see `CHANGELOG.md` for why). Expected output: a per-case comparison table plus overall accuracy for both baseline and agent, written to `eval/results.json`.

### Run with Docker

```bash
docker compose up --build
```

Mounts `./data` as a volume so the SQLite order history — the agent's memory — survives container restarts.

### Cost and runtime

- Test suite: free, <2 seconds, no API calls (baseline is mocked)
- Full eval run: ~2 minutes, 10 Gemini API calls (free tier)
- No paid infrastructure required for any part of this reproduction

## Known limitations

See the "Known limitations" section in [`CHANGELOG.md`](CHANGELOG.md) for the full list of intentional scope cuts (no reverse-complement matching, brute-force fragment search, naive address normalization, rule-based rather than LLM-based customer verification, max-severity rather than weighted verdict logic).
