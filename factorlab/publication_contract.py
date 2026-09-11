"""Date-scoped publication contract; no database, network, or environment access.

These checks establish structural readiness, not vendor completeness or trading
permission. Input fingerprints must come from a separately validated producer.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

DATASET = "monthly_factor_panel_v1"
CORE_FACTORS = {
    "gp_a": {"version": 1, "formula_hash": "924506ea504b239e"},
    "mom_12_1": {"version": 1, "formula_hash": "d7a21fca19ecd4da"},
    "net_issuance": {"version": 1, "formula_hash": "260d5cec83fe0d55"},
    "sue": {"version": 1, "formula_hash": "a94ea425e90066d0"},
    "vol_12m": {"version": 1, "formula_hash": "91b06eaf0881f996"},
}
INPUTS = frozenset({"universe", "fundamentals", "returns", "surprises",
                    "benchmarks", "classifications", "factor_registry"})
MISSING_REASONS = frozenset({"not_applicable", "insufficient_history",
                           "source_value_missing", "insufficient_peer_group"})
SPEC_KEYS = frozenset({"dataset", "asof", "source_revision", "inputs_observed_at",
                       "input_fingerprints", "factor_contract",
                       "expected_universe", "expected_investable"})
UNIVERSE_KEYS = ("asof", "security_id", "mktcap", "adv_63d", "price",
                 "in_universe", "size_bucket", "sector")
FACTOR_KEYS = ("asof", "security_id", "factor_id", "raw", "rank_norm", "z_sector",
               "z_sector_size", "missing_reason")


class PublicationError(ValueError):
    """Stable, credential-free error code. Do not attach provider payloads."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise PublicationError(code)


def day(value: Any) -> date:
    if type(value) is date:
        return value
    require(isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)),
            "invalid_asof")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise PublicationError("invalid_asof") from None


def instant(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        require(isinstance(result, datetime) and result.tzinfo is not None
                and result.utcoffset() is not None, "naive_or_invalid_timestamp")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise PublicationError("naive_or_invalid_timestamp") from None


def numeric(value: Any) -> str | None:
    if value is None:
        return None
    require(not isinstance(value, bool) and isinstance(value, (str, int, float, Decimal)),
            "invalid_number")
    try:
        n = Decimal(str(value))
        require(n.is_finite(), "nonfinite_number")
        # Bounds protect against unbounded numeric/text allocation, not signal selection.
        require(abs(n.adjusted()) <= 1000 or n == 0, "numeric_size_limit")
        text = format(n, "f") if n != 0 else "0"
        return text.rstrip("0").rstrip(".") if "." in text else text
    except (InvalidOperation, ValueError, OverflowError):
        raise PublicationError("invalid_number") from None


def normalize_spec(spec: Mapping[str, Any]) -> dict:
    require(isinstance(spec, Mapping) and set(spec) == SPEC_KEYS, "invalid_spec_keys")
    require(spec["dataset"] == DATASET, "unknown_dataset_contract")
    require(isinstance(spec["source_revision"], str)
            and re.fullmatch(r"[0-9a-f]{40}", spec["source_revision"]) is not None,
            "invalid_source_revision")
    fps = spec["input_fingerprints"]
    require(isinstance(fps, Mapping) and set(fps) == INPUTS, "missing_input_fingerprints")
    require(all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in fps.values()),
            "invalid_input_fingerprint")
    contract = spec["factor_contract"]
    require(isinstance(contract, Mapping) and dict(contract) == CORE_FACTORS,
            "factor_contract_drift")
    n, m = spec["expected_universe"], spec["expected_investable"]
    require(type(n) is int and type(m) is int and 0 < m <= n <= 100000,
            "invalid_expected_scope")
    return dict(spec, asof=day(spec["asof"]).isoformat(),
                inputs_observed_at=instant(spec["inputs_observed_at"]).isoformat(),
                input_fingerprints=dict(sorted(fps.items())),
                factor_contract=json.loads(json.dumps(CORE_FACTORS)))


def spec_fingerprint(spec: Mapping[str, Any]) -> str:
    """Client audit fingerprint; server identity uses PostgreSQL JSONB encoding."""
    return hashlib.sha256(json.dumps(normalize_spec(spec), sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def prepare_rows(spec: Mapping[str, Any], universe: Iterable[Mapping],
                 factors: Iterable[Mapping]) -> tuple[list[dict], list[dict]]:
    s = normalize_spec(spec)
    urows, frows, ids, factor_ids = [], [], set(), set()
    for row in universe:
        require(isinstance(row, Mapping) and set(row) == set(UNIVERSE_KEYS), "universe_shape")
        r = dict(row)
        require(day(r["asof"]).isoformat() == s["asof"], "universe_date_mismatch")
        r["asof"] = s["asof"]
        sec = r["security_id"]
        require(type(sec) is int and 0 < sec <= 2147483647 and sec not in ids, "duplicate_or_invalid_security")
        require(type(r["in_universe"]) is bool, "invalid_membership_boolean")
        require(isinstance(r["sector"], str) and 0 < len(r["sector"]) <= 120, "missing_sector")
        for name in ("mktcap", "adv_63d", "price"):
            r[name] = numeric(r[name])
        if r["in_universe"]:
            require(all(r[k] is not None and Decimal(r[k]) > 0
                        for k in ("mktcap", "adv_63d", "price")), "invalid_investable_inputs")
            require(r["size_bucket"] in ("small", "mid", "large"), "missing_size_bucket")
        else:
            require(r["size_bucket"] is None, "excluded_size_bucket")
        ids.add(sec)
        urows.append(r)
        require(len(urows) <= s["expected_universe"], "universe_count_mismatch")
    require(len(urows) == s["expected_universe"], "universe_count_mismatch")
    members = {r["security_id"] for r in urows if r["in_universe"]}
    require(len(members) == s["expected_investable"], "investable_count_mismatch")
    valid = {fid: 0 for fid in CORE_FACTORS}
    for row in factors:
        require(isinstance(row, Mapping) and set(row) == set(FACTOR_KEYS), "factor_shape")
        r = dict(row)
        require(day(r["asof"]).isoformat() == s["asof"], "factor_date_mismatch")
        r["asof"] = s["asof"]
        sec, fid = r["security_id"], r["factor_id"]
        require(type(sec) is int and sec in members, "factor_outside_investable_scope")
        require(isinstance(fid, str) and fid in CORE_FACTORS, "unknown_factor")
        require((sec, fid) not in factor_ids, "duplicate_factor")
        for name in ("raw", "rank_norm", "z_sector", "z_sector_size"):
            r[name] = numeric(r[name])
        if r["z_sector_size"] is None:
            require(isinstance(r["missing_reason"], str) and r["missing_reason"] in MISSING_REASONS,
                    "unexplained_missing_factor")
        else:
            require(r["missing_reason"] is None and all(r[k] is not None
                        for k in ("raw", "rank_norm", "z_sector")), "invalid_factor_lineage")
            valid[fid] += 1
        if r["raw"] is None:
            require(all(r[k] is None for k in ("rank_norm", "z_sector", "z_sector_size")),
                    "normalized_without_raw")
        factor_ids.add((sec, fid))
        frows.append(r)
    require(len(frows) == len(members) * len(CORE_FACTORS), "factor_count_mismatch")
    require(all(valid.values()), "all_null_required_factor")
    return sorted(urows, key=lambda r: r["security_id"]), sorted(frows, key=lambda r: (r["security_id"], r["factor_id"]))


def eligible_receipt(receipt: Mapping[str, Any], asof: date, cutoff: datetime) -> bool:
    """No prior-month fallback and no substitution of economic date for availability."""
    return (day(receipt["asof"]) == day(asof)
            and instant(receipt["available_at"]) <= instant(cutoff)
            and receipt["factor_contract"] == CORE_FACTORS)
