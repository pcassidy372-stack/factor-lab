"""Coverage probe v2 - read-only, self-explaining. Fixes v1's [-8:] bug
(scanned only most-recent events). Adds an AAPL autopsy so a wrong vol
result explains itself instead of just being wrong."""
import bisect
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
    nrows = 0
    for sec, d in cur.fetchall():
        G[sec].add(str(d))
        nrows += 1
    print("grid rows fetched: %d across %d securities (sanity: ~800k+/~9k+)" % (nrows, len(G)))

    cur.execute("""SELECT sm.security_id FROM symbol_map sm
                   WHERE sm.symbol='AAPL' AND sm.valid_to IS NULL""")
    aapl = cur.fetchone()[0]
    probe_asof_i = asofs.index("2015-06-30") if "2015-06-30" in asofs else 60
    need = asofs[probe_asof_i - 11:probe_asof_i + 1]
    have = G.get(aapl, set())
    missing = [d for d in need if d not in have]
    print("AAPL autopsy @ %s: sec=%s grid_cells=%d need=12 missing=%d %s" % (
        asofs[probe_asof_i], aapl, len(have), len(missing), missing[:12]))

    def sue_ok(sec, a):
        ev = SUE.get(sec)
        if not ev:
            return False
        i = bisect.bisect_right(ev, a)
        return i > 0 and dur(ev[i - 1], a) <= 140

    print("%-12s %8s %8s | %8s %8s" % ("asof", "sue_cov", "pass", "vol_cov", "pass"))
    bands = {"sue": [], "vol": []}
    for ai, a in enumerate(asofs):
        members = uni.get(a, set())
        if not members:
            continue
        n = len(members)
        sc = sum(1 for s in members if sue_ok(s, a)) / n
        vc = 0.0
        if ai >= 12:
            need = asofs[ai - 11:ai + 1]
            vc = sum(1 for s in members if all(d in G.get(s, ()) for d in need)) / n
            bands["vol"].append(vc)
        bands["sue"].append(sc)
        if ai % 12 == 0:
            print("%-12s %8.3f %8s | %8.3f %8s" % (
                a, sc, "Y" if sc >= 0.6 else ".", vc, "Y" if vc >= 0.6 else "."))
    for k, v in bands.items():
        v = np.array(v)
        knife = int(((v >= 0.55) & (v <= 0.65)).sum())
        print("%s: n=%d median=%.3f min=%.3f max=%.3f | knife band 0.55-0.65: %d asofs (%.0f%%)"
              % (k, len(v), np.median(v), v.min(), v.max(), knife, 100 * knife / len(v)))
    cx.close()


if __name__ == "__main__":
    main()
