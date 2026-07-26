# Phase 1 + 1b Findings v2 — DEFINITIVE (2026-07-26)

Supersedes phase1_replication_gate.md and phase1b_report.md. This board is
triply verified: hand-built momentum audit (stored-vs-hand corr 0.905;
hand-vs-French 0.884), robust month-end grid (phantom asofs = 0, T9), and
R20 side effects (phase1_eval.json + factor_ls 10 factors x 159-184 months).
Golden gate 9/9. Evidence: factor_eval_FINAL.log, eval_hand_audit.log.

## Replication gate: REVIEW (4/6) — the honest verdict
PASS: momentum (+6.40%/yr, t=1.51; UMD corr 0.830 under corrected keying),
profitability sign (+0.97%), asset growth aligned (+3.63%, t=1.47 IC-side),
UMD oracle. FAIL: value sign (ebit_ev LS -1.75%/yr) and accruals (dead,
-0.10%). Interpretation, with literature behind it: 2011-2026, $300M+
liquid US commons, sector-size-neutralized deciles — the value winter and
post-publication accrual decay, measured independently on honest
infrastructure. The gate performed its function.

## The structural finding: IC/LS divergence
| factor | IC t | LS ann | reading |
|---|---|---|---|
| sue | +4.55 | +1.78% (t=1.21) | strongest, most consistent monthly rank signal on the platform |
| net_issuance | -3.03 | -2.90% aligned | robust in rank, modest in tails |
| gp_a | +2.69 | +0.97% | rank works; tails don't |
| ebit_ev | +2.27 | -1.75% | rank positive, extreme deciles NEGATIVE — value traps live in D10 |
Rank predictiveness concentrates in the distribution middle; decile
extremes underperform or invert. Deciles are the wrong lens for value and
SUE in this universe. Phase 2's Fama-MacBeth (middle-weighted by
construction) is the designated instrument, not a workaround.

Low-risk pair: both LS wrong-signed this sample (vol aligned -2.00%, beta
aligned -3.33%... beta LS +3.33 raw); recorded, rate-regime caveat noted.
Live observation: 2026-06 appears in the worst-month list of four factors —
an ongoing momentum/low-vol drawdown, dated by the platform in real time.

## Forensic chain (how the prettier numbers died)
1. Phantom-asof grid: 1,123 junk weekend/holiday rows let 22 non-trading
   dates define month-ends; poisoned vol_12m (19 asofs), shaved eval months
   platform-wide (session 7 onward).
2. Paste-entropy incidents: eval file passed through corrupted and reverted
   states never committed to git; a partial duplicate compute died mid-run.
3. UMD keying off-by-one since inception: our forward return keyed by
   signal month vs French's calendar month. Hand audit proved a correct
   series scores ~-0.09 under that keying — therefore Saturday's 0.783
   could not have come from a correct series.
4. Hand audit (eval_hand_audit.py) adjudicated: substrate sound, today's
   board faithful, keying fixed, definitive eval run under R20.
Rules added this weekend: R20 (no output is a result without verified DB
side effects — applies to AI assistants), robust month-end grid, T9
derived-board integrity, py_compile-before-run, single-terminal discipline,
hand-audit precedent for any suspicious eval.

## Standing
Registry frozen v1 (10 factors). No claim herein meets the |t|>3 discovery
bar except sue's IC (replication-class, no novelty asserted). Phase 2
(Fama-MacBeth marginality, redundancy clustering, walk-forward) opens on
this board.
