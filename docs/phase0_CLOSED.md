# PHASE 0: CLOSED (2026-07-26)

Built 2026-07-22 -> 2026-07-26. Every gate evidenced in writing; every rule
(R1-R19) traceable to a named specimen.

## Gates
| Gate | Verdict doc | Headline |
|---|---|---|
| Phase -1 vendor proof | phase_minus1_final_verdicts.md | Vintage: current-view values / original timing; R7-R12 |
| Identity | phase0_identity_gate.md | 10,520 securities; chain proofs TRUE; 19 wild chimeras |
| Prices & TR | phase0_prices_gate.md | 19.8M rows; 96%+ recon; R15b/R16/R17b/R18 |
| Universe | phase0_universe_gate.md | 2011-03 -> now; spec s5/s8 enforced; tail 194 -> 14 |
| Fundamentals | phase0_fundamentals_gate.md | 223,758 bitemporal rows; R19 rejected 7,576 impostor rows |
| Events & estimates | (8b logs) | 315,240 surprises / 229,111 SUE; snapshot clock started 2026-07-25 |
| Golden gate | scripts/golden_gate.py | PASS 8/8 first run; standing precondition |
| Incrementals | scripts/incremental.py | Deployed: hourly cron; daily/weekly/monthly self-feeding |

## What Phase 1 inherits
- Strict-PIT vintage stream live from first weekly sweep (value_pit=true begins).
- Open ledger: chain-audit-v2; 27 CIK-blocked securities (EDGAR recovery);
  45 overlap-conflicts + IPS/RDMX + FLMN/FLMNW; 176 mktcap holes; residual-14
  recon names (R10 caveat); EDGAR original-vintage overlay (registered).
- Rule: golden_gate.py is the first command of every session from here on.

Phase 1 opens with the canonical five and the replication gate.
