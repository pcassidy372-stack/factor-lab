"""Register + freeze the canonical five (spec s9). Definitions are immutable
once frozen; changes require a new version by construction."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

DEFS = [
    ("ebit_ev", "value", "+", "EBIT_ttm / (mktcap + total_debt - cash)",
     {"excl_financials": True, "ev_floor": "max(100e6, 0.05*mktcap)", "ttm_q": 4}),
    ("gp_a", "quality", "+", "gross_profit_ttm / total_assets_latest",
     {"excl_financials": True, "ttm_q": 4}),
    ("accruals", "quality", "-", "(NI_ttm - CFO_ttm) / avg(total_assets, total_assets_4q_ago)",
     {"excl_financials": True, "ttm_q": 4}),
    ("asset_growth", "quality", "-", "total_assets_latest / total_assets_~1y_ago - 1",
     {"excl_financials": True, "yoy_window_days": [300, 430]}),
    ("mom_12_1", "momentum", "+", "tr(m-1) / tr(m-12) - 1 on month-end grid",
     {"min_months": 12}),
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
cur.execute("SELECT factor_id, formula_hash, prior_sign FROM factor_definitions ORDER BY 1")
print("registry:", cur.fetchall())
cx.close()
