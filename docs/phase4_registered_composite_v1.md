# Phase 4 Registered Composite v1 (2026-07-27)

SELECTION (made on registered window ONLY, holdout masked and unread at
commit time): cmpA_ew - equal-weighted z_sector_size of mom_12_1, gp_a,
sue, net_issuance(-); value excluded per F2.

Registered window (<= 2023-07-31, 149 months, avg n=1,916):

| variant | LS ann | NW-t | verdict |
|---|---|---|---|
| cmpA_ew | +7.59% | +2.21 | SELECTED |
| cmpA_icw | +7.10% | +2.00 | IC weights add estimation noise |
| cmpB_ew | +5.68% | +1.35 | value costs -1.9%/yr, -0.9t (F2 trial #2: CONFIRMED) |
| cmpB_icw | +6.28% | +1.68 | ICW learns to shrink value (F2 trial #3) |

Pre-registration scoreboard: #1 A>B confirmed; #2 EW>=ICW confirmed;
#3 NEAR-MISS (t=2.21 vs predicted 2.3 floor - tail correlation, owned);
#4 confirmed: worst months are momentum's crashes (2020-10 -13.8%,
2022-12 -12.2%), all variants crash together.

CAPITAL CAVEAT: diversification is an average-month property here, not a
tail property. The composite inherits momentum's crash profile intact.

Holdout protocol: unmasked only AFTER this commit, reading rules fixed in
advance - ~35 months is too short for reliable t; the bar is sign-positive
and no losses beyond the crash-class already priced in above; the outcome
is RECORDED, not re-selected on, whatever it says. Evidence:
composite_walkforward.log, composites_ls, phase4_holdout_masked.json.


---
## Holdout addendum (2026-07-27, unmasked post-commit 6571b3d)
cmpA_ew: -0.53%/yr, NW-t -0.10, 34 months. PRE-REGISTERED SIGN BAR: FAILED.
Recorded; selection unchanged per protocol. Context (not excuse):
degradation vs registered is ~1.1 SE (unremarkable at n=34); window
includes the live 2026 momentum crash. What held: selection ORDERING -
A_ew best of four OOS; value-included variants worst (-5.4 to -6.3%/yr) =
F2's fourth confirmation, in sealed data. OOS attenuation of this size is
the literature norm; the platform measured its own.

CAPITAL VERDICT: not deployable on this OOS evidence. Honest forward
expectation sits between holdout (~0) and registered (+7.6%), shrunk
toward zero by selection effects. The incrementals extend true OOS by one
month, every month, from here - the composite is now a LIVE PAPER TRACK,
and the next 12-24 months of untouched data are the real trial.

Crash-class check (reading rule #2): PASS - worst holdout month -8.1%
(2023-11), within the priced class; 2026-06 not in the worst five. OOS
failure mode was chronic flatness, not acute crash - the tail protection
worked; the average month didn't.
