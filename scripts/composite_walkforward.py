"""Phase 4: walk-forward composites (registered-window decisions only).
Sets: A = {mom_12_1, gp_a, sue, net_issuance} (F2: value excluded),
      B = A + {ebit_ev, bp} (F2's OOS trial).
Weighting: equal-weight z_sector_size vs walk-forward IC-weight (expanding
registered-window univariate rank ICs known at t; 36m burn-in; floor 0).
Membership: available-case mean requiring >= 3 members (A) / >= 4 (B).
Output: registered stats printed; holdout computed and MASKED to
artifacts/phase4_holdout_masked.json + composites_ls. R20-able."""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

DEVVAL_END = "2023-07-31"
SET_A = ("mom_12_1", "gp_a", "sue", "net_issuance")
SET_B = SET_A + ("ebit_ev", "bp")
SIGN = {"mom_12_1": 1, "gp_a": 1, "sue": 1, "net_issuance": -1, "ebit_ev": 1, "bp": 1}


def nw_t(x, lag=3):
    x = np.asarray(x, float)
    n = len(x)
    e = x - x.mean()
    var = (e * e).sum() / n
    for j in range(1, lag + 1):
        var += 2 * (1 - j / (lag + 1)) * (e[j:] * e[:-j]).sum() / n
    return x.mean() / np.sqrt(var / n)


def main():
    cx = conn()
    cur = cx.cursor()
    cur.execute("SELECT DISTINCT asof FROM universe_snapshots ORDER BY asof")
    asofs = [str(r[0]) for r in cur.fetchall()]
    from datetime import date as _d
    if asofs and asofs[-1][:7] == _d.today().isoformat()[:7]:
        asofs = asofs[:-1]
    nxt = {a: asofs[i + 1] for i, a in enumerate(asofs[:-1])}
    cur.execute("SELECT security_id, d, tr FROM tr_index_d WHERE d = ANY(%s::date[])", (asofs,))
    TR = defaultdict(dict)
    for sec, d, tr in cur.fetchall():
        TR[sec][str(d)] = float(tr)
    print("loading factor cells...")
    cur.execute("""SELECT asof, security_id, factor_id, z_sector_size FROM factor_values
                   WHERE factor_id = ANY(%s)""", (list(SET_B),))
    Z = defaultdict(dict)
    for a, sec, fid, z in cur.fetchall():
        Z[(str(a), sec)][fid] = float(z) * SIGN[fid]
    cur.execute("""SELECT factor_id, asof, ic FROM factor_ic
                   WHERE horizon_m = 1 AND factor_id = ANY(%s) ORDER BY asof""", (list(SET_B),))
    IC = defaultdict(list)
    for fid, a, ic in cur.fetchall():
        IC[fid].append((str(a), float(ic) * SIGN[fid]))

    def wf_weights(members, a):
        w = {}
        for f in members:
            hist = [ic for d, ic in IC[f] if d < a]
            w[f] = max(np.mean(hist), 0.0) if len(hist) >= 36 else None
        if any(v is None for v in w.values()):
            return {f: 1.0 for f in members}
        s = sum(w.values())
        return {f: (v / s if s > 1e-9 else 1.0 / len(members)) for f, v in w.items()}

    def series(members, min_k, weights_fn):
        out = {}
        for a in asofs:
            if a not in nxt:
                continue
            b = nxt[a]
            wts = weights_fn(members, a)
            rows = []
            for (aa, sec), zz in ():
                pass
            for sec in {s for (aa, s) in Z if aa == a}:
                zz = Z[(a, sec)]
                have = [f for f in members if f in zz]
                if len(have) < min_k:
                    continue
                ws = sum(wts[f] for f in have)
                cz = sum(zz[f] * wts[f] for f in have) / (ws if ws > 1e-9 else 1)
                t0, t1 = TR.get(sec, {}).get(a), TR.get(sec, {}).get(b)
                if t0 and t1:
                    rows.append((cz, t1 / t0 - 1.0))
            if len(rows) < 300:
                continue
            rows.sort(key=lambda x: x[0])
            k = len(rows) // 10
            out[a] = (float(np.mean([r for _, r in rows[-k:]]) -
                            np.mean([r for _, r in rows[:k]])), len(rows))
        return out

    variants = {
        "cmpA_ew": (SET_A, 3, lambda m, a: {f: 1.0 for f in m}),
        "cmpA_icw": (SET_A, 3, wf_weights),
        "cmpB_ew": (SET_B, 4, lambda m, a: {f: 1.0 for f in m}),
        "cmpB_icw": (SET_B, 4, wf_weights),
    }
    masked = {}
    all_rows = []
    print("\n%-10s %8s %8s %8s %8s   %s" % ("variant", "months", "LS ann", "NW-t", "avg n", "worst 3 (registered)"))
    for name, (members, mk, wf) in variants.items():
        s = series(members, mk, wf)
        all_rows += [(name, a, v, n) for a, (v, n) in s.items()]
        reg = {a: v for a, (v, n) in s.items() if a <= DEVVAL_END}
        hold = {a: v for a, (v, n) in s.items() if a > DEVVAL_END}
        rv = np.array(list(reg.values()))
        worst = sorted(reg.items(), key=lambda x: x[1])[:3]
        print("%-10s %8d %+7.2f%% %+8.2f %8d   %s" % (
            name, len(rv), 100 * rv.mean() * 12, nw_t(rv),
            int(np.mean([n for _, n in s.values()])),
            [(a[:7], round(v, 3)) for a, v in worst]))
        hv = np.array(list(hold.values()))
        masked[name] = {"months": len(hv), "ls_ann": float(hv.mean() * 12) if len(hv) else None,
                        "nw_t": float(nw_t(hv)) if len(hv) >= 12 else None}
    cur.execute("DELETE FROM composites_ls")
    execute_values(cur, "INSERT INTO composites_ls (composite_id, asof, ls_ret, n) VALUES %s",
                   all_rows, page_size=5000)
    cx.commit()
    Path("artifacts/phase4_holdout_masked.json").write_text(json.dumps(masked, indent=1))
    print("\nholdout: computed and MASKED (artifacts/phase4_holdout_masked.json; not printed)")
    cx.close()


if __name__ == "__main__":
    main()
