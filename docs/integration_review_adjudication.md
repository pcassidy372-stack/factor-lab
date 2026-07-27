# Integration Review Adjudication (2026-07-27, review #3)

Verdict: the best-aligned review of the three - strategy adopted wholesale
for the MT-side build (FL as evidence/risk layer; Lens with evidence
labels; immutable tracker_factor_snapshots frozen at consumption;
unavailable-never-neutral; promotion only on Tracker-specific forward
evidence). MT-side items live in the tracker docket.

FL-side, actioned this session:
- 7.1 CONFIRMED REAL: factor_fm.py carried its own unpatched fwd() copy
  (eval got the S14 bound; fm's duplicate did not). Root cause: code
  duplication. Fix: factorlab/returns.py shared bounded fwd, both callers
  patched. Rerun fm-controls-v2: mean coefficient deltas 0.005-0.06bp -
  ALL REGISTERED FINDINGS VERIFIED; the bug bit individual months (max
  7.7bp) but was aggregate-negligible. Canonical run: fm-controls-v2.
- 7.3 ADOPTED (second review to flag): monthly job now runs
  compute -> eval -> gate after universe refresh; first patch landed as
  dead code after return, caught by mandated inspection, fixed.
- Estimates schema: migration 013 adds period_type/eps_high/eps_low/
  source_observed_at (quarterly collection to follow).
- 7.2 PARTIAL: as-of sector join is correct but historyless today -
  monthly sector snapshot accrual STARTED; pre-2026 drift documented as
  unrecoverable.
- DB separation rule adopted: MT adapter uses FACTOR_DATABASE_URL,
  read-only; fundamentals_q collision confirmed real.
Declined with reasons: append-only factor_values (immutability lives at
consumption in tracker_factor_snapshots; production stays deterministic
rebuild + git), schema namespace (separate DBs chosen).
