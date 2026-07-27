"""Phase 6: register the momentum tracker's systematic screen kernel."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

fid, fam, formula = "trk_core_1m", "tracker", \
    "tr(asof)/tr(asof-1m) - 1; sector-relative via standard pipeline (z_sector). " \
    "Kernel of momentum_tracker_v2's 21/30-bar screen on the monthly grid. " \
    "prior_sign +1 per tracker thesis; literature predicts reversal (-) at this horizon - " \
    "the printed sign adjudicates."
params = {"excl_financials": False, "window_m": 1, "note": "screen-not-selection; B7 context"}
h = hashlib.sha256((formula + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:16]
cx = conn()
cx.autocommit = True
cur = cx.cursor()
cur.execute("""INSERT INTO factor_definitions
    (factor_id, version, family, formula_text, formula_hash, params, prior_sign)
    VALUES (%s, 1, %s, %s, %s, %s, 1) ON CONFLICT (factor_id) DO NOTHING""",
    (fid, fam, formula, h, json.dumps(params)))
cur.execute("SELECT count(*) FROM factor_definitions")
print("registry now:", cur.fetchone()[0], "factors")
cx.close()
