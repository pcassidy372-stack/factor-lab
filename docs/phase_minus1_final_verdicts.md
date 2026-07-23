# Phase -1 Final Verdicts (2026-07-22) - PHASE CLOSED

Evidence: 90-name torture sweep (torture_runner.py), vintage comparator on
KHC/GE/SMCI vs SEC XBRL, feed caches. Artifacts: torture_results.json,
torture_run.log, symbol_change_full.csv, delisted_full.csv, mna_latest_cache.json.
Supersedes the vintage section of phase_minus1_vintage_verdict.md.

## Verdict #2 (REVISED): vendor vintage preservation

| Issuer | Mechanism | Restated periods | Outcome |
|---|---|---|---|
| KHC | later-10-K comparative footnote | 6 | quarterlies ORIGINAL, annuals RESTATED |
| GE  | next-year 10-K/10-Q comparatives | 8 | 7/8 RESTATED-VALUE with ORIGINAL acceptedDate |
| SMCI| 10-K/A + 10-Q/A amendments | 10 | majority RESTATED-VALUE with ORIGINAL acceptedDate |

CONCLUSION: FMP standardized statements serve CURRENT-VIEW VALUES with
ORIGINAL-FILING TIMING. Session-2's quarterly-lane optimism did not
generalize (KHC = footnote mechanism only). The false-confidence trap is
the dominant observed mode.

## Rules adopted (continuing R1-R6)
- R7 VINTAGE: backfill rows labeled value_pit=false / timing_pit=true.
  Strict-PIT mode (live-accumulated vintages) is the gold standard.
  EDGAR original-vintage overlay (companyfacts, ~15 concepts, restated
  periods) REGISTERED as upgrade path - not a Phase 0 blocker.
- R8 ACCEPTED-DATE VALIDATION: lag = accepted - period_end.
  lag <= 10d -> acceptedDate MISSING (vendor period-date fallback; observed
  -2..0d on FRC/SBNY/PARA/LKNCY/BBBY/KVYO) -> quarantine from PIT studies.
  10d < lag <= 25d -> likely earnings-release-sourced; PIT-legitimate; flag.
  lag > 200d -> genuine delinquency (NKLA 282d, TUP 272-363d) - keep.
- R9 NON-RANDOM RESTATEMENTS: restatements correlate with earnings quality;
  backfill-era accruals/quality findings carry a value-PIT caveat and get
  strict-PIT re-confirmation priority.
- R10 TR ORACLE = CONSISTENCY, NOT TRUTH: both self-built TR and the vendor
  dividend-adjusted series are spinoff-blind (GE reconciled 100% through
  GEHC+GEV). Spinoff economics enter via corp_actions events only.
- R11 FEED HOLES: delisted feed missing CERN/ALXN/XLNX/MXIM/YELL; deal feed
  ordering/caps unreliable pre-2021 -> delisting rung sources = deal feed +
  delisted feed + deals_manual.csv (public terms, PIT-legitimate).
  symbol-change feed effective start 2020-09 -> pre-2020 changes (LIN) via
  CIK matching + manual seeds.
- R12 CHIMERAS OPERATIONALIZED: BBBY and SBNY confirmed ticker reuse ->
  symbol_map rows with valid_to at delist, new issuer rows after reuse.

## Gate results
| Gate | Result | Status |
|---|---|---|
| G1 identity | 87/90 clean; 3 flags all explained (2 reuse chimeras, 1 feed depth) | PASS |
| G2 TR reconciliation | 89/90 names >= 95% match (most 100%); sole failure WE 94.8% | PASS w/ R10 caveat |
| G3 statement timestamps | acceptedDate present 90/90; R8 classifier adopted | PASS |
| G4 delisting resolvability | 20/25 via feeds + 5 manual seeds = method complete | PASS w/ R11 |
| Vintage preservation | mixed -> current-view values, true timing | VERDICT WRITTEN (R7) |

## Carried into Phase 0
1. deals_manual.csv: CERN, ALXN, XLNX, MXIM, YELL terms (10 min, manual).
2. WE TR diagnosis (reverse split / SPAC lineage suspect).
3. Spinoff corp_actions sourcing (R10) - design item in prices ingestion.
4. EDGAR original-vintage overlay - registered, scheduled post-Phase-1.
5. SMCI 2017-Q4 NEITHER row - derived-Q4 artifact, note in ingestion tests.

PHASE -1: CLOSED. All exit gates evidenced in writing. Phase 0 is open.
