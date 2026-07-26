# Phase 1b Report — Registry Breadth (2026-07-25, R20-verified)

Evidence: factor_eval_1b.log, phase1_eval.json, factor_ls (10 factors,
118-172 months). Canonical five reproduced EXACTLY under compute v2 —
implicit determinism pass. Gate untouched (canonical five, already PASS).

| New factor | Prior | LS ann (aligned) | NW t | Pre-registered expectation | Outcome |
|---|---|---|---|---|---|
| sue | + | +3.87% | +2.29 | modest positive (PEAD attenuated) | CONFIRMED — strongest new factor, IC t=2.71, 118m |
| net_issuance | − | +2.94% | 1.61 | moderate aligned | CONFIRMED |
| bp | + | +0.41% | 0.19 | weakest, near-zero (value winter) | CONFIRMED — undercuts ebit_ev as predicted |
| beta_36m | − | +0.87% | 0.31 | flat | CONFIRMED |
| vol_12m | − | −1.12% | 0.38 | sign-miss legitimate | SIGN MISS, recorded as pre-registered |

No discovery claims (|t|>3 bar untouched). Revision-3m activates as the
autonomous snapshot history accrues (W30 = snapshot #2, taken by the loop).

## Autonomy note
Weekly job 2026-W30 ran without human involvement: 47 new statement
vintages, 3 LIVE-CAUGHT RESTATEMENTS — the first value_pit=true rows.
The strict-PIT gold standard is now accruing on schedule.
