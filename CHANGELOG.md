# Improvement Changelog

This changelog tracks how the DNA Synthesis Screening Assistant evolved from a naive baseline to the final multi-agent pipeline, including the bugs we hit along the way and what they taught us. All results below come from a real run of `python -m eval.run_eval` against a live Gemini API (`eval/results.json`), not simulated numbers.

## Final comparison

| Metric | Baseline (single prompt, no memory) | Agent (multi-agent pipeline) | Change |
|---|---|---|---|
| Overall accuracy (10 cases) | 5/10 (50%) | 10/10 (100%) | +50 pts |
| Clean/benign orders (4 cases) | 4/4 | 4/4 | parity |
| Direct hazard match, single order (2 cases) | 1/2 | 2/2 | +1 |
| Cross-order fragmentation (3 cases) | 0/3 | 3/3 | +3 |
| Denylisted customer, clean sequence (1 case) | 0/1 | 1/1 | +1 |

The baseline is competitive on benign orders — an LLM correctly defaults to "not suspicious" when nothing looks obviously wrong. It falls to **zero** the instant a case requires something it structurally cannot have: a hazard sequence reference bank, memory of prior orders, or access to a denylist. That gap, not the raw accuracy number, is the actual finding of this project.

## Stage-by-stage

| Stage | What we tried and why | Evidence | Decision / learning |
|---|---|---|---|
| Baseline | Single Gemini prompt given an order's sequences + declared purpose, asked "is this suspicious?" — no hazard database, no order history, no structured verification. Represents what a team would ship if they just asked an LLM directly. | 50% accuracy; 0% on every case requiring memory or a reference database | Established the starting point and confirmed the core hypothesis: sequence text alone, without a reference bank, gives an LLM almost nothing to work with — it correctly ignores clean orders but has no way to recognize a hazard sequence it's never been shown |
| Iteration 1 — schemas + persistence | Built `Signal`/`ScreeningReport` Pydantic models and a SQLite layer (`db.py`) before any agent logic, so every agent would emit auditable evidence rather than a bare yes/no | All schema unit tests passing | Decided every specialist agent emits `Signal`s, never a verdict — only the orchestrator decides. Also hardcoded `requires_human_review=True` at the schema level (not just in application logic), directly satisfying the ground rule that a human must be part of any consequential decision |
| Iteration 2 — Sequence Screening Agent | Added exact/embedded hazard-sequence matching using a sliding-window similarity check | Caught a real bug during testing: an early version of the similarity function let a short fragment (half of a hazard sequence) "match" a same-length window of the full hazard sequence, since any sub-fragment trivially equals part of its source | **Kept, after fixing.** Restricted matching so a submitted sequence can only match a hazard sequence at least as long as itself. This is the first hot-take-worthy bug — see below |
| Iteration 3 — Fragmentation Agent | Added cross-order memory: pulls order history for the same customer *and* for any customer sharing a shipping-address cluster, then searches combinations of fragments for an exact reconstruction of a hazard sequence | Found a synthetic-data bug during testing: two customers meant to share a shell-company address cluster actually had different street addresses, which would have made the adversarial test case (TC-08) pass for the wrong reason | **Kept.** Fixed the data, added a dedicated test (`test_address_cluster_detects_shell_group`) so this class of bug can never silently regress again |
| Iteration 4 — Customer Verification Agent | Added denylist matching and a simple 3-signal shell-account heuristic (new account age, free-mail domain, vague declared purpose) | 6/6 unit tests passing on first run | Kept as-is. Deliberately rule-based rather than LLM-based for this pass — documented as an area an LLM reasoning layer could improve later (e.g. judging purpose plausibility against institution profile) |
| Iteration 5 — Orchestrator | Combined all three agents' signals into a single deterministic verdict using a max-severity rule (any HIGH signal escalates; else any MEDIUM recommends review; else clear) | All 10 hackathon test cases passed end-to-end for the first time | Kept for this submission. Known limitation: a max-severity rule can't distinguish "one weak signal" from "several moderate signals that should add up to something serious" — a weighted or LLM-scored aggregation is the natural next iteration |
| Iteration 6 — Baseline + eval harness | Built the single-prompt baseline and an eval script comparing it to the agent across all 10 cases, with per-case database isolation so one case's order history can never leak into another's fragmentation check | — | Discovered `google-generativeai` (originally planned) had been fully deprecated in favor of `google-genai`; migrated before writing any more code against it, rather than shipping on a dead package |
| Iteration 7 — Reliability hardening | First live eval run failed outright with what looked like a transient "high demand" error | Google's actual error response: `429 RESOURCE_EXHAUSTED`, free-tier limit of 5 requests/minute, explicit `retry in 58s` | **Root cause was a hard rate limit, not overload.** Added exponential backoff *and*, more importantly, proactive pacing (~13s between baseline calls) in the eval script — retries alone can't fix a per-minute cap; only pacing prevents tripping it in the first place. Second hot-take-worthy finding — see below |
| Iteration 8 — API layer | Wired `main.py` (FastAPI) around the orchestrator | 5/5 API-level tests passing, including two genuinely separate HTTP requests where the second correctly detects fragmentation from state the first request persisted | Deliberately did **not** reseed the database on every server startup — only when it's genuinely empty — since reseeding would silently wipe the order-history memory the fragmentation agent depends on across restarts |
| Final | Combined all agents behind the orchestrator and API | 10/10 test cases pass; 100% agent accuracy vs. 50% baseline in the live eval run | The measured improvement is concentrated exactly where the architecture predicts it should be: fragmentation and denylist cases, where the baseline has no access to the information needed at all |

## Known limitations (intentional scope cuts, not oversights)

- No reverse-complement sequence matching (a real screening system would also check the reverse complement strand)
- Fragmentation search is brute-force permutation over a small fragment pool (2–3 pieces) — fine at hackathon order volume, would need smarter candidate generation (e.g. k-mer indexing) at real production scale
- Address clustering uses naive string normalization, not real address parsing/geocoding
- The "vague declared purpose" check is a small hardcoded keyword list, not semantic judgment
- Orchestrator verdict logic is max-severity, not weighted or LLM-scored

## Hot takes / insights

1. **A short fragment will always "match" part of a longer reference, and that's not a finding.** Our first sequence-matching implementation let a 15bp piece of a 30bp hazard sequence register as a match against a same-length window of that hazard sequence — which is true of literally any sub-fragment of anything. The lesson generalizes past DNA: any similarity matcher that doesn't account for length asymmetry between the query and the reference will manufacture false structural matches. We caught it because we tested the adversarial fragmentation case, not just the happy path.

2. **Read the actual error body before you design a retry strategy.** We initially assumed a failed live API call meant transient "high demand" and reached for exponential backoff. Google's error response said otherwise: a hard 5-requests-per-minute free-tier cap with an explicit 58-second retry delay. Retrying harder cannot fix a rate limit — only pacing calls to stay under the quota can. The general lesson: a caught exception's message is data, not just a trigger to retry; assuming the failure mode before reading it costs a debugging cycle.

3. **The baseline's failure mode is total, not partial, and that's the actual point.** We expected the baseline to do *worse* on fragmentation, not to hit exactly 0%. But 0% is the more honest and more interesting result: it's not that a single-prompt LLM is bad at this task, it's that the task is structurally impossible without persistent state and a reference database. No amount of prompt engineering fixes a missing capability.