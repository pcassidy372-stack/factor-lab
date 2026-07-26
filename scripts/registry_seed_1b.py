"""Register + freeze the 1b five (spec s9 breadth). Additive; canonical five untouched."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

DEFS = [
    ("bp", "value", "+", "equity_latest / mktcap",
     {"excl_financials": False}),
    ("net_issuance", "quality", "-", "shares_dil_latest / shares_dil_~1y_ago - 1",
     {"excl_financials": False, "yoy_window_days": [300, 430]}),
    ("sue", "events", "+", "latest SUE with report_date <= asof, staleness <= 140d",
     {"excl_financials": False, "staleness_days": 140}),
    ("vol_12m", "low_risk", "-", "stdev(trailing 12 monthly TR returns) * sqrt(12)",
     {"excl_financials": False, "min_months": 12, "note": "monthly proxy; daily idio-vol on candidates list"}),
    ("beta_36m", "low_risk", "-", "cov(r, SPY_m)/var(SPY_m), trailing 36m (min 24)",
     {"excl_financials": False, "min_months": 24}),
]

cx = conn()
cx.autocommit = True
cur = cx.cursor()
for fid, fam, sign, formula, params in DEFS:
    h = hashlib.sha256((formula + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:16]
    cur.execute("""INSERT INTO factor_definitions
        (factor_id, version, family, formula_text, formula_hash, params, prior_sign)
        VALUES (%s, 1, %s, %s, %s, %s, %s) ON CONFLICT (factor_id) DO NOTHING""",
        (fid, fam, formula, h, json.dumps(params), 1 if sign == "+" else -1))
cur.execute("SELECT count(*), string_agg(factor_id, ',' ORDER BY factor_id) FROM factor_definitions")
print("registry:", cur.fetchone())
cx.close()
