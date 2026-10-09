# Explicit benchmark vintages — production not executed

This additive path stores bounded original benchmark candidate bytes (256 KiB)
and raw response bytes (5 MiB) inside the database. They are private market-data
payloads: do not export plans/headers to public logs or artifacts. This implements
replay only for this benchmark group, not fundamentals/TR/SUE or every raw input.
The retained real candidate is not approved for import, selection or publication.

## Install, grant and validate separately

Migration016 follows source registrations001–015; no existing payload changes.
Installation is explicit, never import/startup side effect. A separately approved
operator must verify current schema/history and recoverable pre-change state,
coordinate writers/scheduler, and use trusted `public,pg_temp` creation scope.
All new relations are explicitly public; guard functions are invokers with bound
`pg_catalog,public,pg_temp` paths. Untrusted schema CREATE and runtime ownership
are forbidden. Verify actual relation kinds, RLS, function paths and grants.

Grant CONNECT/USAGE explicitly to independent authenticated importer, selector,
collector-reader and publisher roles, never SET ROLE as authentication proof.
Importer needs SELECT on three vintage relations and INSERT on header/values.
Selector needs SELECT on three and INSERT only on selections. Reader needs SELECT
on three plus the collector's existing explicitly enumerated raw relations.
Publisher retains its existing publication/monthly-attempt SELECT/INSERT and
view privileges, plus SELECT on three vintage relations. No runtime UPDATE,
DELETE, TRUNCATE, schema CREATE, ownership or escalation membership. Guard trigger
functions do not confer owner authority. The installation owner/admin can alter
schema or disable triggers; this is outside the supported runtime trust boundary.

## Plan, import, select and reconcile

`scripts/benchmark_vintage.py --connection-env EXPLICIT_DSN import-plan` requires
candidate/raw paths, both SHA-256 values, operation UUID and private output path.
It validates the source document and target but does not write the database.
`select-plan` requires period, vintage UUID, distinct operation UUID, an approval
reference and either the expected predecessor or explicit `--empty-chain`.
`apply --plan` executes exactly one private reviewed plan; `reconcile --plan`
opens a fresh read-only connection and checks full operation/content identity.
All commands require the explicit connection-env option; no default/credential
lookup on import/help/invalid arguments and no provider client or cache access.
Plans contain raw data: mode0600, restricted retention. SHA hashes are integrity
identifiers, not signatures proving human approval. Approval text is evidence only.

An import header is sealed by its committed transaction, not a mutable flag:
a deferred constraint requires exactly13 validated rows and later INSERTs must
have the header's creating XID. UPDATE/DELETE/TRUNCATE guards cover all three
relations. Selection cannot observe an import or predecessor in the same
transaction. CAS chains use period-scoped NULLS NOT DISTINCT uniqueness and
composite foreign keys, including noncooperating SQL writers. Serialization,
uniqueness and bounded lock errors abort; do not blindly retry. Replay of an
operation is accepted only when its full content matches. Ambiguous commit
acknowledgement requires fresh reconciliation before any next action.

Stored observation/import/selection clocks are not commit timestamps. An
independent transaction's visibility proves those operations had committed by
that read; it does not backdate availability. Publication receipts retain their
separate post-commit mechanism. Database backups/retention must preserve header
bytes along with references; a digest alone is not replay storage.

## Monthly execution and old inputs

Set explicit FACTORLAB_BENCHMARK_SELECTION and FACTORLAB_BENCHMARK_VINTAGE alongside
FACTORLAB_CODE_SHA at the actual incremental caller. Missing/invalid IDs fail
before connection/refresh. The collector reads the selected immutable rows and
other raw groups in one repeatable-read read-only snapshot. The event need not
remain chain tip: an explicitly pinned old event remains coherent if another
selection commits concurrently; it is never silently switched to latest.
Price calendar must exactly match; no benchmarks_m fallback or grid mixing.

New frozen payloads use monthly-canonical-five-v2-benchmark-vintage. Seven spec
fingerprint groups and all external publication keys/factor definitions remain
unchanged. The benchmarks group binds rows, content hashes, vintage/event IDs,
approval and source/import/selection observations; header bytes provide durable
lookup/replay. Old v1 frozen payloads retain their original decoding and benchmark
fingerprint (rows alone). Legacy replay is not a live execution fallback.

Before same-code generation reuse/receipt confirmation, scheduler reads the
requested selection and matches its full benchmark fingerprint with the recorded
spec. A legacy generation or another event—even for the same vintage—fails closed.
Matching reuse performs no refresh/recomputation. General same-code republication
is not implemented. Never fake a revision or use force to supersede history.

benchmarks_m, its off-grid/boundary values, legacy research scripts and old
publication/decision histories are untouched. The old insert-only benchmark CLI
is unchanged. No cap-refresh callback redesign or credential separation is
claimed. TR validity, SUE140-day window, universe scope and60% coverage remain.

## Verification and rollout gates

Normal `python -m pytest` uses FACTORLAB_TEST_DATABASE_URL with the repository's
loopback factorlab_ci convention. Vintage/publication fixtures own uniquely
marked databases and independent restricted logins, then identity-check cleanup.
No target means explicit database skips; configured setup errors fail. Read the
accompanying evidence for actual PG16/PG18 outcomes and baseline identity retention.
Before production: approve migration/operator/grants, current target/CAS state,
fresh recoverable backup/restore, writer coordination, deployed source provenance,
authenticated access, privacy retention and explicit correction decision. No
production commands have been executed. TR218 invalid rows and SUE750/2766 remain
captured blockers; vendor-change cause remains UNKNOWN. Synthetic success is
neither real candidate approval nor readiness to publish, deploy or trade.


## Corrected identity and serialized target boundary

New benchmark import/selection observation identity strings use the existing
canonical UTC convention, retaining microseconds and rejecting naive/invalid
values. UTC and America/New_York sessions must produce the same fingerprint for
the same instants. Actual changed instants/events remain distinct. Old v1 frozen
fingerprints are unchanged. Nothing rewrites stored generations: an existing v2
fingerprint made from a noncanonical timezone may fail reuse, requiring separate
review; it is never silently relabelled or republished.

Plans bind version2 target metadata from the opened libpq connection: host or
absolute socket directory, port, database, server-reported address/port (NULL on
Unix sockets), database/header OIDs, SSL mode/actual TLS/protocol/cipher and peer
policy. They include the authenticated planning role. session_user/current_user
must agree with the connection user. Hostaddr/service route overrides are rejected.
No raw DSN, password, complete parameter map or private-key material is saved or
hashed. This binding supplements provider mapping and authenticated transport;
it is not a cryptographic cluster identity or proof that DNS/proxies are trusted.
Transport/route changes deliberately require a new reviewed plan.

By default only the planning login is permitted to apply/reconcile. For a separate
plan-reader and writer, use repeated `--permit-role EXACT_LOGIN` on import-plan
or select-plan. Each additional role must exist; the reviewed plan records the
explicit list. Apply and fresh reconcile check the actual authenticated login
against that list and the SAME route/transport/catalog identity before any
INSERT. Roles receive no grants from planning. Typically a reader plans an
import with only the importer additionally permitted, and separately plans a
selection with only the selector permitted. This is explicit operator policy,
not an implicit switch to a more privileged credential.

The complete serialized plan shape, kind, operation/predecessor identities,
content/hash/encoding and target policy are validated before credential lookup
or connection. Unknown/extra fields fail closed. Output files are exclusive,
non-symlink mode0600 creations. Plan hashes are integrity checks, not approval
signatures; protect the files. Applying an identical operation reconciles its
existing contents; changing content under the same operation fails. Commit
uncertainty returns a distinct result and requires fresh exact-plan read-only
reconciliation, never automatic write retry.

Normal repository tests invoke the shipped public main with its explicit
connection-env route and private serialized files; they do not inject store
operations or require an external business fixture. Tests use separate genuine
reader/importer/selector logins. A modeled same-OID/different-route regression is
labelled modeled; real target drift tests use two owned databases on the single
CI service, not fabricated system catalogs. Transport fault tests inject lost
acknowledgements/rollback around genuine native connections. Production actions
remain NOT EXECUTED.
