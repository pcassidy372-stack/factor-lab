# Phase 0 Gate: Market Caps & Universe — CLOSED (2026-07-24)

Evidence: universe_build.log, recon_recheck.log, prices_repair.log,
universe_tail_diagnose.py.

Universe: monthly snapshots 2011-03 (ADV burn-in) -> present; filters $300M /
$2M ADV / $3 / staleness<=5d; common stock only, ADRs excluded (1,478
instrument-class + 457 ADR securities filtered); June curve ~1,460 (2011) ->
~2,830 (2026); macro events legible (COVID -116/mo, SPAC boom +147/mo peak).

Findings -> rules (each from a named specimen):
- R15b two-sided recon tolerance: oracle adjustment-depth quantization
  (AVGO: 100% on 2021-window, 88% on 2011-window under one-sided R15).
  Recheck moved 629/1,070 names to 99+.
- R18 windowed open-symbol-priority fetch: ticker-reuse contamination
  (new "FB" microcap overwrote META 2025+; AFC Gamma onto Hanover;
  Babcock onto Materion). 1,643 securities repaired (1,635 ok / 8 low);
  tr-v2 method version.
- Spec s5/s8 enforcement was MISSING and the recon tail was the canary:
  instrument + ADR filters implemented with counts.

Final recon distribution: 99+ 10,051 | 95-99 299 | <95 66 | no-oracle 102.
Recon-tail universe overlap: 194 -> 14 (retained, flagged; adjudication in
factor QA — 14 names in a ~2,000-name cross-section, R10 caveat applies).

Open ledger:
- chain-audit-v2: recon healing can MASK mapping errors (CXP/EXP class);
  audit ALL multi-symbol securities vs feed evidence; pre-feed-era chains
  flagged manual-verify.
- Mismerge suspects: IPS/RDMX; FLMN/FLMNW (warrant-on-common — also causes
  collateral exclusion of the common; instrument mapping fix).
- 176 mktcap coverage holes (dead microcaps); 66 <95 residual incl. 8
  repair-lows; META residual exclusions per reasons query above.

META restored: closes $595-627 (2026-07), 169 universe months, span from
2012-05-18. AAPL 185/185.
