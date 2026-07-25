# Phase 0 Gate: Fundamentals — CLOSED (2026-07-25)

Evidence: fundamentals_backfill.log, fund_ingest, KHC spot vs session-2 EDGAR
comparator.

223,758 bitemporal rows / 4,668 of 4,741 ever-in-universe securities;
quartile depth 29/55/66 quarters; nulls: rev 0.0% ebit 0.0% cfo 0.9%
assets/equity 2.2% shares 0.0%.

R8 at ingestion: filing 181,054 (81%) | missing 33,643 (15%, timing_pit=false,
concentrated in delisted names' vendor-stuffed histories) | release 8,490
(earnings-release-sourced, PIT-legitimate) | delinquent 571 (real: SMCI-class).

R19 CIK gate: 7,576 impostor rows rejected / 107 securities. BBBY chimera
resolved per-side (shell kept 63 original periods, rejected Beyond's 6;
ex-Overstock kept 65, rejected 192 of the corpse's). 27 securities fully
blocked (ABCO-class: vendor history overwritten by reuse) -> EDGAR
companyfacts recovery on ledger.

Verdict #2 (R7) embodied: all rows backfill=true, value_pit=false; strict-PIT
gold standard begins with live incrementals (session 10). KHC 2016 NI
reproduces session-2 originals exactly: 896/950/842/944, lag_class=filing.
