# factor_lab — Build Spec v0.2

Supersedes v0.1. This revision executes the July 2026 institutional review: every item adopted, adopted-with-modification, or declined with reasons (Appendix A). The architecture changes are real, not cosmetic — identity, bitemporal lineage, return economics, and alpha/risk separation are redesigned.

---

## 0. v0.1 errata (owned, not buried)

1. **Symbol as primary key** — wrong. Tickers change, get reused, and split across share classes; 15 years of history joined on symbol silently corrupts. Fixed via security master (§3).
2. **"The factor model is the risk model"** — wrong as stated. Sector-neutralized alpha exposures push industry covariance into specific variance, understating the risk of precisely the bets the optimizer takes. Alpha and risk models are now separate artifacts (§12).
3. **Cost calibration by turnover band** — backwards. Costs are estimated; turnover emerges (§13).
4. **adjClose semantics asserted, not proven** — v0.1 claimed FMP adjClose is split-only-adjusted. Unproven. v0.2 builds total return from raw prices + corporate-action events and demotes vendor-adjusted series to reconciliation oracle (§6).
5. **Backfill PIT conflation** — append-only vintages make the system point-in-time *prospectively*. Whether backfilled history is genuinely point-in-time depends on vendor vintage-preservation behavior, which must be proven, not assumed (§5, Phase −1).

---

## 1. Design principles

1. **Identity before history.** No price or statement row is ingested until it resolves to a durable `security_id`. Symbols are presentation attributes.
2. **Point-in-time everything — with backfill honesty.** Factor computation at asof `t` reads only rows with `accepted_date <= t`. Backfilled rows are additionally labeled by their epistemic status: *vendor-current-view* until Phase −1 proves vintage preservation; live-accumulated rows are gold-standard PIT.
3. **System time ≠ event time.** Every row carries both when the information became public (`accepted_date`) and when this system first observed it (`observed_at`). These are different facts and both are stored.
4. **Vintages, never overwrites.** Append-only; restatements insert new vintages; originals persist.
5. **Registered definitions before evaluation.** Frozen, versioned, hashed factor registry.
6. **Replication gate before novelty.** Canonical anomalies must reproduce — with external oracles (§11) — before any novel claim.
7. **Honest returns.** Self-built total return from raw prices and corporate actions; delisting economics resolved by hierarchy, not convenience; net-of-cost portfolio reporting.
8. **Selection happens inside the window.** Any clustering, factor selection, or composite weighting is estimated walk-forward on trailing data only.
9. **Zoo-wide multiple-testing discipline.** |t| > 3 for standalone claims; BH-FDR across the registered set; post-hoc ideas queue for the next registered window.

---

## 2. System architecture

| Component | Role | Cadence |
|---|---|---|
| Security master | Identity resolution, symbol history, corporate-action linkage | Continuous; seeded in Phase 0 |
| Ingestion workers | Backfill + incremental pulls into raw/vintage tables | Daily (prices), weekly (estimates snapshot), filing-driven (statements, 13F, Form 4) |
| Universe builder | PIT investable universe snapshots | Monthly |
| Factor engine | Registered factors, standardization, neutralization variants | Monthly, T+1 |
| Evaluation engine | IC, quantiles, Fama–MacBeth, clustering, OOS protocol | Monthly + registered study runs |
| Alpha model | Walk-forward composite from registered factors | Phase 4 |
| Risk model | Industry + style exposures, factor-mimicking returns, Σ | Phase 4, parallel artifact |
| Optimizer + portfolio backtest | Construction ladder, cost model, net-of-cost results | Phase 5 |

Deploy: separate Railway service + Postgres. `railway run` local-execution and `/tmp/x.py` assert-patching workflows transfer unchanged.

---

## 3. Identity: the security master (proportionate two-tier)

```sql
issuers    (issuer_id PK, cik, name)
securities (security_id PK, issuer_id FK, isin, cusip, share_class,
            first_seen, last_seen, status)
symbol_map (security_id FK, symbol, exchange, valid_from, valid_to,
            PRIMARY KEY (symbol, valid_from))
```

- **Every downstream table keys on `security_id`.** Symbol→security resolution happens once, at ingestion, using the `symbol_map` row effective at the observation date.
- Anchors: CIK (issuer level) + ISIN/CUSIP from profile data (security level). Seed `symbol_map` from FMP's symbol-change feed plus the delisted-companies list; extend forward as changes arrive.
- Ambiguous resolutions (reused ticker, missing anchors) go to a quarantine queue for manual adjudication — never guessed silently.
- **Deliberately declined:** the full three-tier issuer/security/listing model. Scope is US-listed common stock, overwhelmingly single-listing; two tiers with ISIN/CUSIP anchors carries it. Revisit if ADRs or multi-class complexity enters the universe (dual-class collapses to one investable line per class, which `securities.share_class` already represents).

---

## 4. Data model (revised DDL sketch)

```sql
-- identity: §3

universe_snapshots (asof, security_id, mktcap, adv_63d, in_universe,
                    size_bucket, PRIMARY KEY (asof, security_id))

prices_raw_d   (security_id, d, open, high, low, close, volume,
                PRIMARY KEY (security_id, d))          -- UNADJUSTED
corp_actions   (security_id, ex_date, action_type,     -- split | div_cash |
                ratio, amount, meta JSONB)             -- div_special | spinoff | ...
tr_index_d     (security_id, d, tr, method_version)
mktcap_m       (asof, security_id, mktcap)             -- PIT market cap

fundamentals_q (security_id, fiscal_period_end,        -- valid time
                accepted_date,                         -- public-knowledge time
                observed_at,                           -- system time (first seen HERE)
                vintage_id, backfill BOOL,
                accession_no, source_hash, mapping_version,
                revenue, gross_profit, ebit, ni, cfo, capex, total_assets,
                total_debt, cash, equity, shares_dil, ...,
                raw JSONB,
                PRIMARY KEY (security_id, fiscal_period_end, vintage_id))

estimates_snapshots (asof, security_id, target_period, eps_mean, rev_mean,
                     n_analysts, n_up, n_down)         -- target_period is calendar-anchored
surprises           (security_id, report_date, eps_actual, eps_est, sue)
insider_txns        (security_id, filing_date, txn_date, insider, txn_code,
                     shares, value, is_10b5_1)
inst_ownership_q    (security_id, quarter_end, report_date, shares_held,
                     chg_shares, n_holders)

delistings     (security_id, delist_date, delist_reason,
                terminal_return, terminal_method)      -- §6

factor_definitions (factor_id PK, version, family, formula_text, formula_hash,
                    params JSONB, validity_rules JSONB, registered_at, frozen BOOL)
factor_values      (asof, security_id, factor_id,
                    raw, rank_norm, z_sector, z_sector_size, z_industry,
                    PRIMARY KEY (asof, security_id, factor_id))
factor_evals       (factor_id, asof, horizon, ic, q_spread, turnover)
fm_coefficients    (run_id, asof, factor_id, beta, t_stat)
risk_exposures     (asof, security_id, exposure_id, value)
risk_factor_rets   (d, exposure_id, ret)               -- weekly/daily mimicking returns
runs               (run_id PK, kind, spec JSONB, git_hash, window_role, created_at)
                                                       -- window_role: dev | validation | holdout
-- phase 5: cov_estimates, portfolios, weights, portfolio_returns, cost_estimates
```

**Vintage rule (unchanged, now bitemporal):** at asof `t`, per (security_id, fiscal_period_end), use the greatest `vintage_id` with `accepted_date <= t`. `observed_at` and `backfill` determine the row's epistemic status; `accession_no` + `source_hash` tie it to the filing and detect silent vendor rewrites on re-pull.

---

## 5. PIT lineage and the backfill honesty rule

- **The problem, precisely:** `accepted_date` proves a filing preceded asof. It does not prove the *stored numbers* are what a vendor user would have seen at that historical moment — the vendor may serve today's standardized interpretation of an old period.
- **Endpoint preference:** for vintage-critical raw values, prefer the as-reported statement endpoints (keyed to actual filings, carrying accession identifiers); use standardized endpoints for convenience fields. Both land in `raw JSONB`.
- **Phase −1 vintage-preservation proof:** for a sample of known-restated quarters, compare FMP's served historical values against the original filing's XBRL facts (SEC EDGAR, free). Outcome is a documented verdict: *vintages preserved* / *current-view served* / *mixed by field*. This verdict — not hope — determines how backfilled history is labeled.
- **Strict-PIT mode:** every study can run in (a) *backfill-trusting* mode or (b) *strict-PIT* mode using only live-accumulated vintages. Mode is recorded in `runs.spec`. Strict-PIT power grows every month the system runs; headline claims eventually migrate there.
- **Re-pull drift detection:** periodic re-fetch of a sample of old quarters; `source_hash` mismatch on an unchanged accession = vendor rewrote history = logged event, new vintage, and a data-quality flag.

---

## 6. Prices, total return, and delisting economics

**Construction:** store unadjusted OHLCV + corporate-action events; build `tr_index` from raw closes with splits applied and dividends reinvested on ex-date. Never trust a pre-adjusted vendor series as input.

**Oracle reconciliation:** FMP's dividend-adjusted price surface is the external oracle. Tolerance test per security: |self-built TR − vendor TR| within tolerance on ≥ 95% of overlapping days; failures quarantined and diagnosed (usually a missed special dividend or spinoff).

**Event coverage to prove in Phase −1:** regular cash dividends, special dividends, return of capital, stock dividends/splits, spinoffs. **Spinoff convention (documented):** value spun-off shares at their first close and treat as reinvested cash-in-kind on distribution date; flag affected names.

**Delisting hierarchy** (replaces v0.1's last-trade convention):
1. **Merger/acquisition:** terminal value = deal consideration at close (cash at terms; stock converted at acquirer's closing price). Sourced from the M&A corporate-events table — the pattern already built for the momentum tracker transfers.
2. **Bankruptcy / performance delisting:** terminal return = −100% base case, with a mandatory sensitivity band (−30% Shumway-style to 0%) reported alongside any small-cap or value claim.
3. **Unknown reason:** return to last trade, name flagged, and the claim must survive the exclusion diagnostic.
Excluding delist months remains a diagnostic, never the primary treatment. `delistings.terminal_method` records which rung applied.

---

## 7. FMP surface (revised)

| Data need | Endpoint family | Key fields | Notes |
|---|---|---|---|
| Identity anchors | profile; symbol-change feed; delisted-companies | CIK, ISIN, CUSIP, ticker changes | Seeds the security master. |
| Quarterly statements (vintage-critical) | financial-statements-**as-reported**, `period=quarter` | filed values, accession linkage, acceptedDate | PIT backbone; raw JSONB retained. |
| Quarterly statements (convenience) | income/balance/cash-flow standardized | curated line items | Mapped fields; `mapping_version` recorded. |
| PIT market cap | historical-market-capitalization | date, marketCap | Never recompute from today's shares. |
| Prices | historical EOD **unadjusted** | OHLCV | Raw store. |
| Dividends & splits | dividend + split calendars/history | ex-dates, amounts, ratios | Feed `corp_actions`. |
| TR oracle | dividend-adjusted EOD surface | adjusted series | Reconciliation only, never input. |
| M&A terms | M&A / corporate events | consideration, close date | Delisting rung 1. Pattern exists. |
| Survivorship | delisted-companies | symbol, date | Names persist in history. |
| Sector/industry | profile (+ monthly snapshot forward) | sector, industry | §10 variants; EDGAR SIC as Phase −1 cross-check. |
| Estimates | analyst-estimates (+ weekly snapshots) | per-date estimates | Calendar-anchored `target_period` to kill FY-roll artifacts. |
| Surprises | earnings-surprises | actual, estimate, date | SUE backfillable day one. |
| Insider | insider-trading feed | txn codes, filing dates | Filter to open-market codes; exclude exercises/gifts/withholding; flag 10b5-1. Accepted date = filing date. |
| 13F | institutional ownership by symbol | held, change, holders | Accepted date = report/filing date (45-day lag). |
| Shares | shares float history + statement counts | shares over time | Net issuance; reconcile sources. |
| Risk-free | treasury rates | 3M/10Y | Excess returns. |

Path caveat stands: v3/v4/stable drift is isolated in `fmp_client.py`; drift costs a mapping update, not a re-backfill.

---

## 8. Universe

Monthly PIT snapshot: US-listed common stock, primary line per share class; exclude ETFs/funds/ADRs (v1). `mktcap >= $300M` PIT, 63-day median dollar volume ≥ $2M, price ≥ $3. Breadth target 2,000–2,800; `size_bucket` terciles stored. Delisted names persist in every snapshot they qualified for, with §6 terminal economics. Financials remain **in the universe** but are excluded per-factor by validity rules (§9) — universe membership and factor applicability are separate concepts.

---

## 9. Factor library v1 + economic-validity rules

TTM rule: four quarters, all `accepted_date <= asof`. Balance items: latest accepted. EV = PIT mktcap + total_debt − cash.

| Factor | Formula | Family | Prior | Validity rules |
|---|---|---|---|---|
| EBIT/EV | EBIT_ttm / EV | Value | + | EV > max($100M, 5% of mktcap); exclude financials |
| FCF yield | (CFO − capex)_ttm / EV | Value | + | Same EV floor; exclude financials |
| Earnings yield | NI_ttm / mktcap | Value | + | — |
| Book/Price | equity / mktcap | Value | + | Financials included (it's their natural metric) |
| Sales/EV | revenue_ttm / EV | Value | + | EV floor; exclude financials |
| Gross profitability | GP_ttm / total_assets | Quality | + | Exclude financials |
| ROIC | NOPAT_ttm / (debt + equity − cash) | Quality | + | Frozen policy: effective tax clipped [0, 45%]; negative EBIT → NA (not winsorized in); ending capital v1; goodwill included; leases as-reported; exclude financials |
| Accruals (Sloan) | (NI − CFO)_ttm / avg assets | Quality | − | Exclude financials |
| Asset growth | Δ total_assets yoy | Quality | − | Exclude financials v1 |
| Net issuance | Δ shares yoy | Quality | − | — |
| Momentum 12-1 | tr(t−21)/tr(t−252) − 1 | Momentum | + | ≥ 200 valid trading days |
| [reserved] mt_v2 | frozen spec import post item-9 | Momentum | + | — |
| SUE | (actual − est)/σ(8q surprises) | Revisions | + | ≥ 4 historical surprises |
| Revision 3m | Δ consensus EPS, calendar-anchored blend of FY1/FY2 weighted by months-to-fiscal-end | Revisions | + | ≥ 3 analysts; activates as snapshot history accrues |
| Estimate breadth | (n_up − n_down)/n | Revisions | + | ≥ 3 analysts |
| Idio vol 252d | σ residual vs sector | Low-risk | − | — |
| Beta 252d | vs SPY TR | Low-risk | −/flat | — |
| Insider net 6m | net open-market $ / mktcap | Flows | + (v1.1) | Codes P/S only; exclude 10b5-1-flagged |
| 13F breadth Δ | Δ holders qoq | Flows | + (v1.1) | — |

Validity rules live in `factor_definitions.validity_rules` and produce NA, not extreme ranks. A name failing a factor's validity rule is absent from that factor's cross-section — it does not become a winsorized tail.

---

## 10. Standardization & neutralization

Per asof, per factor, universe members passing validity rules:

1. Coverage filter: skip (factor, asof) below 60% of eligible names.
2. Winsorize 1/99.
3. Rank-normal transform (registered choice).
4. **Store all variants:** `raw`, `rank_norm`, `z_sector`, `z_sector_size`, `z_industry`. No single canonical neutralization — studies declare which variant they use, in the registry.
5. Marginality machinery (evaluation-time): Fama–MacBeth multivariate for conditional contribution; hierarchical clustering (distance = 1 − |ρ|) for redundancy. **Both walk-forward when they feed selection or weighting** (§11). Sequential Gram–Schmidt remains a study option, not a default.

---

## 11. Evaluation engine + out-of-sample protocol

**Core metrics (unchanged):** Spearman IC at h ∈ {21, 63, 126, 252}d with Newey–West lags ≥ overlap; ICIR; IC decay. Decile portfolios, EW, next-executable rebalance; D10−D1 spreads; block bootstrap (pattern transfer). Fama–MacBeth via `linearmodels`, verified against hand-rolled OLS.

**Window protocol (new):**
- **Development window:** backfill start → T−6y. Engineering, debugging, unlimited inspection.
- **Validation window:** T−6y → T−3y. Limited, *declared* research decisions; each logged in `runs` with `window_role='validation'`.
- **Quarantined holdout:** last 3 years. Sealed from Phase −1 onward; opened only at named release gates (composite v1, portfolio v1). Applies to **novel factors and composites** — canonical replication factors are exempt (their holdout was burned by four decades of literature; registration + t>3 governs them).
- **Walk-forward mandate:** clustering, factor selection, and composite weights estimated on trailing windows only, applied next period. Full-sample selection is disallowed by construction, not by discipline.

**External oracles (new, Phase 1 gate):**
- Constructed 12-1 long-short should correlate strongly with the Ken French momentum (UMD) monthly series over the overlap; value spread directionally consistent with HML; GP/A positive per Novy-Marx. Material divergence = plumbing suspect, not "new finding."
- `alphalens-reloaded` as a test-time cross-check on one factor's IC/quantile numbers; not a runtime dependency.

**Zoo discipline (unchanged):** |t| > 3 standalone; BH-FDR across the registered set; candidates wait for registered windows.

---

## 12. Alpha model ∥ risk model (separated)

**Alpha model:** expected-return score from registered factors. Weights = walk-forward ICIR weighting or walk-forward cluster representatives. Output: cross-sectional score, refreshed monthly. Validated as its own artifact (holdout gate applies).

**Risk model (separate artifact):**
- Exposures: market beta, log-size, sector dummies (**un-neutralized** — industry belongs in the risk model, not residualized out), 252d volatility, 12-1 momentum, B/P. Standardized cross-sectionally.
- Factor returns: weekly cross-sectional regressions of returns on exposures → factor-mimicking return series.
- Covariance: Ledoit–Wolf on the weekly factor-return series.
- Specific variance: EWMA of regression residuals per name, shrunk toward size×sector group means.
- Σ = X_risk F X_risk′ + D, sanity-checked against realized name-level and portfolio-level vol before first use.
- Deliberate overlap (value, momentum appear in both models) is normal; the models answer different questions.
- **Alignment diagnostic:** report the share of alpha score orthogonal to risk exposures each rebalance — the optimizer's appetite concentrates there, so it must be measured, not discovered.

---

## 13. Portfolio construction & transaction costs

**Construction ladder** (each rung must beat the previous, net of costs, to justify its complexity):
1. Equal-weight top decile of composite.
2. Capped score weighting (w ∝ score, 3% cap).
3. Risk-scaled score weighting (score / σ_specific).
4. HRP on Σ.
5. Constrained MVO (cvxpy/OSQP): max α′w − λ·w′Σw − expected_cost(Δw), s.t. long-only v1, w ≤ 3%, sector net |≤| 5%, β ∈ [0.8, 1.2], fully invested.

**Cost model (estimated, not tuned):**
- Half-spread per name via Corwin–Schultz high-low estimator on stored OHLC, bucketed by size×liquidity, floored at tick.
- Impact = k · σ_daily · √(trade $ / ADV), k = 1 initial, sensitivity ±50% reported.
- Constraints: participation ≤ 5% ADV per rebalance, minimum trade size, no trading through halted/stale prints (staleness from the raw price store).
- Turnover **emerges**; break-even cost per strategy reported (the cost level at which net alpha = 0).

Portfolio backtest remains a separate artifact from factor evaluation: monthly, next-executable, net-of-cost, vs SPY / QQQ / EW-universe.

---

## 14. Stack

Python 3.12; pandas + numpy (polars only if profiling demands); `statsmodels` (HAC), `linearmodels` (FamaMacBeth), `scipy.cluster.hierarchy`, `scikit-learn` (LedoitWolf), `cvxpy` + OSQP; Postgres via the momentum tracker's established driver choice. Ken French data library pulled as flat files for oracle tests. Compute is CPU-bound; Railway + `railway run` covers it.

---

## 15. Tests

1. **PIT assertion:** random (security_id, asof, factor) draws → every contributing row `accepted_date <= asof`. CI.
2. **Vintage immutability:** re-ingest a restated quarter → new vintage, original untouched, pre-restatement factor values unchanged.
3. **Identity continuity:** ticker-change names (Phase −1 sample) resolve to one `security_id` across the change; price and fundamental joins survive.
4. **Vintage-preservation verdict test:** the Phase −1 XBRL comparison, encoded as a repeatable script with a documented verdict artifact.
5. **TR oracle:** self-built TR vs vendor dividend-adjusted within tolerance on ≥ 95% of name-days for the torture sample; every failure diagnosed.
6. **Delisting economics:** each hierarchy rung exercised by at least one sample name; terminal values match documented method.
7. **FM oracle:** `linearmodels` vs hand-rolled on synthetic panel.
8. **External replication:** UMD correlation and anomaly-sign checks (§11) as a gate script.
9. **Determinism:** identical inputs + frozen registry → identical `factor_values` hash.

---

## 16. Build sequence v2

| Phase | Objective | Exit gate (measurable) |
|---|---|---|
| **−1 — Vendor & data proof** | ~80-name torture sample: restatement-heavy names, ticker changes, bankruptcies, cash and stock mergers, spinoffs, dual-class, special-dividend/ROC payers, financials, negative-EV, recent IPOs, fiscal-year changers. Prove: vintage preservation (XBRL comparison), TR semantics, identity continuity, delisting resolvability, units/currency sanity. | Written verdicts: vintage-preservation status; TR reconciliation ≥ 95% of name-days; identity continuity 100% of sample; delisting terminal value resolvable ≥ 90% of sample. |
| **0 — Identity + bitemporal foundation** | Security master seeded; bitemporal schema; statements backfill (10–15y, as-reported preferred) with backfill labeling; raw prices + corp actions + TR engine; PIT mktcap; universe snapshots; delistings table. | Tests 1–6 green; universe counts smooth through time (no coverage cliffs); holdout sealed. |
| **1 — Canonical five** | B/P or EBIT/EV, GP/A, accruals, asset growth, 12-1 momentum. Pipeline steps 1–4; IC + quantile engine. | **Replication gate incl. external oracles (§11).** |
| **1b — Registered v1 breadth** | Remaining value/quality/momentum/low-risk factors with validity rules. | Same bar per factor. |
| **2 — Marginality + OOS protocol live** | Fama–MacBeth, walk-forward clustering, registry frozen v1; first registered findings (marginal-t table, redundancy map) on dev+validation windows only. | Findings doc published; zero holdout touches (audited via `runs`). |
| **3 — Events & flows** | SUE backfill; revisions snapshotting live; insider (filtered) + 13F ingestion and factors. | Event-timing proofs (filing-date lags verified on sample); same evaluation bar. |
| **4 — Alpha ∥ risk models** | Walk-forward composite; separate risk model (§12); alignment diagnostic. | Composite passes validation window; Σ implied vs realized vol reconciles at name and portfolio level; each model a separate validated artifact. |
| **5 — Construction & paper track** | Ladder rungs 1→5; cost model; net-of-cost backtests; break-even-cost reporting. | Holdout gate opened for composite + portfolio; each rung justified vs previous; paper track begins. |
| **6 — Decision point** | Momentum-book integration question: which mt_v2 names carry independent quality/valuation/revision/ownership support, and which are expensive volatile factor bets. Allocation-layer unification decided on Phase 5 evidence. Productization gate (incl. FMP licensing + employment-agreement review) attaches **here**, not Phase 0. | Explicit go/no-go memo. |

Phases −1 and 0 are the platform's foundation and carry the largest budget; the review's core message — *data lineage is the model* — is accepted in full.

---

## 17. Known dragons (updated)

- **Vendor vintage behavior** — now a Phase −1 empirical question with a documented verdict, not an assumption in either direction.
- **Restatements** — solved by bitemporal vintages; re-pull drift detection guards the vendor side.
- **Identity** — solved proportionately (§3); quarantine queue absorbs the ugly cases.
- **Sector classification** — current-snapshot primary + monthly snapshots forward + EDGAR SIC cross-check in Phase −1; multiple neutralization variants stored so no single classification is load-bearing.
- **Delisting economics** — hierarchy + mandatory sensitivity bands on small-cap/value claims.
- **Spinoffs** — documented convention, flagged names; genuinely hard, honestly bounded.
- **Estimate history depth** — revisions factors may only mature 6–12 months post-launch; SUE covers events meanwhile.
- **Financials** — in universe, out of incompatible factors via validity rules; sector-specific constructions are a candidates-list item, not v1.
- **Overlapping-horizon inference** — NW lags scale with overlap, enforced in the eval engine.
- **The zoo** — registry + t>3 + FDR + sealed holdout + walk-forward selection. Five locks on one door, because it's the door everything escapes through.

---

## Appendix A — Review disposition ledger

| # | Review item | Verdict | Reason |
|---|---|---|---|
| 1 | Symbol cannot be primary identity | **Adopted (modified)** | Correct and pre-backfill-critical. Implemented as two-tier security master with CIK/ISIN/CUSIP anchors; full issuer/security/listing three-tier declined as disproportionate for US common stock — revisit on ADR/multi-class scope change. |
| 2 | PIT may be false confidence; bitemporal + accession anchoring | **Adopted** | The review's best catch. v0.1 conflated prospective PIT with backfill PIT. Bitemporal fields, accession/source-hash lineage, Phase −1 XBRL proof, strict-PIT mode, backfill labeling all added. XBRL as *standing dependency* declined — it's an oracle and anchor, not a second data pipeline. |
| 3 | TR construction needs endpoint-level proof | **Adopted** | v0.1's adjClose assertion was overconfident. Rebuilt: raw prices + corp actions → self-built TR; vendor dividend-adjusted series demoted to reconciliation oracle; special/ROC/spinoff cases in the torture sample. |
| 4 | Delisting treatment economically incomplete | **Adopted** | Shumway logic is correct. Hierarchy implemented (merger consideration → bankruptcy imputation with sensitivity band → flagged last-trade); exclusion demoted to diagnostic. |
| 5 | Economic-validity rules per factor | **Adopted** | All cheap, all real: EV floors, financials exclusions, frozen ROIC policy, calendar-anchored revisions, insider code filtering. Encoded in `validity_rules`, producing NA not tail ranks. |
| 6 | OOS design not strong enough | **Adopted (modified)** | Walk-forward for anything that selects/weights: adopted as a construction constraint. Dev/validation/holdout windows: adopted. Holdout for canonical replication factors: **declined** — those anomalies' holdout was burned by the literature decades ago; registration + t>3 governs them. Holdout binds novel factors and composites, where it has teeth. |
| 7 | Alpha model ≠ risk model | **Adopted** | v0.1's "elegant payoff" was wrong in exactly the stated way: neutralized alpha exposures push industry covariance into specific risk. Separate risk model built (§12), proportionate Barra-style: industry + style, weekly mimicking returns, shrunk specific variance. Full commercial-grade risk model declined as scope; the separation principle adopted in full. |
| 8 | Cost calibration backwards | **Adopted** | Correct. Estimate spread (Corwin–Schultz from stored OHLC) and impact (√-participation) independently; turnover emerges; break-even cost reported. Turnover-band tuning deleted. |
| 9 | Static sector classifications | **Adopted (modified)** | Multiple neutralization variants stored; EDGAR SIC as Phase −1 cross-check; monthly snapshots forward. PIT SIC as *primary* classification declined — SIC is too coarse to canonize; risk ranked below items 1–4 and bounded by the variant store. |
| 10 | Commercial licensing as Phase 0 dependency | **Adopted (conditional)** | For a personal-capital tool, not a Phase 0 blocker. Attached as a hard gate on the Phase 6 productization decision, alongside the open employment-agreement review. Derived-analytics vs data-redistribution distinction noted for that gate. |
| — | Phase −1 vendor torture test | **Adopted** | The single best idea in the review; scaled from v0.1's single-symbol spike into a measurable proof with written verdicts. |
| — | Five canonical factors first | **Adopted (modified)** | Phase 1 narrowed to five; remaining v1 factors follow as 1b under the same gate. |
| — | Construction complexity ladder | **Adopted** | Capped-score and risk-scaled rungs inserted before HRP/MVO; each rung must earn its complexity net of costs. |
| — | Commercial-edge framing (explainability, diagnostics, momentum integration) | **Noted, parked** | Product-layer, not architecture. The momentum-integration question is adopted verbatim as Phase 6's framing question. |
