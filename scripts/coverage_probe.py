"""Read-only: per-asof raw coverage ratios for sue and vol_12m against the
CURRENT universe. Tests the knife-edge hypothesis: many asofs in the
0.55-0.65 band => small universe shifts legitimately flip factor months."""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn


def dur(a, b):
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def main():
    cx = conn()
    cur = cx.cursor()
    cur.execute("SELECT DISTINCT asof FROM universe_snapshots ORDER BY asof")
    asofs = [str(r[0]) for r in cur.fetchall()]
    cur.execute("SELECT asof, security_id FROM universe_snapshots WHERE in_universe")
    uni = defaultdict(set)
    for a, sec in cur.fetchall():
        uni[str(a)].add(sec)
    cur.execute("SELECT security_id, report_date FROM surprises WHERE sue IS NOT NULL")
    SUE = defaultdict(list)
    for sec, d in cur.fetchall():
        SUE[sec].append(str(d))
    for sec in SUE:
        SUE[sec].sort()
    cur.execute("SELECT security_id, d FROM tr_index_d WHERE d = ANY(%s::date[])", (asofs,))
    G = defaultdict(set)
    for sec, d in cur.fetchall():
        G[sec].add(str(d))

    print("%-12s %8s %8s | %8s %8s" % ("asof", "sue_cov", "sue_pass", "vol_cov", "vol_pass"))
    bands = {"sue": [], "vol": []}
    for ai, a in enumerate(asofs):
        members = uni.get(a, set())
        if not members:
            continue
        n = len(members)
        sue_ok = sum(1 for s in members if SUE.get(s) and
                     any(d <= a and dur(d, a) <= 140 for d in SUE[s][-8:]))
        vol_ok = 0
        if ai >= 12:
            need = asofs[ai - 11:ai + 1]
            vol_ok = sum(1 for s in members if all(d in G.get(s, ()) for d in need))
        sc, vc = sue_ok / n, (vol_ok / n if ai >= 12 else 0.0)
        bands["sue"].append(sc)
        if ai >= 12:
            bands["vol"].append(vc)
        if ai % 12 == 0 or 0.55 <= sc <= 0.65 or (ai >= 12 and 0.55 <= vc <= 0.65):
            print("%-12s %8.3f %8s | %8.3f %8s" % (
                a, sc, "Y" if sc >= 0.6 else ".", vc, "Y" if vc >= 0.6 else "."))
    for k, v in bands.items():
        v = np.array(v)
        knife = int(((v >= 0.55) & (v <= 0.65)).sum())
        print("%s: n=%d median=%.3f min=%.3f max=%.3f | in 0.55-0.65 knife band: %d asofs (%.0f%%)"
              % (k, len(v), np.median(v), v.min(), v.max(), knife, 100 * knife / len(v)))
    cx.close()


if __name__ == "__main__":
    main()
