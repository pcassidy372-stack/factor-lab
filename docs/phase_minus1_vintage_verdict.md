# Phase -1 Verdict #1 — Vendor Vintage Preservation (2026-07-22)

Evidence: KHC 2016-2017 restatement (restated via FY2018 10-K comparatives,
filed 2019-06-07) cross-checked against SEC XBRL companyconcept facts.
Runner: scripts/probe_vintages.py -> artifacts/vintage_probe.json

## Verdict: MIXED, decodable, and buildable-around

| Lane | Behavior observed | PIT status |
|---|---|---|
| Quarterly standardized | 3/3 restated quarters serve ORIGINAL values, original acceptedDate | VINTAGE-PRESERVING (this mechanism) |
| Annual standardized | FY2016 serves RESTATED value (3,596M vs original 3,632M) with ORIGINAL acceptedDate (2017-02-23) | FALSE-CONFIDENCE TRAP - confirmed live |
| As-reported (stable) | Sparse ~5-key blob, no accession, no timestamps | NOT a vintage backbone - demoted |

## Design rules adopted
- R1: PIT clock = acceptedDate (true EDGAR acceptance timestamp; validated via
  after-hours offset on KHC Q3-17 and SMCI delinquency signature: 148d lag,
  two periods accepted same day 2025-02-25).
- R2: QUARTERLY rows are the only PIT statement inputs. TTM = 4 quarters.
  Annual standardized rows are non-PIT convenience, never factor inputs.
- R3: SEC XBRL (companyconcept/companyfacts, accession-stamped) is the
  restatement oracle for cross-checks and re-pull drift detection.
- R4: Period-end matching across sources is fuzzy (+/-5 days): 52/53-week
  fiscal calendars normalize differently (KHC FY2017: EDGAR 2017-12-30 vs
  FMP 2017-12-31 - skipped by exact match in this probe).
- R5: Identity is date-effective. EDGAR-confirmed ticker reuse: CIK 0001130713
  (ex-Overstock) is now legally "BED BATH & BEYOND, INC." holding ticker BBBY;
  original issuer CIK 0000886158 is bankruptcy shell "20230930-DK-Butterfly-1".
  One ticker, two issuers -> symbol_map(valid_from, valid_to) is mandatory.
- R6: Feeds - symbol-change pulled with large limit (spans >= 2020-09; both
  sentinels FB->META 2022-06-09 and SQ->XYZ 2025-01-21 present); delisted feed
  fully paginated (~150/month); earnings lane = /stable/earnings; 13F lane =
  institutional-ownership/symbol-positions-summary + latest-filings acceptedDate.

## Scope and open items
- Evidence base: ONE issuer, ONE restatement mechanism (later-10-K comparatives).
- Untested: 10-K/A and 10-Q/A amendment mechanism - may overwrite quarterly rows.
- FY2017 annual presumed consistent with FY2016 pattern; unverified (R4 skip).
- Both items route to the full torture-sample runner (Phase -1 exit gates).
- Strict-PIT mode stays in the Phase 0 schema regardless: quarterly backfill is
  upgraded to "vendor-vintage-evidenced", not "proven".
