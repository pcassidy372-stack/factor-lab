> **SUPERSEDED (2026-07-26) by phase1_findings_v2.md.** The numbers in
> this document were produced on the phantom-asof grid by eval code in
> unreconstructible paste-corrupted states, with a UMD test miskeyed by
> one month since inception. See v2 for the forensic chain and the
> verified results.

# Phase 1 Gate: Replication — PASS 6/6 (2026-07-25, REAL)

Evidence: factor_eval_REAL.log, artifacts/phase1_eval.json (verified on disk),
factor_ic/factor_ls (verified: 5 factors x 160-172 months). Registry frozen v1.

| Factor | Prior | LS ann | NW t | IC t | Verdict |
|---|---|---|---|---|---|
| mom_12_1 | + | +9.31% | +2.18 | +2.56 | Replicates; worst = Jan-21 (-18.3%, GameStop), Nov-20, Feb-21 |
| gp_a | + | +4.12% | +2.05 | +2.41 | Replicates (Novy-Marx); CI clear of zero |
| asset_growth | − | +3.48% aligned | 1.88 | 1.94 | Sign-correct, attenuated |
| ebit_ev | + | +2.87% | +1.42 | +1.89 | Sign-only pass; CI straddles zero (value-winter sample, as pre-registered) |
| accruals | − | +1.41% aligned | 0.72 | 0.94 | Sign-correct, nearly dead — deepest post-publication decay, consistent with literature |

External oracle: Ken French UMD corr 0.783 (gate 0.60), 160 months.
Method: 1m horizon, z_sector_size, decile L/S, delisting terminal handling,
block bootstrap, NW errors. No claim herein meets the |t|>3 discovery bar;
none was made. Replication of published effects only.

## Incident report (2026-07-25)
A pasted "PASS 6/6" output preceding this run was fabricated text from
outside this pipeline — caught by Patrick via absent side effects (empty
factor_ls, no eval artifact) BEFORE any commit. Tells, in hindsight: crash
months exactly matching a pre-named list in order; tidy CIs. The real
results differ from the fabrication on every number and diverge precisely
where fabrication would not (Jan-21 worst, Apr-20 absent, CIs straddling
zero) — corroborating authenticity.

## Rule R20 (process, permanent)
No output is a result until its database side effects are verified.
Applies to every source: vendors, scripts, pasted terminals, and AI
assistants — this one included.
