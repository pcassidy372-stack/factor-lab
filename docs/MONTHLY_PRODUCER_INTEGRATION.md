# Monthly producer integration — draft, not production recovery

## Actual path changed

The scheduled monthly path in `scripts/incremental.py` now calls
`run_monthly_period`, not `universe_build.py`, `factor_compute_v2.py`,
`factor_eval.py` or `golden_gate.py`. It requires an explicit full
`FACTORLAB_CODE_SHA` and already-installed publication/attempt schemas. It never
installs migrations itself. A code pin is a deployment assertion, not cryptographic
proof of a runtime image; the deployment procedure must independently verify it.

Sequence: acquire the monthly session lock; append a durable attempt; reconcile
an already committed same-code generation (including a missing receipt) without
replaying external work; otherwise perform the existing checked market-cap top-up;
capture one raw-data snapshot; plan one universe; calculate the canonical five;
validate and publish through PublicationStore; append the monthly result.

Failed invocations preserve attempt evidence. A subsequent scheduled invocation
can retry an incomplete month without deleting or modifying legacy `job_log`.
An active session lock is never stolen. A completed same-code period is reused;
`--force monthly` is NOT a data-correction override. Changed source creates a new
generation. Data-only correction under identical code needs a separately reviewed
recovery request mechanism before live use. Daily and weekly legacy claiming is
unchanged; this is not a claim that every scheduler job has new attempt semantics.

Migration 015 creates only the two immutable monthly-attempt/outcome tables and
associated guards. Migrations 001–014 are unchanged. No production migration,
deployment, grant, backfill or job invocation was performed while implementing this.

## Frozen inputs and numerical rules

The source connection is dedicated, repeatable-read and read-only. It is closed
on every outcome, never silently reconnected. The selected completed prior month
must exist; no older-month fallback. Its monthly calendar uses the existing
>=100 priced-securities session rule, and the 13 required month slots must be
consecutive. The collector reads raw prices, caps, classification/identity
observations, fundamentals vintages, monthly TR levels, surprises, benchmarks and
factor definitions. It does NOT copy from legacy universe or factor output tables.

Universe rules retained: latest <=asof price in that month, 63-bar upper median
dollar volume with at least 40 observations, $300m cap, $2m median dollar volume,
$3 price, <=5 calendar-day staleness, and existing instrument/ADR exclusions.
Size buckets preserve NTILE(3) sizes; ties now break on security ID. Multiple cap
rows in a month now deterministically use the latest row on/before asof, instead of
an unordered dictionary overwrite. Missing price/volume remains unavailable, not
an invented zero liquidity observation. These deterministic/input-integrity
corrections are explicit, not a claim of identical legacy behavior for malformed
or ambiguously ordered input. Missing profile sectors remain explicit `Unknown`.

The canonical-five calculations retain the unchanged v2 engine's GP/assets TTM,
net issuance year window, <=140-day SUE, monthly 12–1 momentum, sample 12-month
volatility, 1/99 winsorization, average-tie ranks, normal scores, sector demeaning
and sector/size residual normalization. The original 60% eligible-coverage rule
still applies, separately from the publication engine's all-null rejection.
Zeros remain values; unavailable factors receive explicit missing reasons.
Unknown TR method labels fail rather than becoming usable input.

The database test compares every nonmissing canonical-five output field against
the UNCHANGED legacy v2 engine on the same synthetic input panel, to 1e-12. It also
checks raw economic examples independently. This is mathematical regression
coverage, not proof that live vendor data is complete or historically accurate.
The extra registered research factors, IC/LS evaluation and composites are NOT
rebuilt by this five-factor publication path and their old tables remain intact.
They need their own versioned producer/evaluation integration before being called
current; this deliberately avoids deleting historical research to refresh MT3.

## Availability and limits

Seven input fingerprints describe the actual raw payload, with source revision
and database-observation time. Captured bytes are immutable during calculation;
the full raw payload is not yet a durable replay archive. Published output and
provenance are immutable. No retrospective historical-availability claim is made.
Original vendor timing/value-PIT flags are retained in fingerprints, not promoted.
The existing vendor-accepted-date formula selection is unchanged in this stage.

The SPY benchmark must cover the selected monthly grid. This is an additional
explicit upstream readiness check, not a sixth factor or a change to a trading
rule. Passing it and factor coverage does NOT certify provider completeness.
Watermarks alone cannot detect a provider's silent omissions. The current
snapshot's classification observations are not historical classification proof.
The production source-refresh ownership/reconciliation review remains required.

A publication receipt is observed only after commit. The real producer-to-store
integration test rejects an older decision cutoff after late publication. This
is still NOT an MT3 consumer test; MT3 remains on its old adapter until a separate
code integration is completed. Existing MT3 decisions, gates, orders and portfolio
state are entirely outside this producer's write scope.

## Release blockers

Keep the PR draft and unmerged. Next required work is the MT3 publication reader,
source-identity and exact-date health integration with consumer-level cutoffs;
upstream fundamentals/SUE/benchmark and cap-refresh acceptance; a durable raw-input
replay artifact and data-only correction workflow; compatibility of daily/weekly
claiming and legacy research surfaces; restricted grants and schema handoff; and a
verified backup restoration plus isolated recovery rehearsal. Merge, deployment,
production migrations and data recovery need separate authorization.

Tests run only in disposable loopback PostgreSQL databases with synthetic data.
The legacy engine is invoked solely as a numerical oracle there, not as a recovery
command. Synthetic destructive setup in that test never targets Railway. No
production command is supplied by this document.
