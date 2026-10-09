# Monthly publication protocol v1 — code-only draft

## Scope and status

This is an additive storage/coordination/reader implementation, not a production
recovery. It does not run on import, obtain credentials, apply migrations, fetch
vendor data, replace legacy tables, grant privileges, or activate trading.
Factor Lab migration 014 is registered as source text; production remains untouched.
Migrations 001–013 are byte-for-byte pinned by regression tests.

The first repair (fundamentals binding and scheduler failure reporting) remains
intact. This stage implements a reusable monthly publication boundary and real
PostgreSQL tests. **The legacy monthly producer is not yet redirected to it and
MT3 has not yet been changed to consume it. Keep PR #1 draft and unmerged.**
Do not mistake the new library's passing tests for a repaired production panel.

## Contract

`PublicationStore(connect)` requires a factory returning a new idle psycopg2
connection for every call. It never reads DATABASE_URL or other environment
credentials. Production connection ownership and restricted grants must be
reviewed separately. Do not use a reconnecting wrapper or return pooled/borrowed
connections: the attempt's dedicated session owns an advisory lock until close.

`store.publish(spec, producer)` records a durable attempt, invokes an explicitly
supplied producer, validates its output, writes one date-scoped generation in a
single transaction, then confirms it from a fresh transaction. No retry of the
producer or an uncertain commit is automatic.

The specification identifies:
- `monthly_factor_panel_v1`, exactly one `asof` date and the source Git revision;
- fingerprints for universe, fundamentals, returns, surprises, benchmarks,
  classifications and factor registry from the producer's actual frozen inputs;
- when those inputs were observed (aware UTC, not in the future);
- independently planned universe/investable counts;
- the existing five pinned factor definitions and their formula hashes.

Fingerprint presence proves identification, not upstream completeness. The producer
must supply independently verified coverage and input provenance; this library does
not fabricate those claims. The publication result is explicitly non-authoritative.
The implementation does not alter factor formulas or their coverage policy.

The application canonicalizes timestamps and numeric representations without
rounding high-precision values. PostgreSQL independently validates the request,
assigns its SHA-256 identity from JSONB, and overwrites caller-supplied creation,
seal and availability timestamps. The client-side spec fingerprint is an audit
fingerprint, not a substitute for the server request ID. JSONB encoding is part of
the database protocol; cross-version export/import is a separate reviewed process.

## Immutable relations

- `fl_publication_requests`: frozen input/code/contract/scope specification.
- `fl_publication_attempts`: one immutable start per attempt.
- `fl_publication_outcomes`: one terminal result: published, reused, failed or
  interrupted. Prior attempts are never deleted or overwritten.
- `fl_dataset_generations`: one committed output generation per request identity.
- `fl_universe_generations`: full dated universe with frozen sector and size bucket.
- `fl_factor_generations`: every investable security × the five required factors.
- `fl_publication_receipts`: first fresh-transaction observation of a complete
  committed generation, plus database-computed universe and factor hashes.
- `fl_published_datasets`: reader-visible generations with receipts, not staging.

Every table rejects UPDATE, DELETE and TRUNCATE. Child inserts are allowed only in
the creating generation transaction and before its terminal outcome. A deferred
generation constraint checks exact counts, membership, factor IDs, date agreement
and an associated published outcome at commit. Early `SET CONSTRAINTS` cannot
permit extra children after validation. These guards do not defend against a DBA
who disables triggers; production writer permissions are a release prerequisite.

## Structural readiness and legitimate missingness

A fully published panel has exactly the planned universe/investable counts and
one explicit factor record per investable member per required factor. All five
must have at least one nonnull consumed value; this is rejection of an entirely
missing panel, not a newly invented acceptable-coverage percentage.

A missing consumed value is NULL, never zero. It needs an explicit reason from
`not_applicable`, `insufficient_history`, `source_value_missing`, or
`insufficient_peer_group`. Real zeros remain values. The raw/rank/sector/size
lineage cannot contain normalized values without raw input. All numbers must be
finite. Non-investable members cannot contribute factor values.

`readiness(asof, cutoff)` reports exact-date availability, per-factor nonnull counts
and missing-reason counts. It does not certify source completeness, calculation
correctness, strategy eligibility, or trading authority. The producer's registered
calculation/coverage gates remain mandatory before production publishing.

## Failure, concurrency and retry

All cooperating publishers serialize an asof scope with the same session advisory
lock (namespace 214011). Another active owner receives `publication_scope_busy`, not
a fabricated success. The dedicated connection spans the start/outcome/data
transactions. Process/session loss releases its lock; the next owner can mark
unfinished starts interrupted, preserving the old evidence, before a new attempt.
A still-live but wedged lock is not forcibly stolen. Review/terminate it through an
explicit operational procedure rather than a silent time-based takeover.

Generation, universe, factor rows and the successful outcome commit together. A
failure during any output batch rolls them back, leaving the previous publication
visible. The already-committed attempt can receive a failed outcome in a separate
transaction. If connection/commit acknowledgment is uncertain, no automatic replay
occurs; an unresolved start or already committed outcome is left for reconciliation.
A callback could have performed external side effects; the library cannot roll
those back. Producers should be pure computation over a frozen read-only snapshot.

An exact retry still validates and compares its proposed rows against the immutable
generation. Changed output under the same request identity is a hard conflict, not
an update. A genuinely changed source revision/fingerprint creates a separate
request/generation. Attempt history and original data remain intact.

## Availability is not the sealing timestamp

An INSERT-time or transaction-start timestamp precedes commit, potentially by a
long interval. It must not be used to claim that a dataset was available to an
older decision. The generation stores `seal_xid`, but its `sealed_at` is **not** the
reader's availability boundary.

After the entire generation committed (including deferred validation), a fresh
transaction must actually see it. A receipt trigger refuses same-transaction
confirmation and captures the database clock after observing the committed row.
`available_at` therefore means **known complete by this observation**, a conservative
upper bound on the dataset's commit visibility. It is not a claim that the later
receipt row had already committed at that instant, or that vendor data existed at
its economic asof. Normal readers require the receipt to be visible too.

If the coordinator crashes after the generation commit but before confirmation,
the new dataset is not selected by the published view. A later retry creates a
receipt with its later observation time; it does not backdate one. Repeated
confirmation never changes the first receipt. Delayed confirmation of an older
generation cannot outrank a newer sealed generation for the same date.

`select(asof, cutoff)` requires the exact date, existing factor contract, and a
receipt at or before the cutoff. It freezes one generation ID before reading its
immutable rows. It never falls back from August to July or combines two generations.
The September 1 / September 2 regression exercises this contract, not a rewritten
MT3 historical decision. Existing decisions remain outside the write scope.

## Integration and deployment blockers — still open

1. Adapt the monthly universe/factor producer to one repeatable-read frozen input
   snapshot and independently planned date/scope, then publish through this API.
   Do not merely copy stale legacy outputs and call them validated. Remove the
   legacy delete/rebuild chain from the scheduled path before merging.
2. Validate raw fundamentals, SUE, benchmarks, classifications and price/TR inputs;
   preserve formula/coverage semantics and test numerical parity. Seven hashes do
   not prove that seven inputs were fresh or complete.
3. Wire scheduler attempts to this protocol without deleting or reinterpreting old
   job_log rows. This stage supplies durable **publication** attempts; it does not
   yet replace all daily/weekly/monthly job claiming.
4. Update MT3's selectors, source revision and health to the publication contract;
   add a consumer-level regression and preserve frozen runs. The library test is
   not proof that the deployed worker now enforces it.
5. Review restricted producer/reader grants, staged schema compatibility, backup
   restoration and an isolated data-recovery rehearsal. Production merge,
   migration, deployment and recovery need separate approval.

No production operation is included in this document. CI uses synthetic rows in
isolated loopback PostgreSQL databases. Tests against those rows cannot certify
current vendor data or the contents of the old Railway image.
