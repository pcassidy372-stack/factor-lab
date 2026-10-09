# TR precision policy — candidate, not deployed

`tr-level-decimal50-v1` uses Decimal arithmetic with 50 significant digits,
ROUND_HALF_EVEN, independently of ambient decimal context. It computes the
existing gross factor `(current_close * split_ratio + cash) / previous_close`
directly, avoiding cancellation from subtracting and adding one. No fixed
six-decimal quantization, clamp, epsilon, rebase or historical correction occurs.
Each step persists exactly the Decimal state used by the following step;
restart from that NUMERIC value reproduces uninterrupted calculation.

TR storage is unconstrained NUMERIC NOT NULL in the reviewed schema. Existing
consumers convert to float: levels outside its finite positive range fail
explicitly rather than being stored as an unusable zero/infinity. This is not
support for every mathematically positive real or arbitrary NUMERIC exponent.
Upstream existing float parsing remains; Decimal(str(value)) does not recover
lost vendor precision or establish corporate-action correctness.

Zero seeds, nonfinite/invalid inputs, nonpositive prior price/split, negative
cash, and seed/price-date mismatches fail. Zero gross requires terminal review,
not a fabricated positive continuation; zero close with sufficient cash can
still produce positive gross. No terminal-event repair policy is introduced.

Backfill/repair keep their existing 100 normalization and strict oracle seam
selection thresholds. The incremental path validates its existing stored seed
and date and computes all rows before queuing price/TR inserts. Destructive
repair commands are not run and are not newly authorized by this patch.

Economic labels tr-v1-close-div-split and tr-v2-window-priority stay unchanged,
as do consumer allowlists, factor formulas, SUE gates and publication history.
The helper policy identifies numerical precision in code, not per-row writer
provenance. A new persisted precision/method identity and consumer allowlist
change require a separate reviewed decision. New arithmetic is not bitwise
identical to historical float/round6 output and does not repair old zero seeds.

Run `python -m pytest tests/test_tr_precision.py` using declared test dependencies.
Tests use synthetic arithmetic and extracted writer function bodies with SQL
recorders, not database execution. No claim of live DB roundtrip validation is
made. Normal repository conftest remains active; the saved task launcher also
denies native DB connections, network and subprocesses before collection.
