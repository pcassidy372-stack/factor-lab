"""Phase 2: Fama-MacBeth marginality + redundancy clustering on the
verified board. Monthly cross-sectional OLS of fwd 1m return on all
factor z_sector_size (complete cases); FM = time-series mean of betas,
NW(3) t. Univariate FM per factor for standalone comparison. Redundancy:
avg monthly cross-sectional Spearman |rho| -> hierarchical clusters.
Windows: dev+val (asof <= 2023-07-31, REGISTERED) and full (appendix).
Stores monthly betas in fm_coefficients (R20)."""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

DEVVAL_END = "2023-07-31"


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
    cur.execute("SELECT factor_id FROM factor_definitions ORDER BY factor_id")
    FIDS = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT asof FROM universe_snapshots ORDER BY asof")
    asofs = [str(r[0]) for r in cur.fetchall()]
    nxt = {a: asofs[i + 1] for i, a in enumerate(asofs[:-1])}
    cur.execute("SELECT security_id, d, tr FROM tr_index_d WHERE d = ANY(%s::date[])", (asofs,))
    TR = defaultdict(dict)
    for sec, d, tr in cur.fetchall():
        TR[sec][str(d)] = float(tr)
    cur.execute("""SELECT DISTINCT ON (security_id) security_id, tr
                   FROM tr_index_d ORDER BY security_id, d DESC""")
    TRLAST = {sec: float(tr) for sec, tr in cur.fetchall()}
    cur.execute("SELECT security_id, max(d) FROM tr_index_d GROUP BY 1")
    LAST = {sec: str(d) for sec, d in cur.fetchall()}
    cur.execute("SELECT security_id, delist_date, terminal_return, terminal_method FROM delistings")
    DL = {sec: (str(d), tr, m) for sec, d, tr, m in cur.fetchall()}
    print("loading factor cells...")
    cur.execute("SELECT asof, security_id, factor_id, z_sector_size FROM factor_values")
    FV = defaultdict(dict)
    for a, sec, fid, z in cur.fetchall():
        FV[(str(a), sec)][fid] = float(z)
    print("  cells loaded for %d (asof,sec) pairs" % len(FV))

    def fwd(sec, a):
        b = nxt.get(a)
        t0 = TR.get(sec, {}).get(a)
        if not b or not t0:
            return None
        t1 = TR.get(sec, {}).get(b)
        if t1:
            return t1 / t0 - 1.0
        ld = LAST.get(sec)
        if not ld or ld <= a:
            return None
        r = TRLAST[sec] / t0 - 1.0
        d = DL.get(sec)
        if d and a < d[0] <= b and d[1] is not None and d[2] == "rung1-deal-manual":
            r = (1 + r) * (1 + float(d[1])) - 1
        return r

    by_asof = defaultdict(list)
    for (a, sec), zz in FV.items():
        if len(zz) == len(FIDS):
            r = fwd(sec, a)
            if r is not None:
                by_asof[a].append((sec, [zz[f] for f in FIDS], r))

    multi = defaultdict(dict)
    uni = defaultdict(dict)
    corr_acc = defaultdict(list)
    rows_out = []
    for a in sorted(by_asof):
        obs = by_asof[a]
        if len(obs) < 300:
            continue
        Z = np.array([o[1] for o in obs])
        y = np.array([o[2] for o in obs])
        Zs = (Z - Z.mean(0)) / np.where(Z.std(0) > 1e-9, Z.std(0), 1)
        X = np.column_stack([np.ones(len(y)), Zs])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        for k, f in enumerate(FIDS):
            multi[f][a] = beta[k + 1]
            rows_out.append(("fm-multi", a, f, float(beta[k + 1]), len(y)))
        rk = np.argsort(np.argsort(y))
        for k, f in enumerate(FIDS):
            zf = Zs[:, k]
            uni[f][a] = float(np.corrcoef(np.argsort(np.argsort(zf)), rk)[0, 1])
        R = np.corrcoef(Zs.T)
        corr_acc[a] = R
    execute_values(cur, "INSERT INTO fm_coefficients (run_id, asof, factor_id, beta, n) VALUES %s"
                   " ON CONFLICT DO NOTHING", rows_out, page_size=5000)
    cx.commit()

    def table(name, window):
        print("\n" + "=" * 10, name, "=" * 10)
        print("%-13s %10s %8s | %10s %8s" % ("factor", "FM bp/mo", "NW-t", "uni-IC", "t"))
        for f in FIDS:
            mb = [v for a, v in multi[f].items() if window(a)]
            ub = [v for a, v in uni[f].items() if window(a)]
            if len(mb) < 24:
                print("%-13s insufficient months" % f)
                continue
            print("%-13s %+10.1f %+8.2f | %+10.4f %+8.2f" % (
                f, 1e4 * float(np.mean(mb)), nw_t(mb), float(np.mean(ub)), nw_t(ub)))
        print("months=%d avg n/month=%d" % (
            len([a for a in multi[FIDS[0]] if window(a)]),
            int(np.mean([len(by_asof[a]) for a in by_asof if window(a)]))))

    table("REGISTERED: dev+validation (<= %s)" % DEVVAL_END, lambda a: a <= DEVVAL_END)
    table("APPENDIX: full sample (replication-class)", lambda a: True)

    print("\n" + "=" * 10, "REDUNDANCY (avg |rho|, upper triangle)", "=" * 10)
    Rbar = np.mean([np.abs(corr_acc[a]) for a in corr_acc], axis=0)
    print("      " + " ".join("%6.6s" % f for f in FIDS))
    for i, f in enumerate(FIDS):
        print("%6.6s" % f + " " + " ".join(
            ("%6.2f" % Rbar[i, j]) if j > i else "      " for j in range(len(FIDS))))
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    D = 1 - Rbar
    np.fill_diagonal(D, 0)
    L = linkage(squareform(D, checks=False), method="average")
    for th in (0.5, 0.7):
        labels = fcluster(L, t=th, criterion="distance")
        groups = defaultdict(list)
        for f, g in zip(FIDS, labels):
            groups[g].append(f)
        print("clusters @ distance<%.1f: %s" % (th, sorted(groups.values(), key=len, reverse=True)))
    cx.close()


if __name__ == "__main__":
    main()
