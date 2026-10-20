# Exact-date benchmark repair — operations NOT EXECUTED

The old no-argument script is intentionally replaced. It no longer reads legacy
universe dates, carries prices forward, or updates existing cells. `plan` requires
an explicit completed period: 2026-10 selects September 2026 and twelve preceding
months, using the collector's >=100-price monthly session rule. The bounded
request spans the first through last required session (13 months); there is no
all-history request or fixed end date. The existing `prices_div_adjusted` endpoint
and `adjClose` field remain the dividend-adjusted benchmark contract. No raw-close
fallback is permitted. Provider chunking is unnecessary for this bounded request;
any provider truncation fails exact-grid validation.

Example future operator commands (not authorization to execute):

```
python scripts/benchmark_load.py plan --period 2026-10 --connection-env BENCHMARK_DSN --output reviewed-plan.json
python scripts/benchmark_load.py apply --connection-env BENCHMARK_DSN --plan reviewed-plan.json
python scripts/benchmark_load.py reconcile --connection-env BENCHMARK_DSN --plan reviewed-plan.json
```

Select one explicit connection with host, port, database, user, password and an
approved sslmode/trust policy. Do not put secrets in command arguments. Inherited
libpq environment settings are refused. The loader never uses the fallback DB
helper. Import, help, missing/malformed arguments and unfinished periods perform
no connection/client construction. Planning reads the database and, only when
explicitly invoked, calls the provider; it writes no database rows. The existing
FMP client may update its endpoint-resolution cache during an actual plan call.
No such vendor call was made in the repair tests.

A plan contains all exact values, the original overlap values, target database/
relation OIDs, calendar, normalized payload hash, observation time, source contract
revision and its own deterministic digest. These hashes are integrity evidence,
not historical availability or complete-provider-coverage certification. Preserve
the reviewed plan and the candidate source manifest. OIDs bind the selected target
but are not globally unique server authentication; explicit connection selection
and operational target attestation remain required.

Every response row must have a valid ISO date and finite positive adjClose, and
SPY if symbol is supplied. Truly identical duplicates collapse by numeric value;
conflicting duplicates fail. Non-grid dates are validated and hashed but cannot
replace a missing grid date. No prior-price fill, interpolation or tolerance.

Application uses one SERIALIZABLE owned transaction, a shared advisory lock for
cooperating loader calls, 2s lock and 15s statement bounds. It rechecks schema,
calendar and original overlaps, inserts only missing exact SPY cells, then reads
back the entire grid. Unconstrained NUMERIC is checked against the pinned schema;
Decimal values retain its exact semantics. New identical overlaps are no-ops;
any differing vintage aborts all writes. Off-grid and non-SPY cells are preserved.
Noncooperating concurrent writes are protected by serializable conflict detection
and the primary key. A serialization/unique/lock error is a failed transaction,
never an automatic retry. The advisory lock is not a scheduler exclusion policy.

A commit acknowledgement failure returns UNKNOWN. Do not blindly reapply: use a
fresh connection to reconcile this exact plan. `exact_grid_present` verifies the
observed state, not which actor committed it. Incomplete/conflicting outcomes need
operator review; reconciliation never writes. Interrupted commands also require
fresh reconciliation before deciding any next action.

Required privileges: CONNECT, trusted public USAGE, SELECT on prices_raw_d and
benchmarks_m, INSERT on benchmarks_m, and execution of the built-in catalog and
advisory-lock functions. No ownership, UPDATE, DELETE, TRUNCATE, schema CREATE,
role membership escalation, future-table grants or migration privileges. The
writer rejects non-base benchmark relations, RLS, unexpected columns/types,
missing primary key, application triggers or rules. Trusted source namespace and
restricted login provisioning are separate operational prerequisites.

Before production execution: establish deployed-source identity, refresh owner,
approved connection/reader-writer authentication, scheduler coordination and a
recovery point. Review all adjusted-price overlaps as one vintage; differing
levels require a separate historical-correction decision, not this insert-only
repair. No automatic scheduling or ownership policy is added by this patch.
Captured benchmark gaps, 218 invalid TR rows and SUE 750/2766 coverage remain open.
No raw inputs, factors, publications, jobs or migration records are repaired here.

## Normal repository tests

Install the unchanged `requirements-test.txt` into a dedicated Python 3.12
virtual environment, then run from the Factor Lab repository root:

```
python -m pytest --junitxml=test-results.xml
```

The real `tests/conftest.py` remains active. No private launcher, fake target
module, unshipped fixture plugin or `--noconftest` is required. Without an explicit
`FACTORLAB_TEST_DATABASE_URL`, the 13 benchmark database cases are collected and
explicitly skipped; this is not database acceptance. A configured target's
identity, connection, dependency or setup failure is an error, never a skip.

Use only a newly owned disposable server configured like the existing CI:
loopback `127.0.0.1`, logical database `factorlab_ci`, login `factorlab_ci`, explicit
port and temporary password. Never supply production/application URLs. The
control login needs database/role creation and ownership rights (the existing CI
service uses its isolated bootstrap role). `tests/benchmark_database.py` checks
that identity and creates unique `fl_benchmark_db_<uuid>` databases from template0
and matching restricted `fl_benchmark_writer_<uuid>` LOGIN roles. It records safe
resource intent, tags and checks ownership, closes connections, then drops only
its own tagged resources. It never recreates the control database or modifies
other tests' schemas. An ambiguous ownership/cleanup failure is reported, not
resolved with broad or forced removal.

Each case uses the pinned migration-10 benchmark table and the same minimal
synthetic calendar/legacy fixtures. Runtime connections use the separate writer
password and SELECT/INSERT privileges; no setup role is returned to the test.
Required cleanup rights belong only to setup. A killed test process can leave its
recorded names for operator reconciliation on that disposable server.

Focused commands, using the same normal repository configuration:

```
python -m pytest tests/test_benchmark_loader.py tests/test_benchmark_loader_postgres.py
python -m pytest tests/test_scheduler.py tests/test_benchmark_loader.py
```

Import/help regressions instrument each actual invocation, including an already
imported client, to prohibit credential lookup, client/cache use, database and
network access. They neither delete another test's module nor require file order.
Local evidence may add a safety-only native endpoint/network guard; it supplies
no fixture or application behavior. CI execution and live refresh remain separate.
