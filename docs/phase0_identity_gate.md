# Phase 0 Gate: Identity — CLOSED (2026-07-22)

Evidence: identity_backfill.log, identity_check.py, identity_reconcile.py runs.

Numbers: 11,617 targets -> 8,585 issuers / 10,520 securities; ~1,700 chain-
reconstructed historical ticker rows; 4,696 delistings (309 deal-matched).
Chain proofs TRUE: FB(2021)=META(now)=6087; SQ(2024)=XYZ(now)=8825.
19 live reuse chimeras (BBBY-class) mapped or quarantined.

Adjudication (all auto-actions audit-logged in identity_quarantine, resolved=true):
230 sentinel dedups, 174 contained dups, 48 same-security merges, 7 boundary
trims, 4 CIK dedups.

Open ledger (recorded, not guessed):
- 45 overlap-conflicts: pre-2010 microcap/SPAC ticker reuses; largely below the
  $300M universe floor; adjudicate opportunistically (price-coverage evidence).
- 262 no-profile: deal-feed targets outside FMP profile coverage.
- 126 inactive-no-delist-date: open rows; CLOSURE PLANNED (R14) from price-series
  end dates in the prices ingestion.
- 35 multi-delist-history, 19 reuse-suspect, 16 no-profile-anchors.

Rules added:
- R13 EVIDENCE-SEEDED TARGETS: target set = census UNION delisted feed UNION
  every symbol we hold evidence about (torture sample, deal targets). Feeds
  propose; evidence insists.
- R14 PRICE-END CLOSURE: last raw trade date closes open windows and dates
  delistings for names the delisted feed missed.
