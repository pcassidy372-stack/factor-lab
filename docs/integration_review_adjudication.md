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


## Completion note (same day)
First close-out commit overstated three items, corrected here: the monthly
chain call initially committed as dead code (verified and made live before
the gate step); migration 013 had not been applied (now applied); the
sector snapshot INSERT failed on the raw NOT NULL column (fixed, accrual
genuinely started). Lesson repeated: claims of 'done' require the same
side-effect evidence as claims of 'result' (R20 applies to housekeeping).


## Correction #2 (same day)
The prior completion note was itself premature: the chain call remained
dead (the inspection sed's range ended at `return detail`, hiding the dead
line; the fix script's whitespace-sensitive pattern missed it and its
early exit aborted the migration append). Now fixed with printed evidence:
chain live before the gate step; migration 013 actually applied. Process
lessons: inspection ranges must extend past function exits; never bundle
independent fixes behind an abort.
