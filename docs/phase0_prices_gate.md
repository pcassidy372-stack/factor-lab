# Phase 0 Gate: Prices & Total Return — CLOSED (2026-07-24)

Evidence: prices_backfill.log, price_recon table, diagnose_recon.py.

Numbers: 19,830,359 raw OHLCV rows / 10,441 securities / 157,634 corp actions /
full self-built TR index (tr-v1-close-div-split), 2011->present, delisted
population included.

Reconciliation vs vendor dividend-adjusted oracle (rules R15-R17b applied):
99+: 9,338 | 95-99: 712 | <95: 358 | oracle-unusable: 109.
96.3% of measurable names at >=95% daily-return agreement.

Recon rules adopted in-flight, each from a named specimen:
- R15 price-aware tolerance (AAU: penny rounding at $0.18)
- R16 oracle-corroborated seam repair (AADI: WHWK->AADI basis seam, +176x day)
- R17/R17b oracle-bad exclusion / oracle-unusable classification (ABCO:
  interleaved dividend-adjusted feed; raw side sane)

R14 executed: 123/126 flagged open windows closed and delistings dated from
last-trade evidence; 3 residual = empty-price names.

Chain payoff proven: security 6087 (META) spans 2012-05-18 (FB IPO) -> present.

Open ledger:
- <95 tail (358 names, first_mismatch typically day-one): interleaved/fund
  debris hypothesis; DISPOSITION = measure overlap with universe membership in
  Session 7; individual adjudication only if overlap is nonzero.
- 109 oracle-unusable: match unmeasurable; raw+TR retained; same overlap test.
- ~76 empty-price securities (feed stubs); recon markers present.
- R10 stands: reconciliation proves consistency, not truth; spinoff economics
  remain a corp_actions design item.
