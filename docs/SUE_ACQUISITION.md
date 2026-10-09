# Bounded SUE acquisition and proposals

REAL ACTIONS NOT EXECUTED. No database apply, scheduler integration, migrations,
shared client/cache, or legacy backfill invocation exists in this path.

Run from the repository root with `python -m scripts.sue_acquisition --help`.
The public lifecycle is `plan`, `acquire`, `reconcile`, `propose`. The shipped
synthetic tests demonstrate serialized files and public main with injected
transport, then fresh reconciliation. No real endpoint qualification is implied.

## Explicit inputs

All input/output files must be regular non-symlink 0600 files in user-controlled
0700 directories. Create a new private root yourself. `plan --scope S --selected
IDS --limits L --output P` takes three bounded JSON files. No defaults select an
acquisition sweep. Source examples are in `tests/test_sue_acquisition.py` and use
only SYNTHETIC identifiers.

Scope fields: period YYYY-MM (the previous completed month), economic asof in that
previous month, scope_identity and before_identity (explicit source references),
semantics `UNVERIFIED_PROVIDER_DATE_AND_PIT`, and members. Each member has id,
eligible, held, marker (absent/present/empty/failed), windows (symbol,start,end),
before (date,actual,estimate,sue), and before_complete. Numeric lexemes may be
strings. All supplied before-state fields enter the plan hash; source references
are not independent authentication. `before_complete=false` makes absent dates
UNKNOWN. Unseen columns are not compared. All eligible members remain in coverage;
NULL differs from zero and negative usable SUE. Markers never exclude requests.

Held entries remain held and cannot be selected. Multiple symbol windows require
an explicit hold; this implementation does not resolve or compose them. Reversed,
overlapping or tied windows are retained as flags only on explicitly held members;
active ambiguities are refused. Cross-security overlaps require both members held.
Malformed date/type input always refuses. Unknown before-state coverage stays UNKNOWN.
Disjoint reuse shares one symbol request with separate date attribution. Global
mapping outside the explicit scope remains unknown. Nothing extrapolates newest
symbols. The 1609/407 diagnosis is not a built-in scope or authorization.

Limits must explicitly provide logical requests, physical attempts, retries (0–2),
seconds (1–7200), spacing (at least 0.15 seconds), response_bytes (at most 5 MiB).
The plan binds the full frozen scope, subset, before-state, limits, exact endpoint,
formula contract and implementation file hashes. No DB OIDs or live CAS is invented.

## Acquisition and retention

`acquire --plan P --root ROOT --operation ID --approval REFERENCE
--credential-env NAME` is the only provider-capable operation. It uses ONLY the
named environment value and `/stable/earnings?symbol=...&limit=120`; no fallback,
pagination, cache, netrc/inherited proxies or redirects. TLS verification stays on.
An approval reference is operator evidence, not cryptographic approval or permission
supplied by this code review. No real acquisition was performed in implementation.

Requests are sequential with global spacing, bounded timeouts/backoff, a saved
campaign deadline, and total attempt accounting across resumes. Transient transport,
429 and 5xx have at most two identical retries. 401/403, detected quota exhaustion,
schema drift and other refused HTTP statuses stop the campaign. Each request has
an intent before submission, then an exclusively published raw body/hashed receipt.
Native blocking reads can finish at their finite socket timeout after a campaign
deadline; no subsequent request is allowed. Time measurements are observation
intervals, never commit timestamps or historical availability.

The store keeps binding, numbered intents, exact safe HTTP200 bytes, receipt and
validation hashes. Empty, nonempty, incomplete-history, request/schema failure,
identity-held and interrupted-unknown states stay distinct. A nonempty upcoming or
unusable panel is not empty success. Failures never become ingest markers. Keys,
credential-shaped responses and echoed authenticated URLs are not retained or
hashed as a redacted substitute; such observations are non-replayable failures.
Unexpected transport exception text is not output. Raw provider metadata is private.

`reconcile --plan P --root ROOT --operation ID --output R` validates content/hashes
without credentials/network. Missing receipt after intent/body stays unknown;
`--retry-unknown NEW_REFERENCE` explicitly authorizes only remaining budget, retaining
that old uncertain attempt. A stale lock or incomplete atomic-publication temporary
requires manual review: no automatic deletion or request replay. Receipt-stage
interruption reconciles without any new request. A new observation cannot overwrite
completed bytes; changed plans conflict. Permissions, hashes and append-only APIs
are not filesystem/WORM protection or protection against a malicious file owner.

## Calculation/proposal boundaries

`propose --plan P --root ROOT --operation ID --output Q` retrieves only successful
hash-verified observations, then calculates from that response alone within each
symbol interval. No baseline EPS hole filling or mixed-response composition exists.
Unknown optional fields/raw bytes are retained, absent required fields are rejected,
identical parsed duplicates have a count receipt, conflicting same-date rows refuse.
Post-asof events do not enter history. Provider date/lastUpdated fields do not prove
report/fiscal semantics or pre-event estimate availability.

The formula is unchanged: native float(actual)-float(estimate), four preceding valid
differences minimum, latest eight, population pstdev, sd>1e-9, round4, append current
difference afterward. Nonfinite conversion/results refuse. This is not the Decimal50
TR policy. Null pairs remain missing. Limit120 is never proof of complete history.
Every proposal remains non-applicable with explicit unknown history/date/PIT gaps.

Exact supplied EPS/SUE comparisons classify no-op, insert, correction or unknown
before-state. The output privately includes all available chronological events,
preceding changed dates, unreturned baseline dates, deterministic content-derived
operation IDs, and an unknown forward boundary. It never emits a production-approved
isolated correction or overwrites unseen columns. Calculation completeness and
new usable observations do not redefine or replace the frozen coverage denominator.

Future database application requires separate current-target reads/authentication,
coordinated writers, recoverable before-state, CAS, atomic durable operation receipts,
uncertain-commit reconciliation, and publication replay/selection policy. None is
implemented here; 60% coverage and all existing economic rules remain unchanged.

## Tests

Use ordinary `python -m pytest` with the existing explicit loopback
FACTORLAB_TEST_DATABASE_URL convention for the baseline PostgreSQL tests. New SUE
tests never connect to a database and mock transport/session and clocks. Import/help
and malformed commands retain the real conftest and network/credential isolation.
No private fixture injection or --noconftest is needed. The exact local manifests
and JUnit accompany the review packet; hosted CI was not run.

## Lifecycle correction: v2 formats (REAL ACTIONS NOT EXECUTED)

This section supersedes the earlier single-file proposal and synchronous Requests
limitations above. Document version is now `sue-plan-v2` throughout. V1 documents
and missing observation lineage are rejected, never silently migrated or backdated.
Keep the original implementation/receipts for their original-version interpretation.

`propose --output PATH` writes a small final manifest at PATH and bounded private
files under PATH.shards/. That directory has an immutable intent, one hash-named
shard per selected security, and a completion record. PATH plus its matching
completion/shards is the complete proposal. There is no success manifest before
all shards exist. Interrupted shard/complete/final-publication states resume from
existing observations; bytes must match exactly and no provider request occurs.
A repeat of a complete identical proposal is a verified no-op, not an overwrite.
Changed plan/observation, corrupt shard or conflicting output fails closed.

Per-file bound remains5MiB. At most10,000 security shards and64GiB aggregate bound;
the recorded tighter campaign bound is (selected-count+2)*5MiB. Each response is
still at most5MiB/120 events. Calculation holds one response/security at a time,
not all campaign rows or payloads. Receipt descriptors remain bounded by the
explicit30,000-attempt maximum. Before acquisition, statvfs verifies conservative
space for maximum raw attempts plus proposal bounds plus64MiB reserve on the
private acquisition root. It is a headroom check, not a reservation. A different
proposal filesystem is checked incrementally; later disk failure preserves partial
files and does not create a complete output. A single unusually large security
shard can still fail its5MiB bound truthfully; no rows are truncated. Such a
resource failure needs review, not another download. The50×120 regression completes
all6,000 events. A bounded small in-memory preview is not the CLI output path.

Dependencies are linear-size: each shard stores one ordered changed-date vector;
each event stores its prefix count and up to8 preceding valid EPS-difference dates.
This recovers every preceding changed date without copying quadratic prefix lists.
All available events, before-state comparisons and unknown downstream boundaries
remain represented. This does not establish missing provider history or readiness.

Observation envelopes bind campaign/operation and approval reference, campaign hash,
request intent/hash, independently retained HTTP status/hash, receipt/hash, exact
raw payload hash and observation start/end. Shards embed this lineage; manifest
references bind every shard and observation. Same observed receipt is deterministic.
Identical raw bytes in another operation keep equal payload/economic-content hashes
but different observation and event operation identities. This alone does not change
no-op/insert/correction classification. Hashes are not authentication, human approval,
or historical availability. EPS/date/PIT semantics remain unverified.

The default HTTPS adapter now constructs a Requests session inside one owned POSIX
fork worker per request. No shell/exec/credential argv/file is used. The child clears
its environment, disables trust_env, verifies TLS and refuses redirects. Status is
sent independently and durably saved before body handling. Known401/403 and other
permanent refusals do not read their bodies or retry. Quota detection stops429;
other429/5xx and transport failures retain bounded transient retry policy.

The parent polls with a monotonic deadline even when headers/body/decompression do
not yield. It terminates then, if necessary, kills only its Process object, joins
with bounded waits and refuses unreaped cleanup. Cancellation is an unknown-request
outcome, not an automatic retry. The tests use actually blocked child processes and
no-network synthetic Sessions; they are not live network measurements. Blocking
provider/decode work ends with that worker. Parent parsing is bounded by5MiB/120 rows;
its elapsed time is also charged. Process cleanup may add up to roughly1.1seconds
to the request budget; it does not authorize another request. The implementation
requires POSIX fork and is intended for a single foreground CLI, not arbitrary
multithreaded embedding. Abrupt external destruction of the controller/host remains
a manual reconciliation case, not proof a request never happened.

Campaign wall deadline is durable, wall rollback refuses, and completed attempts
charge monotonic elapsed plus spacing; missing receipts conservatively charge their
reserved request allowance. Resumes use the smaller remaining wall/cumulative/
current-monotonic budget. A reset process clock cannot reset request counts or
previous charges. Explicit retry-unknown approval is required after cancellation;
no blind duplicate occurs. A durable terminal status prevents retries even if a
receipt was interrupted. Injected synthetic transports are a test seam; normal CLI
acquire always uses the owned HTTPS adapter.

Public main tests exercise serialized plan -> fake acquire -> fresh reconcile ->
sharded proposal, private modes, tampering, partial resumption and exact coverage.
The original82 test identities remain; small test-only previews obtain v2 envelopes
through actual synthetic acquisition/reconcile. The old adapter options test now
inspects `_read` directly; separate tests prove its owned-process boundary. Original
no-overwrite public replay now checks identical verified output/no-op. No numerical
or privacy assertion is removed. No real acquisition, DB apply or hosted CI occurred.
