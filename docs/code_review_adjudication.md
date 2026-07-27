# Code Review Adjudication (2026-07-27) — Session 14 Controls

Review: Factor_Lab_Current_State_Code_Review.pdf (conditional GO, 8/10
architecture, 5/10 backtest reliability). Verdict-by-verdict:

## Adopted (real catches — reviewer earned their fee)
- P0-3 vintage-selection ordering: max-vintage collapsed at LOAD before the
  as-of filter — restatements would have silently ERASED original filings
  from history. Caught pre-fire (0 restatements existed). Fix: factorlab/
  pit.py, the single production selector; gate T10 proves orig-then-restate
  + after-close cutoff through the production path. THE catch of the review.
- P0-2 unbounded delisting fallback: latest-ever TR could masquerade as a
  1-month return across gaps/halts. Bounded to the window.
- P0-4 after-close look-ahead: 16:00-ET cutoff on accepted timestamps.
  Fingerprint of correctness: fundamentals factors lost 20-46 cells each;
  sue/mom/vol lost exactly zero.
- P0-1 (residual): incomplete current month excluded from formal inference
  (the "live 2026-06 crash" rows were partial-month).
- Tie-rank nondeterminism: argsort -> rankdata(average); material for
  net_issuance's zero point-mass.
- P0-5 (kernel): single-transaction derived rebuilds — a dying compute can
  no longer leave a partial board.
- Provenance (kernel): fm run_id semantics; registry formula text corrected
  (operatingIncome); requirements pinned.

## Declined / deferred, with reasons
- Exchange-calendar table: superseded by the data-driven robust grid
  (>=100 traders), which caught Good Fridays a DOW filter would miss.
- Append-only lineage, staging swaps, pytest tree, NUMERIC->float8: right
  for a multi-user shop; solo-scale substitutes adopted (git + gate +
  synthetic tests through production paths + single-txn rebuilds). Ledger.
- PIT sectors: history unrecoverable from vendor; monthly sector
  snapshotting to begin (ledger); limitation documented.
- Holdout contamination: partially irreversible at factor level (full-
  sample stats seen); control adopted = composite decisions cite the
  registered window ONLY; holdout computed-but-masked until release.

## Controls-rerun adjudication vs pre-registered bounds
FM registered window: ALL 10 factors within +-0.4bp/mo — bound HELD;
every Phase-2 finding controls-robust. LS bounds breached for mom
(+6.40->+8.73) and sue (+1.78->+2.43): traced to the controls themselves
(partial-month exclusion removed a -15.2% mom month; bounded fallback) —
plus a bound-setting error (flat LS bounds are incoherent when a control
deletes a crash month; future bounds scale per-control). accruals crossed
zero (t~0.2, noise) flipping the replication line to REVIEW 5/6 — no
credit taken; sign checks on dead factors are coin-flips. UMD 0.830->0.852.
Gate 10/10 (T10 first-run PASS).
