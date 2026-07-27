# Phase 2 Registered Findings v1 (2026-07-26)

Evidence: factor_fm.log, fm_coefficients (R20: 1,590 rows / 159 asofs),
commit fabb5fb. Registered window = dev+validation (asof <= 2023-07-31,
124 months, ~1,376 names/mo, complete-cases across all 10 factors).
Holdout untouched. No claim herein meets |t|>3; sue's univariate IC
(t=3.24/3.62) is IC-not-premium and replication-class.

## Marginal (Fama-MacBeth) table — registered window
| factor | FM bp/mo | NW-t | uni-IC t | disposition |
|---|---|---|---|---|
| mom_12_1 | +11.9 | +1.41 | +0.60 | only near-significant marginal premium (full-sample t=2.31) |
| gp_a | +10.7 | +1.54 | +1.90 | strongest fundamental marginal |
| bp | +7.2 | +0.95 | +0.60 | noise |
| sue | +4.9 | +1.39 | +3.24 | positive marginal; best rank signal on platform |
| net_issuance | -5.4 (aligned +) | 0.91 | 1.82 | aligned, modest |
| ebit_ev | **-9.5** | **-1.20** | +1.44 | see finding F2 |
| accruals | -0.3 | 0.06 | 0.53 | dead (as pre-registered) |
| vol_12m | -1.4 | 0.13 | -2.00 | rank-alive, premium-dead |
| beta_36m | +3.3 | 0.41 | -0.09 | noise |
| asset_growth | +1.5 | 0.26 | 0.29 | absorbed by net_issuance |

## F1 — RETRACTION (pre-registered falsification honored)
The v2-findings claim that Fama-MacBeth would vindicate ebit_ev ("rank
works, tails are traps") is FALSIFIED: the marginal beta is negative in
both windows. Retracted as promised at registration.

## F2 — Value is its correlations (the replacement finding)
ebit_ev is the correlation hub of the board (|rho|: gp_a .30,
net_issuance .30, accruals .26, bp .23, vol .22). Conditional on
partners, its residual is weakly negative. EBIT/EV contributes no
marginal information in this universe/window; its univariate rank IC is
borrowed. Composite design implication (Phase 4): no standalone value
sleeve; value enters through quality/issuance or not at all.

## F3 — Redundancy map
Clusters @ d<0.7: {asset_growth, net_issuance} (rho .36 — investment/
dilution are one bet), {beta_36m, vol_12m}. All else singletons; the
predicted value cluster {ebit_ev, bp} did NOT form (.23). Max pairwise
|rho| = .36: the neutralization pipeline yields a near-orthogonal board.

## F4 — Standing signals
Momentum: the platform's one marginal premium. SUE: the platform's most
consistent rank signal. Low-vol: alive in ranks (IC t about -2.0), absent
in premiums. Everything else: honest noise this window.

---
## v1.1 addendum (2026-07-27, post-controls)
Session-14 controls (pit selector, 16:00 cutoff, bounded fallback,
completed months, tie-safe ranks) moved the registered FM table by at most
0.4bp/mo: F1-F4 stand unchanged and are hereby marked CONTROLS-ROBUST.
Registered run stored as fm_coefficients run_id='fm-controls'; v1 preserved
as 'fm-multi'. LS-level shifts (mom +8.73%, sue +2.43% t=1.74) are
mechanical consequences of the named controls, documented in
code_review_adjudication.md.
