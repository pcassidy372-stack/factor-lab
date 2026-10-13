# Factor Lab reliability repair: code-only draft

Base commit: `5d0c939e2deb98474dc4971acc79dacb4b1783e1`.
Base tree: `33629a87510767bcc694860ebcb9ac35a041ef91`.
Branch: `repair/factor-lab-reliability-20260911`.

## Status and authorization

This is the first tested implementation on the approved repair branch, not a
completed data recovery or a production-ready release. Approval covers code,
regression tests, and a draft pull request only. Do not merge, deploy, run these
jobs against Railway, change authority, or force a historical job from this PR.
No production fixture or credential belongs in this repository.

## Implemented

- Weekly INSERT uses named parameters, including the omitted `source_hash`.
  The original m1 hash algorithm, zero-value handling, and R8 lag classification
  are preserved. Currency cannot shift into the hash placeholder.
- Vintage lookup and insertion run in one transaction under a per-security
  transaction advisory lock. Identical replays do not insert; changed values
  append a new vintage. This serializes this writer path, not arbitrary legacy
  scripts. A failed/uncertain commit is not automatically retried or reported as
  success; a subsequent explicit retry reconciles the stored identity.
- Incomplete statement sets are counted without inserting partial vintages.
  Identity conflicts, malformed responses, and conflicting duplicate statements
  fail explicitly. `nodata`, `unchanged`, new vintages, and revisions are distinct.
- A statement exception no longer becomes an overall `weekly ok`. Estimates can
  accrue independently, but the parent job still fails when statements fail.
  Per-period transactions may commit before a later period fails; on an error,
  inserted counters are not a complete durable reconciliation. Readback is
  required before recovery, rather than treating the error as a total rollback.
- Calendar-fetch failures stop daily TR extension before using an incomplete
  action feed. Child subprocess nonzero exits, timeouts and start failures are
  checked. The factor chain stops at the first failed stage and does not run a
  second sampled gate. Monthly source errors/empty scopes fail explicitly.
- Parent process failures exit nonzero. Persisted errors contain a type, safe
  SQLSTATE where available, and fixed stage codes, not raw exceptions or child
  output. Result summaries are no longer truncated at 400 characters.
- `--force JOB` selects only that job; it does not bypass its durable claim.

## Tests and limits

Pure tests exercise SQL parameter maps, hash/currency separation, replay, append
semantics, R8 boundaries, malformed/incomplete inputs, false-success prevention,
child errors, secret redaction, and scheduling. The orchestration suite imports
the actual scheduler and substitutes external I/O dependencies explicitly.

The PostgreSQL suite uses the real migration-001 schema in a unique temporary
schema with synthetic issuer/security rows. It checks real parameter binding,
fresh-connection retry, preservation of prior vintages, concurrent identical
writers, and rollback after both application and constraint exceptions.

Only `FACTORLAB_TEST_DATABASE_URL` may enable integration tests. It must explicitly
name host `127.0.0.1`, database `factorlab_ci`, and user `factorlab_ci`; remote hosts
and DSN options/service/hostaddr overrides are rejected before connection. Tests
remove inherited application URLs, libpq settings and FMP keys, and prohibit live
provider HTTP requests. Teardown drops only the UUID-named schema it created.

The test-only GitHub Actions workflow targets PostgreSQL 16 and 18 on Python 3.12
with disposable containers, contents-read permissions, no application secrets,
and no deployment steps. Local skips are not integration passes. CI is a test of
synthetic behavior, not production data or recovery acceptance.

## Mandatory merge/deployment blockers

1. **Versioned dataset publication is not implemented here.** The legacy monthly
   path still calls the full rebuild. `universe_build.py` still deletes and
   batch-writes the legacy universe; `factor_compute_v2.py` still replaces the
   factor panel. Do not use them as a production recovery. Prepare date-scoped
   generations, validate before atomic publication, and preserve prior datasets.
2. **Attempt history and recovery policy remain unresolved.** The existing claim
   function is not an append-only attempt ledger. A claimed month is not output
   certification; deleting old job records or using `--force` is not authorized.
3. **PIT availability remains unresolved.** This patch preserves the existing
   vendor accepted dates and `value_pit` flags; it does not certify historical
   value availability. Consumers must enforce observed/publication timestamps.
   Newly observed revisions cannot be retroactively available at old cutoffs.
4. **Exact-date factor health remains unresolved.** Require coverage for the exact
   universe generation, finite values and explicit missing reasons. Historical
   registry entries must not certify a current all-null monthly factor panel.
5. **Raw SUE and benchmark refresh ownership requires validation.** Recomputing
   derived factors alone cannot refresh their stale upstream inputs.
6. **MT3 consumer correction is separate and not changed by this PR.** Bind
   universe and factor reads to the same published source and cutoff. Add the
   September 1 cutoff/September 2 publication regression; preserve old decisions.
7. **Recovery operations require separate approval and preparation.** Verify an
   actually restorable Factor Lab backup and rehearse on an isolated Factor Lab
   recovery database. The MT3 testing database is not interchangeable.
8. **Deployment provenance requires verification.** Future images and produced
   data must identify reviewed source. The old deployed bytes remain unverified;
   a Git commit date alone does not prove which bytes were uploaded earlier.

Keep this PR draft and unmerged while those blockers are resolved or scoped into
an explicitly reviewed release plan. Strategies, `elig_deal`, certified absence,
live deal authority, orders, positions, and portfolio snapshots are unchanged.
No migration file or Railway configuration file is modified.

## Test command (disposable local/CI resources only)

```sh
python -m pip install -r requirements-test.txt
python -m pytest
```

There is deliberately no production migration, reset, backfill or force command.
