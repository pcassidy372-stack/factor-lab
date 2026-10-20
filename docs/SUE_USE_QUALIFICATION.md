# Option A: explicitly observed revised-history research

PRODUCTION AND REAL DATA NOT ENABLED. The user selected Option A for research; this
is not an endorsement of investment performance, particular records or trading.
Original pre-announcement consensus remains unsupported by this endpoint alone.
No original pilot flags, receipts, plans or proposals are rewritten.

`scripts.sue_use_assessment` is a credential-free command with explicit --root,
--manifest (SHA-256 filename), --cutoff (aware timestamp) and --output. It reads
private content-addressed package files, checks hashes/links and writes one new
0600 assessment exclusively. It never acquires data, applies rows or discovers
credentials. Help/argument failures do not construct a client. File roots must be
0700, nonsymlink paths; files 0600. Each original document is limited to 5MiB;
package aggregate is 32MiB, at most 5000 referenced files / 1000 subjects. Hashes
prove content consistency, not independent factual truth or human approval.

The `sue-observed-research-use-v1` manifest contains version, policy reference,
original plan reference and subjects (security_id, symbol, observation, issuer,
events references). A package contains this manifest and a map of content hash
to exact UTF-8 document bytes. The explicit local loader follows only hash names,
never an arbitrary path or URL. Missing, tampered or swapped evidence refuses.
The frozen input retains the package itself, enabling hash-verified replay instead
of retaining an opaque digest alone. Long-term production evidence retention and
authenticated trust decisions are not implemented by this filesystem interface.

Policy requires the Option A use/version, research approval reference, aware
availability timestamp and an external evidence source record. Observation files
reference exact raw bytes and the unchanged v2 lineage. Original acquisition code
identity validation remains unchanged: new modules do not forge or reseal old
plans. Incompatible older acquisition code needs its original version authority;
this evaluator does not bypass plan_mismatch. External evidence records identify
an explicit review reference and retained record, not a guessed FMP field.

Issuer evidence binds the intended numeric security and requested current ticker
holder over the observation interval; same issuer and evidenced continuous renames
are supported, previous-holder reuse is refused. Existing holds remain. Every
attributed pre-asof event must have a resolved date/event linkage and explicit
matching accounting/currency/share-basis evidence. Unknown fallback/rescheduling
is refused. This conservative first contract requires one compatible basis across
the returned history for a security; no splice, automatic rescale or second split
adjustment exists. More complex legitimate basis changes need separate review.

Actual receipt completion, later event validation and policy availability establish
the no-earlier-than boundary. lastUpdated stays in the original payload as provider
last-checked metadata and is never used as availability. Option A does not require
pre-release capture or an announcement timestamp; it does require an evidenced
release date/link sufficient for the economic event window. Original-consensus
use refuses. UTC conversion preserves aware timestamp microseconds; naive times
refuse. Timestamp strings beyond six fractional digits refuse rather than silently
truncate. Exact numeric row identity is independent of ambient Decimal precision. Numeric
identity strings are bounded to 4096 digits and exponent magnitude 4096 before
expansion; out-of-bound evidence refuses without rescaling. These are input resource
bounds, not a change to the SUE calculation. CLI refusals emit only fixed safe codes. Publication receipt availability and downstream cutoff remain additional
requirements, not replaced by research_cutoff.

Rows and all pre-asof supporting history are recalculated with the existing native
prior4/latest8 population SD, 1e-9 threshold, round4 calculator. NULL/empty and
insufficient-history outcomes remain distinct; zero/negative SUE remain valid.
No subset replaces full scope or the 60% denominator. This package must exactly
match both current SUE rows and supporting history read in one repeatable-read
snapshot; an omitted/changed DB row refuses. Full historical matching is deliberately
conservative; this patch is not an ingestion or correction implementation.

New publishing uses monthly-canonical-five-v3-sue-use. prepare_publication and
calculate_factors require evidence; v1/v2 are accepted only by explicit diagnostic
replay. replay_monthly returns data and old fingerprints, not a publishing callback.
The generic PublicationStore is unchanged: privileged direct DB writes or arbitrary
external producers are not prevented by this Python application guard.

The existing surprises fingerprint group now binds the validated assessment and
exact row/history/evidence/policy/observation identities; the other six groups and
all external spec/factor keys stay unchanged. The scheduler compares this identity
before early confirm/reuse. Matching reuse does not refresh or capture; missing
legacy or changed identities refuse, including equal prices with new observations.
Existing generations/receipts/decisions are never relabelled or rewritten.

The actual incremental monthly caller requires an explicit package and cutoff.
Its normal production resolver deliberately raises
`sue_use_production_resolution_unavailable` before a monthly refresh. Synthetic
fixtures pass complete packages explicitly. This is a tested acceptance/refusal
boundary, NOT an enabled forward research service. No acquisition, production
resolver, migration, schema, grant or MT3 contract change is provided.

## Exact receipt-time qualification (supersedes v1 assessment semantics)

`sue-observed-research-use-v2-exact-epoch` compares original numeric observation
start/finish lexemes as exact rational seconds, independent of ambient Decimal
precision. Aware cutoff/issuer timestamps are converted using integer days,
seconds and microseconds, never float or total_seconds(). Both issuer interval
edges use exact numbers; the start is NOT rounded upward for containment.

The displayed `not_before` uses the FIRST representable microsecond at or after
receipt completion (ceiling), then the later policy/event-validation constraint.
Exact seconds/microseconds stay exact. For example, 1599048000.0000004 completes
0.4 microseconds after 12:00:00 UTC; that cutoff refuses, and its displayed floor
is 12:00:00.000001 UTC. Negative epochs and second/day rollover follow the same
mathematical ceiling. Direct calculation/publication uses that conservative floor;
publication receipt availability remains separate. lastUpdated has no authority.

Clock strings are at most 128 characters; exact decimal epochs have at most 64
coefficient digits and exponent magnitude 64 (integer inputs bounded to 256 bits).
Finite values must fall within Python's supported aware UTC datetime range,
0001-01-01 through 9999-12-31T23:59:59.999999. Oversized/out-of-range clocks refuse
before exponent expansion, with fixed safe codes. Normal submicrosecond values
are supported. The aware-string UTC contract remains unchanged (up to six digits).

This is a new assessment/policy version and identity, not a change to the original
acquisition/receipt schema or bytes. Old v1 assessments/packages cannot silently
serve as new v2 qualification; a new explicit policy/assessment is required. Do not
reseal original observations, reuse a stale assessment, or rewrite publications.
No real qualified publication exists from these tests. Synthetic documents alone
are rebuilt for boundary tests. Legacy frozen monthly v1/v2 replay is unchanged.
All production-resolution, 1000-subject/32MiB/full-history equality limits remain.
