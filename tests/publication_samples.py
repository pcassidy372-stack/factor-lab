"""Synthetic fixtures; no vendor records or production credentials."""
import copy
import hashlib
from factorlab.publication_contract import CORE_FACTORS, DATASET, INPUTS


def sample():
    spec = {"dataset": DATASET, "asof": "2020-08-31", "source_revision": "1" * 40,
            "inputs_observed_at": "2020-09-02T13:43:00+00:00",
            "input_fingerprints": {k: hashlib.sha256(k.encode()).hexdigest() for k in INPUTS},
            "factor_contract": copy.deepcopy(CORE_FACTORS),
            "expected_universe": 3, "expected_investable": 2}
    universe = [dict(asof=spec["asof"], security_id=s, mktcap=500000000,
                     adv_63d=3000000, price=10, in_universe=True,
                     size_bucket="small" if s == 1 else "large", sector="Synthetic")
                for s in (1, 2)]
    universe.append(dict(asof=spec["asof"], security_id=3, mktcap=None,
                        adv_63d=None, price=1, in_universe=False,
                        size_bucket=None, sector="Unknown"))
    factors = [dict(asof=spec["asof"], security_id=s, factor_id=fid,
                    raw=0 if s == 1 else 1, rank_norm=-1 if s == 1 else 1,
                    z_sector=-1 if s == 1 else 1, z_sector_size=-1 if s == 1 else 1,
                    missing_reason=None) for s in (1, 2) for fid in CORE_FACTORS]
    return spec, universe, factors
